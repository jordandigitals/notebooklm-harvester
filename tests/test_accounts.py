"""Account management with a fake adapter (no real session, no Chrome)."""

import pytest

from nlh import accounts, adapter, paths
from nlh.adapter import ChromeProfile, SessionStatus

PROFILES = [
    ChromeProfile("Profile 2", "Jordan", "j@example.com"),
    ChromeProfile("Profile 6", "Second", "s@example.com"),
]


@pytest.fixture
def fake(monkeypatch, home):
    """Fake library: logins write a dummy session file; checks return a scripted status."""
    state = {"check": ["ok"], "chrome_logins": 0, "interactive_logins": 0}

    def write_session(name):
        d = paths.profile_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "storage_state.json").write_text("{}")  # fixture only, no real session

    def login_from_chrome(name, folder, email, verify=True):
        state["chrome_logins"] += 1
        write_session(name)
        return SessionStatus("ok", "Signed in.", email)

    def login_interactive(name, timeout_s=300):
        state["interactive_logins"] += 1
        write_session(name)
        return SessionStatus("ok", "Signed in.", "fresh@example.com")

    def check_session(name):
        s = state["check"].pop(0) if len(state["check"]) > 1 else state["check"][0]
        return SessionStatus(s, s)

    # Default: no own sign-in window, so these tests exercise the Chrome route on every OS.
    monkeypatch.setattr(adapter, "interactive_available", lambda: False)
    monkeypatch.setattr(adapter, "list_chrome_profiles", lambda *a: PROFILES)
    monkeypatch.setattr(adapter, "login_from_chrome", login_from_chrome)
    monkeypatch.setattr(adapter, "login_interactive", login_interactive)
    monkeypatch.setattr(adapter, "check_session", check_session)
    return state


def test_add_two_accounts_from_chrome(ledger, fake):
    a1, _ = accounts.add_from_chrome(ledger, "Profile 2")
    a2, _ = accounts.add_from_chrome(ledger, "Profile 6")
    assert a1.profile_name == "acct-j" and a2.profile_name == "acct-s"
    assert (a1.label, a1.email, a1.chrome_profile, a1.last_status) == (
        "Jordan",
        "j@example.com",
        "Profile 2",
        "ok",
    )
    assert paths.storage_path("acct-j").exists() and paths.storage_path("acct-s").exists()
    assert [p.folder for p, added in accounts.chrome_candidates(ledger) if added] == [
        "Profile 2",
        "Profile 6",
    ]


def test_duplicate_email_refused(ledger, fake):
    accounts.add_from_chrome(ledger, "Profile 2")
    with pytest.raises(accounts.AccountError):
        accounts.add_from_chrome(ledger, "Profile 2")
    assert len(ledger.accounts()) == 1


def test_unknown_chrome_profile(ledger, fake):
    with pytest.raises(accounts.AccountError):
        accounts.add_from_chrome(ledger, "Profile 99")


def test_add_interactive(ledger, fake):
    a, st = accounts.add_interactive(ledger)
    assert st.ok and a.email == "fresh@example.com" and a.chrome_profile is None
    assert fake["interactive_logins"] == 1


def test_stale_chrome_account_rereads_once(ledger, fake):
    a, _ = accounts.add_from_chrome(ledger, "Profile 2")
    fake["chrome_logins"] = 0
    fake["check"] = ["needs_sign_in"]
    st = accounts.check(ledger, a)
    assert fake["chrome_logins"] == 1  # exactly one re-read, no loop
    assert st.ok and ledger.account(a.id).last_status == "ok"


def test_stale_signin_account_not_retried(ledger, fake):
    a, _ = accounts.add_interactive(ledger)
    fake["check"] = ["needs_sign_in"]
    st = accounts.check(ledger, a)
    assert st.state == "needs_sign_in" and fake["interactive_logins"] == 1
    assert ledger.account(a.id).last_status == "needs_sign_in"


def test_one_signin_at_a_time(ledger, fake):
    accounts.SIGNIN_LOCK.acquire()
    try:
        with pytest.raises(accounts.AccountError):
            accounts.add_from_chrome(ledger, "Profile 2")
    finally:
        accounts.SIGNIN_LOCK.release()


def test_output_dir_and_label(ledger, fake, tmp_path):
    a, _ = accounts.add_from_chrome(ledger, "Profile 2")
    with pytest.raises(accounts.AccountError):
        accounts.set_output_dir(ledger, a, str(tmp_path / "missing"))
    out = tmp_path / "out"
    out.mkdir()
    accounts.set_output_dir(ledger, a, str(out))
    accounts.set_label(ledger, a, "  Work  ")
    b = ledger.account(a.id)
    assert b.output_dir == str(out.resolve()) and b.label == "Work"
    with pytest.raises(accounts.AccountError):
        accounts.set_label(ledger, a, "   ")


def test_fake_second_profile_directory_kept_separate(ledger, fake):
    a1, _ = accounts.add_from_chrome(ledger, "Profile 2")
    a2, _ = accounts.add_from_chrome(ledger, "Profile 6")
    assert paths.profile_dir(a1.profile_name) != paths.profile_dir(a2.profile_name)
    assert (
        paths.storage_path(a1.profile_name).parent.parent == paths.storage_path(a2.profile_name).parent.parent
    )


def test_with_session_rereads_chrome_once_and_retries(ledger, fake):
    a, _ = accounts.add_from_chrome(ledger, "Profile 2")
    fake["chrome_logins"] = 0
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise adapter.AdapterError("expired", "needs_sign_in")
        return "listing"

    assert accounts.with_session(ledger, a, flaky) == "listing"
    assert len(calls) == 2 and fake["chrome_logins"] == 1
    assert ledger.account(a.id).last_status == "ok"


def test_with_session_gives_up_after_one_retry(ledger, fake):
    a, _ = accounts.add_from_chrome(ledger, "Profile 2")
    fake["chrome_logins"] = 0

    def always_stale():
        raise adapter.AdapterError("expired", "needs_sign_in")

    with pytest.raises(adapter.AdapterError):
        accounts.with_session(ledger, a, always_stale)
    assert fake["chrome_logins"] == 1
    assert ledger.account(a.id).last_status == "needs_sign_in"


def test_with_session_signin_account_not_renewed(ledger, fake):
    a, _ = accounts.add_interactive(ledger)

    def stale():
        raise adapter.AdapterError("expired", "needs_sign_in")

    with pytest.raises(adapter.AdapterError):
        accounts.with_session(ledger, a, stale)
    assert fake["interactive_logins"] == 1


def test_stale_csrf_error_maps_to_sign_in():
    err = adapter._map_error(ValueError("CSRF token not found in HTML. Final URL: x"))
    assert err.state == "needs_sign_in"


def test_windows_sign_in_again_moves_chrome_account_to_own_signin(ledger, fake, monkeypatch):
    """D-022: on Windows, Sign in again on a Chrome account opens the own window, not Chrome."""
    import sys

    acct, _ = accounts.add_from_chrome(ledger, "Profile 2")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(adapter, "interactive_available", lambda: True)
    monkeypatch.setattr(
        adapter,
        "login_interactive",
        lambda name, timeout_s=300: SessionStatus("ok", "Signed in.", "j@example.com"),
    )
    before = fake["chrome_logins"]
    st = accounts.sign_in_again(ledger, ledger.account(acct.id))
    assert st.ok and fake["chrome_logins"] == before
    after = ledger.account(acct.id)
    assert after.chrome_profile is None and after.last_status == "ok"
    assert after.profile_name == acct.profile_name  # same account, same saved files


def test_own_signin_with_wrong_google_account_is_refused(ledger, fake, monkeypatch):
    import sys

    acct, _ = accounts.add_from_chrome(ledger, "Profile 2")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(adapter, "interactive_available", lambda: True)
    monkeypatch.setattr(
        adapter,
        "login_interactive",
        lambda name, timeout_s=300: SessionStatus("ok", "Signed in.", "x@example.com"),
    )
    st = accounts.sign_in_again(ledger, ledger.account(acct.id))
    assert not st.ok and "x@example.com" in st.message
    assert ledger.account(acct.id).chrome_profile == "Profile 2"


def test_mac_sign_in_again_still_rereads_chrome(ledger, fake, monkeypatch):
    import sys

    acct, _ = accounts.add_from_chrome(ledger, "Profile 2")
    monkeypatch.setattr(sys, "platform", "darwin")
    before = fake["chrome_logins"]
    assert accounts.sign_in_again(ledger, ledger.account(acct.id)).ok
    assert fake["chrome_logins"] == before + 1
    assert ledger.account(acct.id).chrome_profile == "Profile 2"


def test_windows_stale_chrome_account_skips_automatic_chrome_reread(ledger, fake, monkeypatch):
    """On Windows a stale Chrome account goes straight to Sign in again (no locked re-read)."""
    import sys

    acct, _ = accounts.add_from_chrome(ledger, "Profile 2")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(adapter, "interactive_available", lambda: True)
    before = fake["chrome_logins"]

    def stale():
        raise adapter.AdapterError("Sign in again.", "needs_sign_in")

    with pytest.raises(adapter.AdapterError):
        accounts.with_session(ledger, ledger.account(acct.id), stale)
    assert fake["chrome_logins"] == before
    assert ledger.account(acct.id).last_status == "needs_sign_in"
