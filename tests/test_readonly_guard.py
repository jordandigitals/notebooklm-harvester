"""ARCH-RULES 1: no write call toward NotebookLM anywhere in the app code."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "nlh"

# Library API write methods and CLI write words. There is no future-write stub, so
# nothing is exempt.
FORBIDDEN = [
    r"\.create\(",
    r"\.delete\(",
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
    r"\.retry\(",
    r"\.configure\(",
]
# CLI subcommands that change the account. Only adapter.py runs the CLI, so the word
# check applies there (elsewhere words like "label" are ordinary ledger fields).
CLI_WRITE_WORDS = re.compile(
    r"[\"'](create|delete|generate|source|rename|copy|ask|note|label|share|configure)[\"']"
)


def test_no_write_calls_in_app_code():
    hits = []
    for path in SRC.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pat in FORBIDDEN:
                if re.search(pat, line):
                    hits.append(f"{path.name}:{n}: {line.strip()}")
            if path.name == "adapter.py" and CLI_WRITE_WORDS.search(line):
                hits.append(f"{path.name}:{n}: {line.strip()}")
    assert hits == [], "write call toward NotebookLM found:\n" + "\n".join(hits)


def test_only_adapter_imports_library():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        uses = re.search(r"^\s*(from|import) notebooklm\b", text, re.M) or re.search(
            r"[\"']-m[\"'],\s*[\"']notebooklm[\"']", text
        )
        if path.name != "adapter.py":
            assert not uses, f"{path.name} touches notebooklm directly"


# Structural check (final review): inside the app, calls on the library client and the CLI
# words are allow-listed, so a new write call fails however it is spelled.
ALLOWED_CLIENT_CALLS = {
    "client.notebooks.list",
    "client.artifacts.list_audio",
    "client.artifacts.download_audio",
}
ALLOWED_CLI_WORDS = {"auth", "login"}


def _dotted(node):
    import ast

    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def test_library_calls_are_allow_listed():
    import ast

    problems = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Any reference client.<group>.<method>, called or not (catches aliasing such as
            # f = client.notebooks.delete).
            if isinstance(node, ast.Attribute):
                ref = _dotted(node) or ""
                if ref.startswith("client.") and ref.count(".") == 2 and ref not in ALLOWED_CLIENT_CALLS:
                    problems.append(f"{path.name}:{node.lineno} {ref}")
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func) or ""
            where = f"{path.name}:{node.lineno}"
            if name.startswith("client.") and name not in ALLOWED_CLIENT_CALLS:
                problems.append(f"{where} {name}")
            if name == "getattr" and len(node.args) > 1 and not isinstance(node.args[1], ast.Constant):
                problems.append(f"{where} getattr with a computed name")
            if name.endswith("._cli") and path.name != "adapter.py":
                problems.append(f"{where} {name} outside the adapter")
            if name == "_cli":
                if path.name != "adapter.py":
                    problems.append(f"{where} _cli outside the adapter")
                    continue
                arg = node.args[0] if node.args else None
                if not isinstance(arg, ast.List):
                    problems.append(f"{where} _cli without a literal argument list")
                    continue
                words = [
                    e.value for e in arg.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                command = next((w for w in words if not w.startswith("-")), None)
                if command not in ALLOWED_CLI_WORDS:
                    problems.append(f"{where} _cli command {command!r}")
    assert problems == [], "library call outside the read-only allowlist:\n" + "\n".join(problems)
