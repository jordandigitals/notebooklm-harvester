"""Account management (R2): add, check, sign in again, label, output folder.

Sessions live in the library profile folders under the app home. Only one sign-in
runs at a time, and nothing retries in a loop (ARCH-RULES 6, 7).
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from nlh import adapter, paths
from nlh.adapter import AdapterError, ChromeProfile, SessionStatus
from nlh.ledger import Account, Ledger, now
from nlh.log import get_logger

log = get_logger()

SIGNIN_LOCK = threading.Lock()


class AccountError(Exception):
    """A plain-language problem with an account action."""


def _profile_name(ledger: Ledger, email: str | None) -> str:
    """Make a new, unused library profile name from an email (or a counter)."""
    base = re.sub(r"[^a-z0-9]+", "-", (email or "").split("@")[0].lower()).strip("-") or "account"
    name, n = f"acct-{base}", 2
    while ledger.account_by_profile(name) or paths.profile_dir(name).exists():
        name, n = f"acct-{base}-{n}", n + 1
    return name


def _signin(fn, *args) -> SessionStatus:
    """Run one sign-in, refusing if another is already running."""
    if not SIGNIN_LOCK.acquire(blocking=False):
        raise AccountError("Another sign-in is already running. Finish it first.")
    try:
        return fn(*args)
    finally:
        SIGNIN_LOCK.release()


def _trash_profile(name: str) -> Path | None:
    """Move a failed or refused add's profile folder to <home>/trash (FINDING-3, never deleted).

    The folder may hold a half-finished Google sign-in; it must not linger under profiles/.
    """
    src = paths.profile_dir(name)
    if not src.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = paths.trash_dir() / f"{name}-{stamp}"
    n = 2
    while dest.exists():
        dest = paths.trash_dir() / f"{name}-{stamp}-{n}"
        n += 1
    shutil.move(str(src), str(dest))
    log.info("moved profile %s to trash", name)
    return dest


def trash_entries() -> list[dict]:
    """What is in the trash (folder names and sizes only)."""
    out = []
    for item in sorted(paths.trash_dir().iterdir()):
        size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) if item.is_dir() else 0
        out.append({"name": item.name, "size_bytes": size})
    return out


def clear_trash() -> int:
    """Remove the trash folders (asked first in the page). App data only, never user files."""
    count = 0
    for item in paths.trash_dir().iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
        count += 1
    log.info("cleared %d trash entries", count)
    return count


def chrome_candidates(ledger: Ledger) -> list[tuple[ChromeProfile, bool]]:
    """Chrome profiles on this machine, each with whether its email is already an account."""
    known = {(a.email or "").lower() for a in ledger.accounts()}
    return [(p, p.email.lower() in known) for p in adapter.list_chrome_profiles()]


def add_from_chrome(ledger: Ledger, folder: str) -> tuple[Account, SessionStatus]:
    """Add an account from a signed-in Chrome profile (reads its cookies once, locally)."""
    match = [p for p in adapter.list_chrome_profiles() if p.folder == folder]
    if not match:
        raise AccountError("That Chrome profile was not found on this computer.")
    prof = match[0]
    existing = [a for a in ledger.accounts() if (a.email or "").lower() == prof.email.lower()]
    if existing:
        raise AccountError(f"{prof.email} is already added as '{existing[0].label}'.")
    name = _profile_name(ledger, prof.email)
    try:
        status = _signin(adapter.login_from_chrome, name, prof.folder, prof.email)
    except AccountError:
        raise  # another sign-in is running; this one never started
    except Exception:
        _trash_profile(name)
        raise
    if not status.ok:
        _trash_profile(name)
        raise AccountError(status.message)
    acct = ledger.add_account(name, prof.name, prof.email, prof.folder)
    ledger.update_account(acct.id, last_status=status.state, last_checked=now())
    log.info("account added from Chrome %s as %s", prof.folder, name)
    return ledger.account(acct.id), status


def add_interactive(ledger: Ledger, label: str | None = None) -> tuple[Account, SessionStatus]:
    """Add an account through a fresh Google sign-in in Chrome."""
    name = _profile_name(ledger, None)
    try:
        status = _signin(adapter.login_interactive, name)
    except AccountError:
        raise  # another sign-in is running; this one never started
    except Exception:
        _trash_profile(name)
        raise
    if not status.ok:
        _trash_profile(name)
        raise AccountError(status.message)
    if status.email and any((a.email or "").lower() == status.email.lower() for a in ledger.accounts()):
        _trash_profile(name)
        raise AccountError(f"{status.email} is already added.")
    acct = ledger.add_account(name, label or status.email or name, status.email, None)
    ledger.update_account(acct.id, last_status=status.state, last_checked=now())
    log.info("account added by sign-in as %s", name)
    return ledger.account(acct.id), status


def check(ledger: Ledger, acct: Account, *, reread_chrome: bool = True) -> SessionStatus:
    """Check an account's session.

    A session copied from Chrome goes stale when Chrome rotates its own cookies.
    For such accounts one automatic re-read from Chrome is tried; never more.
    """
    status = adapter.check_session(acct.profile_name)
    if not status.ok and status.state == "needs_sign_in" and not uses_own_signin(acct) and reread_chrome:
        log.info("session for %s stale, re-reading Chrome %s once", acct.profile_name, acct.chrome_profile)
        try:
            status = _signin(adapter.login_from_chrome, acct.profile_name, acct.chrome_profile, acct.email)
        except AccountError as exc:
            status = SessionStatus("needs_sign_in", str(exc))
    ledger.update_account(acct.id, last_status=status.state, last_checked=now())
    return status


def uses_own_signin(acct: Account) -> bool:
    """True when Sign in again opens the app's own Chrome window for this account.

    Always for accounts signed in directly. On Windows also for accounts copied from
    Chrome (D-022): re-reading Chrome there needs Chrome fully closed every time, and the
    copy goes stale while Chrome runs, so the account moves to its own sign-in instead.
    """
    if not acct.chrome_profile:
        return True
    return paths.is_windows() and adapter.interactive_available()


def sign_in_again(ledger: Ledger, acct: Account) -> SessionStatus:
    """Renew a session: own sign-in window (see uses_own_signin), else re-read Chrome."""
    if not uses_own_signin(acct):
        status = _signin(adapter.login_from_chrome, acct.profile_name, acct.chrome_profile, acct.email)
        ledger.update_account(acct.id, last_status=status.state, last_checked=now())
        return status
    status = _signin(adapter.login_interactive, acct.profile_name)
    if status.ok and acct.email and status.email and status.email.lower() != acct.email.lower():
        ledger.update_account(acct.id, last_status="needs_sign_in", last_checked=now())
        return SessionStatus(
            "needs_sign_in",
            f"That window was signed in as {status.email}, not {acct.email}. Press Sign in again "
            f"and choose {acct.email}.",
        )
    fields = {"last_status": status.state, "last_checked": now()}
    if status.ok and acct.chrome_profile:
        fields["chrome_profile"] = None  # now signed in on its own; no longer tied to Chrome
        log.info("account %s moved from Chrome %s to its own sign-in", acct.profile_name, acct.chrome_profile)
    ledger.update_account(acct.id, **fields)
    return status


def set_label(ledger: Ledger, acct: Account, label: str) -> None:
    """Rename an account in the app (local only)."""
    label = label.strip()
    if not label:
        raise AccountError("The name cannot be empty.")
    ledger.update_account(acct.id, label=label[:80])


def set_output_dir(ledger: Ledger, acct: Account, folder: str) -> Path:
    """Remember an account's output folder after checking it exists and is writable."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise AccountError("That folder does not exist.")
    if not os.access(path, os.W_OK):
        raise AccountError("The app cannot write to that folder.")
    path = path.resolve()
    ledger.update_account(acct.id, output_dir=str(path))
    return path


def with_session(ledger: Ledger, acct: Account, fn):
    """Run a library call; if the session is stale, renew it once and retry.

    Accounts added from Chrome are renewed by re-reading Chrome (Chrome rotates its
    Google cookies, so copies go stale within the hour). Others must sign in again.
    Exactly one retry, never a loop (ARCH-RULES 6).
    """
    try:
        result = fn()
    except AdapterError as exc:
        if exc.state != "needs_sign_in":
            raise
        if uses_own_signin(acct):  # no automatic Chrome re-read (Windows, D-022)
            ledger.update_account(acct.id, last_status="needs_sign_in", last_checked=now())
            raise
        log.info("session for %s stale, re-reading Chrome %s once", acct.profile_name, acct.chrome_profile)
        status = _signin(adapter.login_from_chrome, acct.profile_name, acct.chrome_profile, acct.email, False)
        if not status.ok:
            ledger.update_account(acct.id, last_status="needs_sign_in", last_checked=now())
            raise AdapterError(status.message, "needs_sign_in") from None
        try:
            result = fn()
        except AdapterError as again:
            ledger.update_account(acct.id, last_status=again.state, last_checked=now())
            raise
    ledger.update_account(acct.id, last_status="ok", last_checked=now())
    return result
