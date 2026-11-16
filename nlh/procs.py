"""The one place the app starts child processes (D-031).

On Windows the packaged app is a windowed program, and Windows gives every console program
it starts (ffmpeg, ffprobe, taskkill, PowerShell) a console window of its own unless told
not to. That window flashes up for every conversion. Every start goes through run() or
popen() here, which add CREATE_NO_WINDOW on Windows. Mac and Linux are unchanged.

tests/test_no_console_guard.py fails if any other module starts a process directly.
"""

from __future__ import annotations

import subprocess
import sys

# Windows: start a console program without creating a console window for it.
CREATE_NO_WINDOW = 0x08000000


def hidden_flags(extra: int = 0) -> int:
    """creationflags for a hidden child: CREATE_NO_WINDOW on Windows, plus any extra flags."""
    if sys.platform == "win32":
        return extra | CREATE_NO_WINDOW
    return extra


def _hide(kwargs: dict) -> dict:
    """Add the hidden-window flag to subprocess keyword arguments (Windows only)."""
    if sys.platform == "win32":
        kwargs["creationflags"] = hidden_flags(kwargs.get("creationflags", 0))
    return kwargs


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with no console window on Windows."""
    return subprocess.run(cmd, **_hide(kwargs))  # noqa: S603


def popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen with no console window on Windows."""
    return subprocess.Popen(cmd, **_hide(kwargs))  # noqa: S603
