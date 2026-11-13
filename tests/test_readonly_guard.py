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
