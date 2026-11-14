"""App home and file locations, the same logic on Mac, Windows and Linux (ARCH-RULES 9)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    """True on Windows. The one switch for Windows-only behaviour (tests flip it)."""
    return sys.platform == "win32"


def app_dir() -> Path:
    """Folder the app runs from: beside the packaged executable, or the source checkout."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # Mac app bundle: <name>.app/Contents/MacOS/<exe>; the app "sits" where the .app is.
        if exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents":
            return exe_dir.parent.parent.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


def portable_home() -> Path | None:
    """The portable app home (R13), only when a "portable" flag file AND a "data" folder sit
    beside the app. Never enabled automatically."""
    base = app_dir()
    if (base / "portable").is_file() and (base / "data").is_dir():
        return base / "data"
    return None


def app_home() -> Path:
    """Return the app home directory, creating it with owner-only permissions.

    Order: NLH_HOME, then portable mode (R13), then the OS default
    (Windows %LOCALAPPDATA%\\NLH, others ~/.nlh). The saved Google sessions live under
    it, so it must never sit inside a git checkout.
    """
    override = os.environ.get("NLH_HOME")
    portable = portable_home()
    if override:
        home = Path(override).expanduser()
    elif portable:
        home = portable
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        home = Path(base) / "NLH"
    else:
        home = Path.home() / ".nlh"
    ensure_private_dir(home)
    return home


def ensure_private_dir(path: Path) -> Path:
    """Create a directory if needed and restrict it to the owner where the OS supports it."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path, 0o700)
    return path


def notebooklm_home() -> Path:
    """Directory passed to the library as NOTEBOOKLM_HOME (holds profiles/<name>/)."""
    return ensure_private_dir(app_home() / "home")


def profile_dir(profile_name: str) -> Path:
    """Folder of one account's library profile."""
    return notebooklm_home() / "profiles" / profile_name


def storage_path(profile_name: str) -> Path:
    """The account's saved session file. Secret: never read into logs or output."""
    return profile_dir(profile_name) / "storage_state.json"


def ledger_path() -> Path:
    """SQLite ledger location."""
    return app_home() / "ledger.db"


def log_dir() -> Path:
    """Folder for the local technical log."""
    return ensure_private_dir(app_home() / "logs")


def temp_dir() -> Path:
    """Scratch folder for downloads in progress (cleaned after each item)."""
    return ensure_private_dir(app_home() / "tmp")


def secure_session_file(profile_name: str) -> None:
    """Set owner-only permissions on an account's session file and folder (Mac and Linux)."""
    if sys.platform == "win32":
        return
    folder = profile_dir(profile_name)
    if folder.exists():
        os.chmod(folder, 0o700)
        for item in folder.iterdir():
            if item.is_file():
                os.chmod(item, 0o600)


def trash_dir() -> Path:
    """Where failed sign-in profile folders are moved instead of being deleted (FINDING-3)."""
    return ensure_private_dir(app_home() / "trash")


class AppLock:
    """Exclusive lock file in the app home, held for the life of the process (FINDING-2).

    Stops two app processes from sharing one app home (a second start opens the running
    page instead). fcntl on Mac and Linux, msvcrt on Windows. The OS drops the lock when
    the process ends, even after a crash.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (app_home() / "app.lock")
        self._fh = None

    def acquire(self) -> bool:
        """Take the lock without waiting. False if another process holds it."""
        fh = open(self.path, "a+b")  # noqa: SIM115 (kept open while the lock is held)
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        """Let go of the lock (normally the process exit does this)."""
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
