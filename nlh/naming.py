"""File naming per D-013: <overview title>.mp3, safe on Windows and Mac, never clashing.

On Windows the overview title is shortened so the full saved path stays under 240
characters (D-023). Mac and Linux names are unchanged.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from nlh import paths

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = (
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)
MAX_STEM = 150
MAX_FOLDER = 80

# D-023, Windows only. A Windows PC without long-path support refuses paths of 260
# characters or more. The full saved path stays under WIN_MAX_PATH, counting the ".mp3"
# and room for a " (999)" clash suffix; the ".part" file during saving adds 5 more.
WIN_MAX_PATH = 240
WIN_MIN_STEM = 40
SUFFIX_ROOM = len(" (999)")  # FINDING-6: room for up to 999 same-named files
TOO_LONG_MESSAGE = "This folder path is too long for Windows. Pick a shorter output folder."
LONG_FOLDER_WARNING_CHARS = 150  # the page warns about output folders longer than this (Windows)


class PathTooLong(Exception):
    """The output folder is too deep for even a WIN_MIN_STEM title on Windows."""

    def __init__(self) -> None:
        super().__init__(TOO_LONG_MESSAGE)
        self.message = TOO_LONG_MESSAGE


def _windows() -> bool:
    """True when the Windows path cap applies."""
    return paths.is_windows()


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


def folder_name(notebook_title: str) -> str:
    """Name of a notebook's subfolder (D-019): same rules as file names, max MAX_FOLDER."""
    return safe_stem(notebook_title)[:MAX_FOLDER].rstrip(". ") or "Notebook"


def _u16(text: str) -> int:
    """Length in UTF-16 code units, the unit Windows' path limit counts (emoji count 2)."""
    return len(text.encode("utf-16-le")) // 2


def stem_room(folder: Path, ext: str = ".mp3") -> int | None:
    """Characters left for the file stem in folder under the Windows cap; None off Windows."""
    if not _windows():
        return None
    # len(folder) + separator + stem + suffix room + ext must stay under WIN_MAX_PATH.
    return WIN_MAX_PATH - 1 - _u16(os.path.abspath(folder)) - 1 - SUFFIX_ROOM - _u16(ext)


def check_fits(out_dir: Path, notebook_title: str, ext: str = ".mp3") -> None:
    """Raise PathTooLong if a WIN_MIN_STEM title cannot fit (Windows only). Writes nothing."""
    room = stem_room(Path(out_dir) / folder_name(notebook_title), ext)
    if room is not None and room < WIN_MIN_STEM:
        raise PathTooLong()


def fit_stem(stem: str, room: int | None) -> str:
    """Shorten a stem to room characters (Windows cap), never below WIN_MIN_STEM or empty."""
    if room is None or _u16(stem) <= room:
        return stem
    if room < WIN_MIN_STEM:
        raise PathTooLong()
    cut, used = [], 0
    for ch in stem:  # whole characters only, never half of an emoji
        used += _u16(ch)
        if used > room:
            break
        cut.append(ch)
    short = "".join(cut)
    return short.rstrip(". ") or short


def notebook_folder(out_dir: Path, notebook_title: str) -> Path:
    """Subfolder for a notebook's overviews (D-019): <output>/<notebook title>/.

    Created if missing; an existing folder is reused. On Windows it is refused (before
    anything is created) when even a WIN_MIN_STEM title would not fit inside it.
    """
    check_fits(out_dir, notebook_title)
    folder = Path(out_dir) / folder_name(notebook_title)
    folder.mkdir(exist_ok=True)
    return folder


def unique_path(folder: Path, title: str, ext: str = ".mp3") -> Path:
    """Pick a name in folder that clashes with nothing, case-insensitively.

    Adds " (2)", " (3)" and so on. Case-only matches count as clashes on every
    platform (Windows and the default Mac file system ignore case). Pending .part
    files count too, so two items never aim at the same name. On Windows the stem is
    shortened so the full path stays under WIN_MAX_PATH (D-023).
    """
    stem = fit_stem(safe_stem(title), stem_room(folder, ext))
    taken = {p.name.lower() for p in folder.iterdir()} if folder.exists() else set()

    def free(name: str) -> bool:
        return name.lower() not in taken and f"{name}.part".lower() not in taken

    candidate = f"{stem}{ext}"
    n = 2
    while not free(candidate):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return folder / candidate
