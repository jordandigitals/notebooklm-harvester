"""The single adapter around notebooklm-py (ARCH-RULES 3).

Every call into the library lives in this module. The rest of the app uses the
types defined here, never the library's own. The adapter is READ-ONLY toward
NotebookLM (ARCH-RULES 1): it lists notebooks, lists audio overviews and downloads
one overview. It also runs the library's local sign-in commands, which only write
the session file on this machine.

Two call paths:
- Read calls use the library's Python API in process, with an explicit session
  file path per account, so accounts never share global state.
- Session calls (auth check, sign-in) run the pinned CLI as a subprocess with
  NOTEBOOKLM_HOME set to the app home. Their output is parsed, never logged raw.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nlh import paths
from nlh.log import get_logger, redact

LIST_SPACING_S = 1.0
BACKOFF_S = (4, 8, 16)
STATUS_NAMES = {
    0: "unknown",
    1: "pending",
    2: "in_progress",
    3: "completed",
    4: "failed",
    5: "suggested",
    6: "pending_review",
}

log = get_logger()


# ---------------------------------------------------------------- app types


@dataclass(frozen=True)
class Notebook:
    """A notebook in the account, as the app sees it."""

    id: str
    title: str
    created_at: str | None


@dataclass(frozen=True)
class AudioOverview:
    """One audio overview artifact in a notebook."""

    artifact_id: str
    title: str
    notebook_id: str
    notebook_title: str
    created_at: str | None
    status: str

    @property
    def completed(self) -> bool:
        """True when NotebookLM reports the overview as finished."""
        return self.status == "completed"


@dataclass(frozen=True)
class SessionStatus:
    """Result of a session check: ok, needs_sign_in or library_error."""

    state: str
    message: str
    email: str | None = None

    @property
    def ok(self) -> bool:
        """True when the session works."""
        return self.state == "ok"


@dataclass(frozen=True)
class ChromeProfile:
    """A Chrome profile on this machine: folder name, display name and email only."""

    folder: str
    name: str
    email: str


class AdapterError(Exception):
    """A library failure, carrying a plain-language message and a state."""

    def __init__(self, message: str, state: str = "library_error"):
        super().__init__(message)
        self.message = message
        self.state = state


# ---------------------------------------------------------------- helpers


def _iso(value: datetime | None) -> str | None:
    """Format a datetime as ISO text, or None."""
    return value.isoformat() if value else None


def _map_error(exc: Exception) -> AdapterError:
    """Translate a library exception into a plain message and a state."""
    from notebooklm import exceptions as ex

    name = type(exc).__name__
    log.warning("library error %s: %s", name, redact(str(exc))[:300])
    if isinstance(exc, (ex.AuthError, ex.AuthExtractionError, ex.HeadlessReauthError)):
        return AdapterError("This account's Google sign-in has expired. Use Sign in again.", "needs_sign_in")
    text = str(exc).lower()
    if "csrf token not found" in text or "login required" in text:
        # A stale copied session: Google serves a page without the sign-in token.
        return AdapterError("This account's Google sign-in has expired. Use Sign in again.", "needs_sign_in")
    if isinstance(exc, ex.RateLimitError):
        return AdapterError("NotebookLM is limiting requests right now. Try again later.")
    if isinstance(exc, ex.NetworkError):
        return AdapterError("Could not reach NotebookLM. Check the internet connection.")
    if isinstance(exc, ex.ArtifactNotReadyError):
        return AdapterError("This audio overview is not finished yet.")
    if isinstance(exc, ex.NotFoundError):
        return AdapterError("NotebookLM could not find that notebook or overview.")
    if isinstance(exc, FileNotFoundError):
        return AdapterError("This account has no saved sign-in. Use Sign in again.", "needs_sign_in")
    return AdapterError(f"NotebookLM library error ({name}). Details are in the log.")


def _is_rate_limit(exc: Exception) -> bool:
    """True if the exception is a rate limit, by type or by its text."""
    from notebooklm import exceptions as ex

    if isinstance(exc, ex.RateLimitError):
        return True
    text = str(exc).lower()
    return "rate limit" in text or "too many requests" in text or "429" in text


async def _with_backoff(make_call: Callable, sleep=asyncio.sleep):
    """Run an async call, backing off exponentially on rate limits (ARCH-RULES 7)."""
    for attempt, delay in enumerate((*BACKOFF_S, None)):
        try:
            return await make_call()
        except Exception as exc:
            if delay is None or not _is_rate_limit(exc):
                raise
            log.info("rate limited, retry %d after %ss", attempt + 1, delay)
            await sleep(delay)


def _client(profile_name: str):
    """Open a library client on one account's session file."""
    from notebooklm import NotebookLMClient

    path = paths.storage_path(profile_name)
    if not path.exists():
        raise FileNotFoundError("no session file")
    return NotebookLMClient.from_storage(path=str(path))


def _run(coro):
    """Run a coroutine to completion and map library errors."""
    try:
        return asyncio.run(coro)
    except AdapterError:
        raise
    except Exception as exc:
        raise _map_error(exc) from None


# ---------------------------------------------------------------- read calls


def list_notebooks(profile_name: str) -> list[Notebook]:
    """List the account's notebooks (read-only)."""

    async def go():
        async with _client(profile_name) as client:
            rows = await _with_backoff(lambda: client.notebooks.list())
            return [Notebook(n.id, n.title or "Untitled notebook", _iso(n.created_at)) for n in rows]

    return _run(go())


def _overview(a, nb: Notebook) -> AudioOverview:
    """Convert a library artifact into an AudioOverview."""
    return AudioOverview(
        artifact_id=a.id,
        title=a.title or nb.title,
        notebook_id=nb.id,
        notebook_title=nb.title,
        created_at=_iso(a.created_at),
        status=STATUS_NAMES.get(int(a.status), "unknown"),
    )


def list_library(
    profile_name: str, on_progress: Callable[[str], None] | None = None
) -> list[tuple[Notebook, list[AudioOverview]]]:
    """List every notebook with its audio overviews in one session, calls spaced 2 s apart."""

    async def go():
        out = []
        async with _client(profile_name) as client:
            rows = await _with_backoff(lambda: client.notebooks.list())
            notebooks = [Notebook(n.id, n.title or "Untitled notebook", _iso(n.created_at)) for n in rows]
            for i, nb in enumerate(notebooks):
                if on_progress:
                    on_progress(f"Reading notebook {i + 1} of {len(notebooks)}")
                await asyncio.sleep(LIST_SPACING_S)
                arts = await _with_backoff(lambda nb_id=nb.id: client.artifacts.list_audio(nb_id))
                out.append((nb, [_overview(a, nb) for a in arts]))
        return out

    return _run(go())


def list_audio_overviews(profile_name: str, notebook: Notebook) -> list[AudioOverview]:
    """List the audio overviews of one notebook (read-only)."""

    async def go():
        async with _client(profile_name) as client:
            arts = await _with_backoff(lambda: client.artifacts.list_audio(notebook.id))
            return [_overview(a, notebook) for a in arts]

    return _run(go())


def download_overview(profile_name: str, overview: AudioOverview, dest: Path) -> Path:
    """Download one existing audio overview to dest. Never regenerates anything."""

    async def go():
        async with _client(profile_name) as client:
            await _with_backoff(
                lambda: client.artifacts.download_audio(overview.notebook_id, str(dest), overview.artifact_id)
            )
        return dest

    started = time.monotonic()
    result = _run(go())
    if not dest.exists() or dest.stat().st_size == 0:
        raise AdapterError("The download finished but no audio arrived.")
    log.info(
        "downloaded artifact %s (%d bytes) in %.1fs",
        overview.artifact_id,
        dest.stat().st_size,
        time.monotonic() - started,
    )
    return result


# ---------------------------------------------------------------- session calls


def _cli_env() -> dict[str, str]:
    """Environment for library CLI subprocesses: the app's own NOTEBOOKLM_HOME."""
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        # The Windows wrapper runs as "python -m nlh.adapter", so the app must be importable.
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(paths.app_dir()), env.get("PYTHONPATH")) if p)
    env["NOTEBOOKLM_HOME"] = str(paths.notebooklm_home())
    env.pop("NOTEBOOKLM_PROFILE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


LIBRARY_CLI_FLAG = "--notebooklm-cli"


def run_library_cli(args: list[str]) -> None:
    """Run the library's own command line in this process.

    Used by the packaged app sub-command and by "python -m nlh.adapter" on Windows.
    On Windows the Chrome cookie fix is applied first (see _windows_chrome_cookie_fix).
    """
    if sys.platform == "win32":
        _windows_chrome_cookie_fix()
    from notebooklm.notebooklm_cli import main as library_main

    sys.argv = ["notebooklm", *args]
    library_main()


def interactive_available() -> bool:
    """True if the fresh browser sign-in can run (needs Playwright, left out of some builds)."""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def _cli(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run the pinned library CLI. Output is captured and never logged raw.

    Packaged app: the executable runs the library itself (LIBRARY_CLI_FLAG). Source run on
    Windows: through this module (python -m nlh.adapter). Both go via run_library_cli,
    which applies the Windows Chrome cookie fix before handing over to the library.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, LIBRARY_CLI_FLAG, *args]
    elif sys.platform == "win32":
        cmd = [sys.executable, "-m", "nlh.adapter", *args]
    else:
        cmd = [sys.executable, "-m", "notebooklm", *args]
    return subprocess.run(
        cmd, env=_cli_env(), capture_output=True, text=True, timeout=timeout, encoding="utf-8"
    )


def check_session(profile_name: str) -> SessionStatus:
    """Check an account's session with the library's auth check (it also refreshes it)."""
    if not paths.storage_path(profile_name).exists():
        return SessionStatus("needs_sign_in", "No saved sign-in for this account.")
    try:
        proc = _cli(["--profile", profile_name, "auth", "check", "--test", "--json"], 120)
    except subprocess.TimeoutExpired:
        return SessionStatus("library_error", "The sign-in check timed out.")
    paths.secure_session_file(profile_name)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        log.warning("auth check for %s gave no JSON (exit %d)", profile_name, proc.returncode)
        return SessionStatus("library_error", "The sign-in check failed. Details are in the log.")
    email = (data.get("account") or {}).get("email")
    if data.get("status") == "ok":
        return SessionStatus("ok", "Signed in.", email)
    log.info("auth check for %s: status %s", profile_name, data.get("status"))
    return SessionStatus("needs_sign_in", "This account needs to sign in again.", email)


def _login_result(proc: subprocess.CompletedProcess, profile_name: str, verify: bool = True) -> SessionStatus:
    """Turn a login subprocess result into a SessionStatus, logging only safe facts.

    verify=False skips the follow-up auth check (a slow CLI run) when the caller is
    about to use the session anyway and will notice if it does not work.
    """
    paths.secure_session_file(profile_name)
    if proc.returncode == 0 and paths.storage_path(profile_name).exists():
        return check_session(profile_name) if verify else SessionStatus("ok", "Signed in.")
    # The library prints its errors to stdout as well as stderr; look at both.
    lines = [
        ln.strip()
        for ln in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
        if ln.strip() and "unseal" not in ln.lower() and "keychain" not in ln.lower()
    ]
    log.warning(
        "login for %s failed (exit %d): %s", profile_name, proc.returncode, redact(" | ".join(lines[-3:]))
    )
    return SessionStatus("needs_sign_in", _login_failure_message(" ".join(lines).lower()))


def _login_failure_message(text: str) -> str:
    """A plain message for the usual reasons a sign-in copy from Chrome fails."""
    if "locked" in text or "being used by another process" in text:
        return (
            "Chrome is still open, so its sign-in could not be read. "
            "Quit Chrome completely (also its icon near the clock), then try again."
        )
    if "dll load failed" in text or "virus" in text:
        return "Windows security blocked the part that reads Chrome's sign-in. See INSTALL.md, Windows notes."
    if "no populated" in text or "was not found" in text:
        return "That Chrome profile has no saved Google sign-in on this computer."
    return "Sign-in did not complete. Details are in the log."


def login_from_chrome(
    profile_name: str, chrome_folder: str, email: str, verify: bool = True
) -> SessionStatus:
    """Save a session from an already signed-in Chrome profile (reads local cookies once)."""
    try:
        proc = _cli(
            [
                "login",
                "--browser-cookies",
                f"chrome::{chrome_folder}",
                "--account",
                email,
                "--profile-name",
                profile_name,
            ],
            180,
        )
    except subprocess.TimeoutExpired:
        return SessionStatus("library_error", "Reading the Chrome sign-in timed out.")
    return _login_result(proc, profile_name, verify)


GOOGLE_SIGNIN_URL = "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fnotebooklm.google.com%2F"
# Google sets these only after a real sign-in. Only cookie NAMES are looked at, never values.
SIGNED_IN_COOKIE_NAMES = frozenset({"SID", "__Secure-1PSID"})


def _google_signin_window(profile_name: str, timeout_s: int) -> bool:
    """Open Chrome on Google's sign-in page and wait until Google has really signed in.

    Why: notebooklm-py 0.8.2 counts any landing on the NotebookLM web address as signed
    in, but a signed-out browser also lands there (Google's public NotebookLM page), so
    a fresh profile was saved with no Google sign-in at all (found in M4, 2026-11-13).
    This window uses the same browser profile folder the library uses for the account,
    so after it closes the library finds a real Google session and saves it.
    Returns True once signed in and on NotebookLM, False on timeout or a closed window.
    """
    from urllib.parse import urlparse

    from playwright.sync_api import sync_playwright

    browser_profile = paths.profile_dir(profile_name) / "browser_profile"
    browser_profile.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile),
            headless=False,
            channel="chrome",
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled", "--password-store=basic"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(GOOGLE_SIGNIN_URL)
            while time.monotonic() < deadline:
                pages = list(ctx.pages)  # one snapshot: tabs can close between reads
                if not pages:
                    return False  # the person closed the window
                names = {c["name"] for c in ctx.cookies("https://accounts.google.com")}
                hosts = {urlparse(pg.url).hostname or "" for pg in pages}
                on_notebooklm = any(h.endswith("notebooklm.google.com") for h in hosts)
                if names & SIGNED_IN_COOKIE_NAMES and on_notebooklm:
                    return True
                time.sleep(1)
            return False
        except Exception as exc:  # window closed mid-wait, or Chrome could not start
            log.info("sign-in window ended: %s", type(exc).__name__)
            return False
        finally:
            try:
                ctx.close()
            except Exception:  # already closed by the person
                pass


def login_interactive(profile_name: str, timeout_s: int = 300) -> SessionStatus:
    """Open Chrome for a fresh Google sign-in and save the session to a new profile."""
    if not _google_signin_window(profile_name, timeout_s):
        return SessionStatus(
            "needs_sign_in", "Google sign-in was not finished. Try again and sign in in the Chrome window."
        )
    try:
        proc = _cli(
            [
                "--profile",
                profile_name,
                "login",
                "--browser",
                "chrome",
                "--browser-timeout",
                "60",
            ],
            120,
        )
    except subprocess.TimeoutExpired:
        return SessionStatus("needs_sign_in", "Sign-in was not finished in time.")
    return _login_result(proc, profile_name)


def chrome_local_state() -> Path | None:
    """Path of Chrome's Local State file on this machine, if Chrome is installed."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        base = Path.home() / ".config" / "google-chrome"
    path = base / "Local State"
    return path if path.exists() else None


def list_chrome_profiles(local_state: Path | None = None) -> list[ChromeProfile]:
    """List Chrome profiles with a signed-in Google account (names and emails only)."""
    path = local_state or chrome_local_state()
    if not path:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cache = (data.get("profile") or {}).get("info_cache") or {}
    out = [
        ChromeProfile(folder, info.get("name") or folder, info.get("user_name") or "")
        for folder, info in cache.items()
    ]
    return sorted((p for p in out if p.email), key=lambda p: p.folder)


# ------------------------------------------------- Windows Chrome cookie fix


def _windows_chrome_cookie_fix() -> None:
    """Point the pinned library at the folder where Windows Chrome keeps its cookies.

    Chrome on Windows stores each profile's cookie database in <profile>/Network/Cookies.
    notebooklm-py 0.8.2 (and 0.8.6, checked 2026-11-13) looks only for <profile>/Cookies,
    so it finds no profile, and its reader cannot locate Chrome's key file from the
    Network folder. This adds the Network/Cookies profiles and reads them with Chrome's
    "Local State" key file. It runs only inside the sign-in subprocess on Windows and
    reads cookies exactly as the library would. Remove it once the library does this.
    """
    from notebooklm.cli import _chromium_profiles as cp

    original_discover = cp.discover_chromium_profiles
    original_read = cp.read_chromium_profile_cookies

    def discover(browser_name):
        found = list(original_discover(browser_name))
        if not cp.is_chromium_browser(browser_name):
            return found
        key = cp._canonical_chromium_browser_name(browser_name)
        root = cp._user_data_dir(key)
        if root is None or not root.is_dir():
            return found
        known = {p.directory_name for p in found}
        names = cp._load_local_state_names(root)
        for entry in sorted(root.iterdir()):
            db = entry / "Network" / "Cookies"
            if entry.name in known or not cp._PROFILE_DIR_RE.match(entry.name) or not db.is_file():
                continue
            found.append(
                cp.ChromiumProfile(
                    browser=key,
                    directory_name=entry.name,
                    human_name=names.get(entry.name, entry.name),
                    cookies_db=db,
                )
            )
        return found

    def read(profile, *, domains):
        db = Path(profile.cookies_db)
        if db.parent.name == "Network":
            import rookie_cookies

            return rookie_cookies.chromium_based(
                str(db.parent.parent.parent / "Local State"), str(db), domains
            )
        return original_read(profile, domains=domains)

    cp.discover_chromium_profiles = discover
    cp.read_chromium_profile_cookies = read


if __name__ == "__main__":
    run_library_cli(sys.argv[1:])
