"""Adapter pieces that run without a real session, and log redaction."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from nlh import adapter
from nlh.log import redact


def test_chrome_profiles_names_and_emails_only(tmp_path):
    ls = tmp_path / "Local State"
    ls.write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Your Chrome", "user_name": ""},
                        "Profile 2": {"name": "Jordan", "user_name": "j@example.com"},
                        "Profile 1": {"name": "Other", "user_name": "o@example.com"},
                    }
                }
            }
        )
    )
    got = adapter.list_chrome_profiles(ls)
    assert [(p.folder, p.name, p.email) for p in got] == [
        ("Profile 1", "Other", "o@example.com"),
        ("Profile 2", "Jordan", "j@example.com"),
    ]


def test_backoff_retries_rate_limit_then_succeeds():
    calls, sleeps = [], []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 Too Many Requests")
        return "ok"

    async def fake_sleep(s):
        sleeps.append(s)

    assert asyncio.run(adapter._with_backoff(call, sleep=fake_sleep)) == "ok"
    assert sleeps == [4, 8]


def test_backoff_does_not_retry_other_errors():
    async def call():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(adapter._with_backoff(call, sleep=lambda s: None))


def test_overview_mapping():
    nb = adapter.Notebook("nb", "Notebook", None)
    a = SimpleNamespace(id="a1", title="", status=3, created_at=None)
    ov = adapter._overview(a, nb)
    assert ov.title == "Notebook" and ov.completed
    a2 = SimpleNamespace(id="a2", title="T", status=2, created_at=None)
    assert adapter._overview(a2, nb).status == "in_progress"


def test_check_session_without_file(home):
    st = adapter.check_session("nobody")
    assert st.state == "needs_sign_in"


def test_redact_removes_cookie_names_and_tokens():
    text = "cookies SID HSID __Secure-1PSID SAPISID NID token=abc123 " + "x" * 40
    out = redact(text)
    for bad in ("SID", "__Secure", "SAPISID", "NID", "abc123", "x" * 40):
        assert bad not in out


def test_redact_keeps_uuids_and_paths():
    line = "saved artifact f9dd4176-ac01-49f8-bd2c-77b8876b04a8 to /Users/someone/Music/Long Name/T.mp3"
    assert redact(line) == line


def test_interactive_login_stops_when_google_signin_not_finished(home, monkeypatch):
    """No library login runs (and nothing is saved) unless Google really signed in."""
    calls = []
    monkeypatch.setattr(adapter, "_google_signin_window", lambda name, t: False)
    monkeypatch.setattr(adapter, "_cli", lambda args, timeout: calls.append(args))
    st = adapter.login_interactive("acct-x", 5)
    assert st.state == "needs_sign_in"
    assert "not finished" in st.message
    assert calls == []


def test_interactive_login_hands_over_after_google_signin(home, monkeypatch):
    """After a real Google sign-in the library login runs for the same profile."""
    calls = []
    monkeypatch.setattr(adapter, "_google_signin_window", lambda name, t: True)

    def fake_cli(args, timeout):
        calls.append(args)
        return SimpleNamespace(returncode=1, stderr="", stdout="")

    monkeypatch.setattr(adapter, "_cli", fake_cli)
    adapter.login_interactive("acct-x", 5)
    assert calls and calls[0][:4] == ["--profile", "acct-x", "login", "--browser"]


def test_signed_in_cookie_names_are_names_only():
    """The sign-in wait compares cookie names, never values."""
    assert adapter.SIGNED_IN_COOKIE_NAMES == {"SID", "__Secure-1PSID"}


def test_windows_fix_finds_network_cookies_and_reads_with_key_file(tmp_path, monkeypatch):
    """Windows Chrome keeps cookies in <profile>/Network/Cookies; the fix finds and reads them."""
    import sys
    import types

    from notebooklm.cli import _chromium_profiles as cp

    root = tmp_path / "User Data"
    (root / "Profile 2" / "Network").mkdir(parents=True)
    (root / "Profile 2" / "Network" / "Cookies").write_bytes(b"")  # empty fixture, no real data
    (root / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Work"}}}}), encoding="utf-8"
    )
    # Keep the module's own functions so monkeypatch restores them after the test.
    monkeypatch.setattr(cp, "discover_chromium_profiles", cp.discover_chromium_profiles)
    monkeypatch.setattr(cp, "read_chromium_profile_cookies", cp.read_chromium_profile_cookies)
    monkeypatch.setattr(cp, "_user_data_dir", lambda name: root)
    seen = {}

    def fake_chromium_based(key_file, db, domains):
        seen.update(key_file=key_file, db=db, domains=domains)
        return [{"name": "SID", "domain": ".google.com"}]

    fake_module = types.SimpleNamespace(chromium_based=fake_chromium_based)
    monkeypatch.setitem(sys.modules, "rookie_cookies", fake_module)
    adapter._windows_chrome_cookie_fix()

    profiles = cp.discover_chromium_profiles("chrome")
    assert [(p.directory_name, p.human_name) for p in profiles] == [("Profile 2", "Work")]
    got = cp.read_chromium_profile_cookies(profiles[0], domains=[".google.com"])
    assert got == [{"name": "SID", "domain": ".google.com"}]
    assert seen["key_file"] == str(root / "Local State")
    assert seen["db"] == str(root / "Profile 2" / "Network" / "Cookies")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("could not read chrome cookies: browser database is locked.", "Quit Chrome completely"),
        ("importerror: dll load failed while importing rookie_cookies: ... virus ...", "Windows security"),
        ("no populated chrome profiles were found.", "no saved Google sign-in"),
        ("something else", "Details are in the log"),
    ],
)
def test_login_failure_messages_are_plain(text, expected):
    assert expected in adapter._login_failure_message(text)


def test_cli_uses_windows_wrapper_only_on_windows(monkeypatch):
    """On Windows sign-in commands run through nlh.adapter so the cookie fix applies."""
    captured = {}
    monkeypatch.setattr(adapter.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    monkeypatch.setattr(adapter.sys, "platform", "win32")
    adapter._cli(["auth", "check"], 5)
    assert captured["cmd"][1:4] == ["-m", "nlh.adapter", "auth"]
    captured.clear()
    monkeypatch.setattr(adapter.sys, "platform", "darwin")
    adapter._cli(["auth", "check"], 5)
    assert captured["cmd"][1:4] == ["-m", "notebooklm", "auth"]
