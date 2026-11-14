"""ARCH-RULES 1 and D-025: no write call toward NotebookLM anywhere in the app code, except
the two Studio calls (chat ask on the current conversation, generate audio) inside the
adapter's StudioClient zone."""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "nlh"

# Library API write methods and CLI write words.
FORBIDDEN = [
    r"\.create\(",
    r"\.delete",
    r"\.rename\(",
    r"\.generate",
    r"\.add_url\(",
    r"\.add_file\(",
    r"\.add_text\(",
    r"\.add_drive",
    r"\.add_research",
    r"\.share\(",
    r"\.sources\.",
    r"\.notes\.",
    r"\.sharing\.",
    r"\.chat\.",
    r"\.research\.",
    r"\.labels\.",
    r"\.retry",
    r"\.configure\(",
    r"\.revise",
    r"delete_conversation",
]
# CLI subcommands that change the account. Only adapter.py runs the CLI, so the word
# check applies there (elsewhere words like "label" are ordinary ledger fields).
CLI_WRITE_WORDS = re.compile(
    r"[\"'](create|delete|generate|source|rename|copy|ask|note|label|share|configure)[\"']"
)

# D-025: the Studio zone in adapter.py. Only these classes and functions may name the two
# write calls, and only these two.
STUDIO_ZONE = {"_StudioChat", "_StudioArtifacts", "StudioClient", "ask_for_plan", "generate_audio"}
STUDIO_WRITES = {"chat.ask", "artifacts.generate_audio"}
# The Studio page code may call the adapter's two Studio functions (not the library).
STUDIO_CALLERS = {"studio_routes.py", "studio_runner.py"}
STUDIO_ADAPTER_CALLS = {"adapter.ask_for_plan", "adapter.generate_audio"}

ALLOWED_CLIENT_CALLS = {
    "client.notebooks.list",
    "client.artifacts.list_audio",
    "client.artifacts.download_audio",
}
STUDIO_CLIENT_CALLS = {"client.chat.ask", "client.artifacts.generate_audio"}
ALLOWED_CLI_WORDS = {"auth", "login"}


def _zone_lines(tree) -> set[int]:
    """Line numbers inside the Studio zone (adapter.py only)."""
    lines: set[int] = set()
    for node in tree.body:
        if (
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in STUDIO_ZONE
        ):
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def _studio_line_ok(line: str) -> bool:
    """A Studio-zone line may only mention the two allowed writes (or define the function)."""
    probe = line
    for w in STUDIO_WRITES:
        probe = probe.replace(w, "OK")
    probe = re.sub(r"\b(def|async def) generate_audio\b", "def OK", probe)
    probe = probe.replace("self.generate_audio", "self.OK").replace("self._ask", "self.OK")
    return not any(re.search(p, probe) for p in FORBIDDEN)


def regex_hits(name: str, text: str) -> list[str]:
    """Write words found by plain text search, outside the allowed places."""
    zone = _zone_lines(ast.parse(text)) if name == "adapter.py" else set()
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if n in zone:
            if not _studio_line_ok(code):
                hits.append(f"{name}:{n}: {line.strip()}")
            continue
        probe = code
        if name in STUDIO_CALLERS:
            for call in STUDIO_ADAPTER_CALLS:
                probe = probe.replace(call, "adapter.OK")
        if any(re.search(p, probe) for p in FORBIDDEN):
            hits.append(f"{name}:{n}: {line.strip()}")
        if name == "adapter.py" and CLI_WRITE_WORDS.search(code):
            hits.append(f"{name}:{n}: {line.strip()}")
    return hits


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def structural_problems(name: str, text: str) -> list[str]:
    """Calls on the library client and the CLI words, allow-listed however they are spelled."""
    tree = ast.parse(text)
    zone = _zone_lines(tree) if name == "adapter.py" else set()
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            ref = _dotted(node) or ""
            if ref.startswith("client.") and ref.count(".") == 2 and ref not in ALLOWED_CLIENT_CALLS:
                if not (node.lineno in zone and ref in STUDIO_CLIENT_CALLS):
                    problems.append(f"{name}:{node.lineno} {ref}")
            # demo.py may replace the two functions with its fakes (an assignment, never a call).
            replacing = name == "demo.py" and isinstance(node.ctx, ast.Store)
            if ref in STUDIO_ADAPTER_CALLS and name not in STUDIO_CALLERS and not replacing:
                problems.append(f"{name}:{node.lineno} {ref} outside the Studio page code")
        if not isinstance(node, ast.Call):
            continue
        fn = _dotted(node.func) or ""
        where = f"{name}:{node.lineno}"
        if fn == "getattr" and len(node.args) > 1 and not isinstance(node.args[1], ast.Constant):
            problems.append(f"{where} getattr with a computed name")
        if fn.endswith("._cli") and name != "adapter.py":
            problems.append(f"{where} {fn} outside the adapter")
        if fn == "_cli":
            if name != "adapter.py":
                problems.append(f"{where} _cli outside the adapter")
                continue
            arg = node.args[0] if node.args else None
            if not isinstance(arg, ast.List):
                problems.append(f"{where} _cli without a literal argument list")
                continue
            words = [e.value for e in arg.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            command = next((w for w in words if not w.startswith("-")), None)
            if command not in ALLOWED_CLI_WORDS:
                problems.append(f"{where} _cli command {command!r}")
    return problems


def _sources():
    for path in sorted(SRC.rglob("*.py")):
        yield path.name, path.read_text(encoding="utf-8")


def test_no_write_calls_in_app_code():
    hits = [h for name, text in _sources() for h in regex_hits(name, text)]
    assert hits == [], "write call toward NotebookLM found:\n" + "\n".join(hits)


def test_library_calls_are_allow_listed():
    problems = [p for name, text in _sources() for p in structural_problems(name, text)]
    assert problems == [], "library call outside the allowlist:\n" + "\n".join(problems)


def test_only_adapter_imports_library():
    for name, text in _sources():
        uses = re.search(r"^\s*(from|import) notebooklm\b", text, re.M) or re.search(
            r"[\"']-m[\"'],\s*[\"']notebooklm[\"']", text
        )
        if name != "adapter.py":
            assert not uses, f"{name} touches notebooklm directly"


# ---------------------------------------------------------------- the guard itself fails on bad code

BAD_SOURCES = {
    "delete outside the zone": "async def f(client):\n    await client.notebooks.delete('x')\n",
    "generate video outside the zone": (
        "async def f(client):\n    await client.artifacts.generate_video('x')\n"
    ),
    "ask outside the zone": "async def f(client):\n    await client.chat.ask('x', 'q')\n",
    "aliased write": "def f(client):\n    g = client.sources.add_url\n",
}


@pytest.mark.parametrize("label", list(BAD_SOURCES))
def test_guard_catches_a_deliberate_bad_write(label):
    text = BAD_SOURCES[label]
    found = regex_hits("adapter.py", text) + structural_problems("adapter.py", text)
    assert found, f"the guard missed: {label}"


def test_guard_catches_extra_write_inside_the_studio_zone():
    text = (
        "class StudioClient:\n"
        "    def __init__(self, inner):\n"
        "        self.delete = inner.notebooks.delete\n"
        "async def generate_audio(client):\n"
        "    await client.artifacts.generate_video('x')\n"
        "    await client.chat.delete_conversation('x', 'y')\n"
    )
    found = regex_hits("adapter.py", text) + structural_problems("adapter.py", text)
    assert len(found) >= 3


def test_studio_calls_only_from_studio_page_code():
    text = "from nlh import adapter\nadapter.generate_audio('p', 'n', 'f')\n"
    assert structural_problems("harvest.py", text)
    assert not structural_problems("studio_routes.py", text)


# ---------------------------------------------------------------- the proxies' surfaces


class _Everything:
    """A fake library client where every attribute exists and records calls."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        sub = _Everything()
        sub.calls = self.calls

        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        if name in ("chat", "artifacts", "notebooks", "sources", "notes", "sharing"):
            return sub
        return method


def _public(obj) -> set[str]:
    return {k for k in vars(obj) if not k.startswith("_")}


def test_studio_client_exposes_exactly_two_writes():
    from nlh.adapter import StudioClient

    sc = StudioClient(_Everything())
    assert _public(sc) == {"chat", "artifacts"}
    assert _public(sc.artifacts) == {"generate_audio"}
    assert _public(sc.chat) == set()  # ask is a method on the class, not a copied attribute
    assert [m for m in vars(type(sc.chat)) if not m.startswith("_")] == ["ask"]
    for bad in ("delete_conversation", "ask_new", "get_history"):
        with pytest.raises(AttributeError):
            getattr(sc.chat, bad)
    for bad in ("generate_video", "delete", "rename", "retry_failed", "revise_slide", "list_audio"):
        with pytest.raises(AttributeError):
            getattr(sc.artifacts, bad)
    for bad in ("notebooks", "sources", "notes", "sharing", "research"):
        with pytest.raises(AttributeError):
            getattr(sc, bad)


def test_studio_ask_never_passes_a_conversation():
    import asyncio

    from nlh.adapter import StudioClient

    inner = _Everything()
    asyncio.run(StudioClient(inner).chat.ask("nb", "question"))
    assert inner.calls == [("ask", ("nb", "question"), {})]


def test_read_only_client_is_unchanged():
    from nlh.adapter import ReadOnlyClient

    rc = ReadOnlyClient(_Everything())
    assert _public(rc) == {"notebooks", "artifacts"}
    assert _public(rc.notebooks) == {"list"}
    assert _public(rc.artifacts) == {"list_audio", "download_audio"}
    with pytest.raises(AttributeError):
        rc.chat  # noqa: B018
