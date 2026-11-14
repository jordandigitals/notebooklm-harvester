"""The single adapter around notebooklm-py (ARCH-RULES 3).

Every call into the library lives in this module. The rest of the app uses the
types defined here, never the library's own. The adapter is READ-ONLY toward
NotebookLM (ARCH-RULES 1): it lists notebooks, lists audio overviews and downloads
one overview. It also runs the library's local sign-in commands, which only write
the session file on this machine. The one exception is the Studio stage (D-025):
StudioClient may ask a notebook a question on its current conversation and generate
an audio overview, both only after an explicit click.

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


class _ReadOnlyNotebooks:
    """Only notebooks.list (FINDING-5)."""

    def __init__(self, inner):
        self.list = inner.notebooks.list

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: the harvester is read-only toward NotebookLM")


class _ReadOnlyArtifacts:
    """Only artifacts.list_audio and artifacts.download_audio (FINDING-5)."""

    def __init__(self, inner):
        self.list_audio = inner.artifacts.list_audio
        self.download_audio = inner.artifacts.download_audio

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: the harvester is read-only toward NotebookLM")


class ReadOnlyClient:
    """Wraps a library client so only the three read calls exist (ARCH-RULES 1, FINDING-5).

    notebooks.list, artifacts.list_audio and artifacts.download_audio are copied across as
    bound methods; anything else, however it is reached, raises AttributeError. The AST
    guard test stays as a second line.
    """

    def __init__(self, inner):
        self.notebooks = _ReadOnlyNotebooks(inner)
        self.artifacts = _ReadOnlyArtifacts(inner)

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: the harvester is read-only toward NotebookLM")


class _ReadOnlyContext:
    """Async context manager that opens the library client and yields the read-only wrapper."""

    def __init__(self, ctx):
        self._ctx = ctx

    async def __aenter__(self) -> ReadOnlyClient:
        return ReadOnlyClient(await self._ctx.__aenter__())

    async def __aexit__(self, *exc):
        return await self._ctx.__aexit__(*exc)


def _client(profile_name: str) -> _ReadOnlyContext:
    """Open a read-only library client on one account's session file."""
    from notebooklm import NotebookLMClient

    path = paths.storage_path(profile_name)
    if not path.exists():
        raise FileNotFoundError("no session file")
    return _ReadOnlyContext(NotebookLMClient.from_storage(path=str(path)))


# ---------------------------------------------------------------- Studio (D-025)


class _StudioChat:
    """Only chat.ask, and only on the notebook's current conversation (D-025 condition 2)."""

    def __init__(self, inner):
        self._ask = inner.chat.ask

    async def ask(self, notebook_id: str, question: str):
        # No conversation_id and no source_ids are passed through: the question goes to the
        # notebook's current conversation over all sources. Nothing here can delete or reset a
        # conversation ("ask --new" does that and is not reachable).
        return await self._ask(notebook_id, question)

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: Studio may only ask and generate audio")


class _StudioArtifacts:
    """Only artifacts.generate_audio (D-025)."""

    def __init__(self, inner):
        self.generate_audio = inner.artifacts.generate_audio

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: Studio may only ask and generate audio")


class StudioClient:
    """The only write path toward NotebookLM (D-025 condition 7).

    Exposes exactly chat.ask (current conversation, never new) and artifacts.generate_audio.
    Anything else raises AttributeError. Completion is checked through ReadOnlyClient
    (artifacts.list_audio), so this proxy needs nothing more. The guard test checks both
    the surface of this class and that no write call exists anywhere else.
    """

    def __init__(self, inner):
        self.chat = _StudioChat(inner)
        self.artifacts = _StudioArtifacts(inner)

    def __getattr__(self, name):
        raise AttributeError(f"{name} is not allowed: Studio may only ask and generate audio")


class _StudioContext:
    """Async context manager that opens the library client and yields the Studio wrapper."""

    def __init__(self, ctx):
        self._ctx = ctx

    async def __aenter__(self) -> StudioClient:
        return StudioClient(await self._ctx.__aenter__())

    async def __aexit__(self, *exc):
        return await self._ctx.__aexit__(*exc)


def _studio_client(profile_name: str) -> _StudioContext:
    """Open a Studio client on one account's session file."""
    from notebooklm import NotebookLMClient

    path = paths.storage_path(profile_name)
    if not path.exists():
        raise FileNotFoundError("no session file")
    return _StudioContext(NotebookLMClient.from_storage(path=str(path)))


class _QuietLibrary:
    """Silence the library's logger during Studio calls: on a failed ask it logs the full
    answer text, and plan text must never reach a log or the console (brief, review R2)."""

    def __enter__(self):
        import logging

        self.lg = logging.getLogger("notebooklm")
        self.level, self.propagate = self.lg.level, self.lg.propagate
        self.null = logging.NullHandler()
        self.lg.addHandler(self.null)
        self.lg.setLevel(logging.CRITICAL + 1)
        self.lg.propagate = False
        return self

    def __exit__(self, *exc):
        self.lg.removeHandler(self.null)
        self.lg.setLevel(self.level)
        self.lg.propagate = self.propagate


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


# ---------------------------------------------------------------- Studio calls (D-025)

AUDIO_FORMATS = ("deep_dive", "brief", "critique", "debate")
AUDIO_LENGTHS = ("short", "default", "long")


@dataclass(frozen=True)
class PlanAnswer:
    """The text of one plan answer. Never logged (only its length is)."""

    text: str


@dataclass(frozen=True)
class Submitted:
    """A generation Google accepted: the task id is also the future artifact id."""

    task_id: str
    status: str


def _studio_error(exc: Exception) -> AdapterError:
    """Map a Studio failure. A rate or quota refusal becomes state "limit": the caller stops
    for the day (D-025 condition 3). Only the exception type is logged."""
    log.warning("studio call failed: %s", type(exc).__name__)
    if isinstance(exc, AdapterError):
        return exc
    if _is_rate_limit(exc) or "quota" in str(exc).lower() or "user_displayable" in str(exc).lower():
        return AdapterError(
            "NotebookLM refused because of a usage limit. Studio stops for this account today.", "limit"
        )
    from notebooklm import exceptions as ex

    # Mapped by type only; the exception text is not logged (it could quote the notebook).
    if isinstance(exc, (ex.AuthError, ex.AuthExtractionError, ex.HeadlessReauthError, FileNotFoundError)):
        return AdapterError("This account's Google sign-in has expired. Use Sign in again.", "needs_sign_in")
    if isinstance(exc, ex.NetworkError):
        return AdapterError("Could not reach NotebookLM. Check the internet connection.")
    if isinstance(exc, ex.NotFoundError):
        return AdapterError("NotebookLM could not find that notebook.")
    return AdapterError(f"NotebookLM library error ({type(exc).__name__}).")


def ask_for_plan(profile_name: str, notebook_id: str, question: str) -> PlanAnswer:
    """Ask a notebook one question on its current conversation (write action, D-025).

    Called only from an explicit click. No retry: a retry could post the question twice.
    """

    async def go():
        async with _studio_client(profile_name) as client:
            result = await client.chat.ask(notebook_id, question)
            return PlanAnswer(str(getattr(result, "answer", "") or ""))

    started = time.monotonic()
    with _QuietLibrary():
        try:
            answer = asyncio.run(go())
        except Exception as exc:
            raise _studio_error(exc) from None
    log.info(
        "studio ask on notebook %s: %d characters in %.1fs",
        notebook_id,
        len(answer.text),
        time.monotonic() - started,
    )
    return answer


def generate_audio(
    profile_name: str,
    notebook_id: str,
    focus: str,
    audio_format: str = "deep_dive",
    audio_length: str = "default",
    language: str = "en",
) -> Submitted:
    """Ask NotebookLM to generate one audio overview (write action, D-025).

    Called only by the Studio runner after Jordan's click. Never retried: a refusal stops the
    account for the day, and a network failure leaves the item for Jordan to resume or drop.
    """
    if audio_format not in AUDIO_FORMATS or audio_length not in AUDIO_LENGTHS:
        raise AdapterError("Unknown overview format or length.")
    from notebooklm import AudioFormat, AudioLength

    fmt = AudioFormat[audio_format.upper()]
    length = AudioLength[audio_length.upper()]

    async def go():
        async with _studio_client(profile_name) as client:
            status = await client.artifacts.generate_audio(
                notebook_id,
                language=language or "en",
                instructions=focus or None,
                audio_format=fmt,
                audio_length=length,
            )
            return Submitted(str(status.task_id), str(status.status))

    with _QuietLibrary():
        try:
            sub = asyncio.run(go())
        except Exception as exc:
            raise _studio_error(exc) from None
    if not sub.task_id or sub.status == "failed":
        raise AdapterError("NotebookLM did not accept the request.")
    log.info("studio generation accepted on notebook %s: task %s", notebook_id, sub.task_id)
    return sub


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


# Read-only toward NotebookLM (ARCH-RULES 1): the only library commands the app runs.
ALLOWED_CLI_COMMANDS = frozenset({"auth", "login"})
_CLI_OPTIONS_WITH_VALUE = frozenset({"--profile", "-p", "--storage", "--notebook", "-n"})


def _cli_command(args: list[str]) -> str | None:
    """The library sub-command in args, skipping global options and their values."""
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in _CLI_OPTIONS_WITH_VALUE:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def run_library_cli(args: list[str]) -> None:
    """Run the library's own command line in this process.

    Used by the packaged app sub-command and by "python -m nlh.adapter" on Windows.
    On Windows the Chrome cookie fix is applied first (see _windows_chrome_cookie_fix).
    """
    command = _cli_command(args)
    if command not in ALLOWED_CLI_COMMANDS:
        print(
            f"Refused: this app runs only {sorted(ALLOWED_CLI_COMMANDS)}, not {command!r}.", file=sys.stderr
        )
        raise SystemExit(2)
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
    return _run_group(cmd, _cli_env(), timeout)


def _run_group(cmd: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    """Run a command in its own process group and kill the whole group on timeout (FINDING-4).

    A sign-in can start a browser as a grandchild; killing only the direct child would leave
    that browser running and holding the profile lock.
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill a process and everything it started."""
    import signal

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()
    log.info("timed-out sign-in process group stopped")


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


GOOGLE_SIGNIN_BASE = "https://accounts.google.com/ServiceLogin?continue="
# Google sets these only after a real sign-in. Only cookie NAMES are looked at, never values.
SIGNED_IN_COOKIE_NAMES = frozenset({"SID", "__Secure-1PSID"})


def notebooklm_url() -> str:
    """NotebookLM's address as the pinned library sees it (it follows Google's moves and
    NOTEBOOKLM_BASE_URL), so the app never hard-codes an old host."""
    from notebooklm.config import get_base_url

    return get_base_url().rstrip("/") + "/"


def google_signin_url() -> str:
    """Google's sign-in page, continuing to NotebookLM."""
    from urllib.parse import quote

    return GOOGLE_SIGNIN_BASE + quote(notebooklm_url(), safe="")


NUDGE_AFTER_S = 3  # grace after Google's sign-in cookie appears before taking the window to NotebookLM
NUDGE_EVERY_S = 15


def _google_signin_window(profile_name: str, timeout_s: int) -> bool:
    """Open Chrome on Google's sign-in page and wait until Google has really signed in.

    Why: notebooklm-py 0.8.2 counts any landing on the NotebookLM web address as signed
    in, but a signed-out browser also lands there (Google's public NotebookLM page), so
    a fresh profile was saved with no Google sign-in at all (found in M4, 2026-11-13).
    This window uses the same browser profile folder the library uses for the account,
    so after it closes the library finds a real Google session and saves it.
    Google sometimes stops after the sign-in on an extra page (security check-up, account
    chooser) instead of continuing to NotebookLM. Once its sign-in cookie exists the
    sign-in is done, so the window is taken to NotebookLM by the app itself.
    Returns True once signed in and on NotebookLM, False on timeout or a closed window.
    Only the site name of the last page and whether Google had signed in are logged.
    """
    from urllib.parse import urlparse

    # The library's own list of NotebookLM addresses: Google also serves it as
    # notebook.google.com ("Gemini Notebook"), so a fixed host check missed the landing.
    from notebooklm._auth.browser_capture import url_matches_base_host
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
        last_site, signed_since, nudged_at = "", None, 0.0

        def sid_marks() -> set:
            """Name and expiry of Google's sign-in cookies (never values)."""
            return {
                (c["name"], c.get("expires"))
                for c in ctx.cookies("https://accounts.google.com")
                if c["name"] in SIGNED_IN_COOKIE_NAMES
            }

        try:
            at_launch = sid_marks()  # cookies left from an earlier sign-in may be stale
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(google_signin_url())
            while time.monotonic() < deadline:
                pages = list(ctx.pages)  # one snapshot: tabs can close between reads
                if not pages:
                    log.info(
                        "sign-in window closed (last site %s, signed in %s)", last_site, bool(signed_since)
                    )
                    return False
                last_site = urlparse(pages[-1].url).hostname or ""
                marks = sid_marks()
                if marks:
                    if any(url_matches_base_host(pg.url) for pg in pages):
                        return True  # Google sent the window to NotebookLM itself
                    if marks == at_launch:
                        time.sleep(1)  # only old cookies: wait for the person to sign in
                        continue
                    signed_since = signed_since or time.monotonic()
                    now_s = time.monotonic()
                    if now_s - signed_since >= NUDGE_AFTER_S and now_s - nudged_at >= NUDGE_EVERY_S:
                        nudged_at = now_s
                        log.info("signed in but stopped on %s; opening NotebookLM", last_site)
                        try:
                            pages[-1].goto(notebooklm_url(), wait_until="domcontentloaded", timeout=30000)
                        except Exception as exc:  # a slow page; the next round checks again
                            log.info("opening NotebookLM: %s", type(exc).__name__)
                time.sleep(1)
            log.info("sign-in window timed out (last site %s, signed in %s)", last_site, bool(signed_since))
            return False
        except Exception as exc:  # window closed mid-wait, or Chrome could not start
            log.info(
                "sign-in window ended: %s (last site %s, signed in %s)",
                type(exc).__name__,
                last_site,
                bool(signed_since),
            )
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
