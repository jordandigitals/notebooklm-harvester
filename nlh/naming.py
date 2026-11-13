"""File naming per D-013: <overview title>.mp3, safe on Windows and Mac, never clashing."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = (
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)
MAX_STEM = 150


def safe_stem(title: str) -> str:
    """Turn an overview title into a file stem legal on Windows and Mac."""
    text = unicodedata.normalize("NFC", title or "")
    text = _ILLEGAL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    if not text:
        text = "Audio Overview"
    if text.split(".")[0].upper() in _RESERVED:
        text = f"{text} (file)"
    return text[:MAX_STEM].rstrip(". ")


def unique_path(folder: Path, title: str, ext: str = ".mp3") -> Path:
    """Pick a name in folder that clashes with nothing, case-insensitively.

    Adds " (2)", " (3)" and so on. Case-only matches count as clashes on every
    platform (Windows and the default Mac file system ignore case). Pending .part
    files count too, so two items never aim at the same name.
    """
    stem = safe_stem(title)
    taken = {p.name.lower() for p in folder.iterdir()} if folder.exists() else set()

    def free(name: str) -> bool:
        return name.lower() not in taken and f"{name}.part".lower() not in taken

    candidate = f"{stem}{ext}"
    n = 2
    while not free(candidate):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return folder / candidate
