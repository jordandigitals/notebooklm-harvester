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
    monkeypatch.setattr(adapter, "_run_group", lambda cmd, env, timeout: captured.setdefault("cmd", cmd))
    monkeypatch.setattr(adapter.sys, "platform", "win32")
    adapter._cli(["auth", "check"], 5)
    assert captured["cmd"][1:4] == ["-m", "nlh.adapter", "auth"]
    captured.clear()
    monkeypatch.setattr(adapter.sys, "platform", "darwin")
    adapter._cli(["auth", "check"], 5)
    assert captured["cmd"][1:4] == ["-m", "notebooklm", "auth"]


class _FakePage:
    def __init__(self, url):
        self.url = url
        self.visited = []

    def goto(self, url, **kw):
        self.visited.append(url)
        # Google's sign-in continues to its security check-up page instead of NotebookLM;
        # only an explicit visit to NotebookLM lands there (as notebook.google.com).
        self.url = (
            "https://gds.google.com/web/chip"
            if "accounts.google.com" in url
            else "https://notebook.google.com/"
        )


class _FakeContext:
    def __init__(self, page, stale_at_launch=False):
        self.pages = [page]
        self.stale = stale_at_launch

    def cookies(self, url):
        # A fresh profile has no sign-in cookie until the person signs in (after the first
        # visit); a stale profile already carries an old one that never changes.
        if self.stale:
            return [{"name": "SID", "expires": 100}, {"name": "NID", "expires": 5}]
        if not self.pages[0].visited:
            return [{"name": "NID", "expires": 5}]
        return [{"name": "SID", "expires": 200}, {"name": "NID", "expires": 5}]

    def close(self):
        pass


def test_signin_window_takes_signed_in_window_to_notebooklm(home, monkeypatch):
    """Signed in but stuck on another Google page: the app opens NotebookLM itself and succeeds."""
    import sys
    import types

    page = _FakePage("about:blank")
    ctx = _FakeContext(page)

    class _PW:
        def __enter__(self):
            launcher = types.SimpleNamespace(launch_persistent_context=lambda **kw: ctx)
            return types.SimpleNamespace(chromium=launcher)

        def __exit__(self, *a):
            return False

    fake_sync_api = types.SimpleNamespace(sync_playwright=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(adapter, "NUDGE_AFTER_S", 0)
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    assert adapter._google_signin_window("acct-x", 30) is True
    assert page.visited[0] == adapter.google_signin_url()
    assert page.visited[-1] == adapter.notebooklm_url()


def _fake_playwright(monkeypatch, ctx):
    import sys
    import types

    class _PW:
        def __enter__(self):
            launcher = types.SimpleNamespace(launch_persistent_context=lambda **kw: ctx)
            return types.SimpleNamespace(chromium=launcher)

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(sync_playwright=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    monkeypatch.setattr(adapter, "NUDGE_AFTER_S", 0)
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)


def test_signin_window_never_nudges_on_stale_cookies(home, monkeypatch):
    """An old sign-in cookie left in the profile must not pull the person off Google's page."""
    page = _FakePage("about:blank")
    _fake_playwright(monkeypatch, _FakeContext(page, stale_at_launch=True))
    assert adapter._google_signin_window("acct-x", 1) is False
    assert page.visited == [adapter.google_signin_url()]  # never sent to NotebookLM


def test_notebooklm_address_comes_from_the_library():
    from notebooklm.config import get_base_url

    assert adapter.notebooklm_url() == get_base_url().rstrip("/") + "/"
    assert adapter.google_signin_url().startswith(
        "https://accounts.google.com/ServiceLogin?continue=https%3A"
    )


@pytest.mark.parametrize(
    ("args", "allowed"),
    [
        (["--profile", "p", "auth", "check", "--json"], True),
        (["login", "--browser-cookies", "chrome::Profile 2"], True),
        (["--profile", "p", "login", "--browser", "chrome"], True),
        (["delete", "-n", "nb"], False),
        (["-n", "nb", "source", "add", "f.pdf"], False),
        (["generate", "audio"], False),
        ([], False),
    ],
)
def test_library_cli_runs_only_sign_in_commands(args, allowed, monkeypatch):
    """The packaged exe passes its arguments through; only auth and login may reach the library."""
    import sys
    import types

    ran = []
    fake_cli = types.SimpleNamespace(main=lambda: ran.append(1))
    monkeypatch.setitem(sys.modules, "notebooklm.notebooklm_cli", fake_cli)
    monkeypatch.setattr(adapter, "_windows_chrome_cookie_fix", lambda: None)
    if allowed:
        adapter.run_library_cli(args)
        assert ran == [1]
    else:
        with pytest.raises(SystemExit):
            adapter.run_library_cli(args)
        assert ran == []


def test_redact_cookie_values_with_slashes_and_json_values():
    """SAPISID-style values (xxxx/yyyy) and JSON value fields are removed; paths survive."""
    samples = [
        "SAPISID=AbCdEfGh1234ijkl/MnOpQrSt5678uvwx",
        '{"name": "x", "value": "AbCdEfGhIjKlMnOpQrSt/12345"}',
        "token list AbCdEfGh1234ijkl/MnOpQrSt5678uvwx done",
        "SID=g.a000short",
    ]
    for s in samples:
        out = redact(s)
        assert "1234ijkl" not in out and "IjKlMnOp" not in out and "a000short" not in out, out
    path = "C:/repos/notebooklm-harvester/.venv/Lib/site-packages/notebooklm/_auth/browser_capture.py"
    assert redact(path) == path


def test_tracebacks_in_the_log_are_redacted():
    """log.exception writes a traceback; the formatter redacts the whole formatted record."""
    import logging
    import sys

    from nlh.log import RedactingFormatter, get_logger

    try:
        raise RuntimeError("Cookie: __Secure-1PSID=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd")
    except RuntimeError:
        record = logging.LogRecord("nlh", logging.ERROR, __file__, 1, "job failed", (), sys.exc_info())
    assert any(isinstance(h.formatter, RedactingFormatter) for h in get_logger().handlers)
    text = RedactingFormatter("%(levelname)s %(message)s").format(record)
    assert "AbCdEfGhIjKl" not in text and "__Secure-1PSID" not in text
    assert "job failed" in text and "Traceback" in text


def test_read_only_client_exposes_only_three_calls():
    """FINDING-5: the wrapped client has list, list_audio, download_audio and nothing else."""
    from types import SimpleNamespace

    import pytest

    calls = []
    nb = SimpleNamespace(
        list=lambda: calls.append("list"),
        delete=lambda *a: calls.append("delete"),
        create=lambda *a: calls.append("create"),
    )
    art = SimpleNamespace(
        list_audio=lambda *a: calls.append("la"),
        download_audio=lambda *a: calls.append("dl"),
        delete=lambda *a: calls.append("adel"),
        generate_audio=lambda *a: calls.append("gen"),
    )
    ro = adapter.ReadOnlyClient(SimpleNamespace(notebooks=nb, artifacts=art, sources=object(), chat=object()))
    ro.notebooks.list()
    ro.artifacts.list_audio("n")
    ro.artifacts.download_audio("n", "p", "a")
    for bad in (
        lambda: ro.notebooks.delete,
        lambda: ro.notebooks.create,
        lambda: ro.artifacts.delete,
        lambda: ro.artifacts.generate_audio,
        lambda: ro.sources,
        lambda: ro.chat,
        lambda: ro.notebooks.rename,
    ):
        with pytest.raises(AttributeError):
            bad()
    assert calls == ["list", "la", "dl"]


def test_cli_timeout_kills_the_whole_group(tmp_path):
    """FINDING-4: a timed-out command and the child it started are both gone."""
    import subprocess
    import sys
    import time

    import pytest

    marker = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time;"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open(r'{marker}', 'w').write(str(c.pid));"
        "time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        adapter._run_group([sys.executable, "-c", script], dict(__import__("os").environ), timeout=3)
    child = int(marker.read_text())
    time.sleep(1)
    alive = True
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child}"], capture_output=True, text=True
            ).stdout
            alive = str(child) in out
        else:
            import os

            os.kill(child, 0)
    except OSError:
        alive = False
    assert not alive
