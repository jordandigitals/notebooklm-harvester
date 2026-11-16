"""D-031: the app never opens a visible console window on Windows.

Every child process starts through nlh.procs, which adds CREATE_NO_WINDOW on Windows.
The AST check below fails if any other module starts a process directly, so a new call
cannot forget the flag.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from nlh import procs

SRC = Path(__file__).resolve().parent.parent / "nlh"

# Calls that start a process. os.startfile is allowed: it asks Explorer to show a folder
# (a window the person asked for), it does not start a console program.
STARTERS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execvp"),
    ("os", "spawnv"),
    ("os", "spawnl"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}
STARTER_NAMES = {name for _, name in STARTERS}


def _direct_starts(path: Path) -> list[str]:
    """Process starts in a module that do not go through nlh.procs."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os", "asyncio"):
            for alias in node.names:
                if alias.name in STARTER_NAMES:
                    found.append(f"{path.name}:{node.lineno} from {node.module} import {alias.name}")
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        named = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
        if named and (f.value.id, f.attr) in STARTERS:
            found.append(f"{path.name}:{node.lineno} {f.value.id}.{f.attr}")
    return found


def test_every_process_start_goes_through_procs():
    """Only nlh/procs.py may call subprocess directly."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "procs.py":
            continue
        offenders += _direct_starts(path)
    assert offenders == [], "start these through nlh.procs.run/popen (D-031): " + ", ".join(offenders)


def test_guard_catches_a_direct_start(tmp_path):
    """The guard itself notices subprocess.Popen, run and a from-import."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import subprocess\nfrom subprocess import check_output\n"
        "subprocess.Popen(['x'])\nsubprocess.run(['y'])\n",
        encoding="utf-8",
    )
    found = _direct_starts(sample)
    assert len(found) == 3


@pytest.mark.parametrize("starter", ["run", "popen"])
def test_windows_start_is_hidden(monkeypatch, starter):
    """On Windows the hidden-window flag is set, and other flags are kept."""
    seen = {}
    monkeypatch.setattr(procs.sys, "platform", "win32")
    target = "run" if starter == "run" else "Popen"
    monkeypatch.setattr(procs.subprocess, target, lambda cmd, **k: seen.update(k))
    getattr(procs, starter)(["ffmpeg"], creationflags=0x200)
    assert seen["creationflags"] & procs.CREATE_NO_WINDOW
    assert seen["creationflags"] & 0x200  # CREATE_NEW_PROCESS_GROUP kept for the sign-in group kill


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_mac_and_linux_unchanged(monkeypatch, platform):
    """Mac and Linux get exactly the arguments they always did."""
    seen = {}
    monkeypatch.setattr(procs.sys, "platform", platform)
    monkeypatch.setattr(procs.subprocess, "run", lambda cmd, **k: seen.update(k))
    procs.run(["ffmpeg"], check=False)
    assert seen == {"check": False}
    assert procs.hidden_flags() == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_real_console_child_gets_no_console():
    """A real console program started hidden has no console window of its own."""
    code = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
    cmd = [sys.executable, "-c", code]
    out = procs.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert out.returncode == 0
    assert out.stdout.strip() == "0"
