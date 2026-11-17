"""D-035 on the Mac: the macOS check, the Cocoa guards (fail safe), downloads, screens, quit and wipe.

The pure parts run on every system with fakes. The checks against the real pywebview Cocoa classes run only
on a Mac with pywebview installed. The window itself is checked by the build report (screenshots of the
native window, the quit alert, six sign-in runs).
"""

import json
import sys
import types

import pytest

from nlh import server, window, window_mac

TOKEN = "test-token"


# ---------------------------------------------------------------- macOS version (ruling 5)


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("15.3.2", True),
        ("11.3", True),
        ("11.3.1", True),
        ("12.0", True),
        ("11.2.3", False),
        ("10.15.7", False),
        ("", False),
        ("junk", False),
    ],
)
def test_window_needs_macos_11_3(text, ok):
    assert (window_mac.runtime_ok(text) == text) is ok


def test_old_macos_opens_the_browser_with_the_plain_line(ledger, monkeypatch):
    urls = []
    monkeypatch.setattr(server.webbrowser, "open", urls.append)
    monkeypatch.setattr(server.threading, "Timer", lambda t, fn: types.SimpleNamespace(start=fn))
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    monkeypatch.setattr(window_mac.platform, "mac_ver", lambda: ("10.15.7", ("", "", ""), "x86_64"))
    app = server.create_app(TOKEN, ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert urls == ["http://127.0.0.1:5000/?t=x"] and app.config["WINDOW_NOTE"] == window.FALLBACK_NOTE


# ---------------------------------------------------------------- pure guard decisions


def _allowed(url):
    return window.allowed_url(url, 5000)


@pytest.mark.parametrize(
    ("url", "what"),
    [
        ("http://127.0.0.1:5000/?view=history", "allow"),
        ("about:blank", "allow"),
        ("https://notebooklm.google.com/notebook/x", "browser"),
        ("http://example.com/", "browser"),
        ("http://127.0.0.1:5001/", "browser"),  # another local port never loads in the window
        ("file:///etc/hosts", "block"),
        ("javascript:alert(1)", "block"),
        ("data:text/html,hi", "block"),
    ],
)
def test_nav_decision(url, what):
    assert window_mac.nav_decision(url, _allowed) == what


def test_should_download():
    assert window_mac.should_download('attachment; filename="history.csv"', True)  # text/csv is showable
    assert window_mac.should_download("", False)
    assert not window_mac.should_download("", True)
    assert not window_mac.should_download("inline", True)


def test_flip_screens_measures_from_the_top_of_the_primary_screen():
    # 1440 x 900 screen, Dock 70 points at the bottom, menu bar 25 at the top
    assert window_mac.flip_screens([(0, 70, 1440, 805)], 900) == [(0, 25, 1440, 805)]
    # a second screen above the first
    two = window_mac.flip_screens([(0, 70, 1440, 805), (0, 900, 1920, 1055)], 900)
    assert two[1] == (0, -1055, 1920, 1055)
    assert window.first_size(window_mac.flip_screens([(0, 70, 1440, 805)], 900)) == (80, 57, 1280, 740)


def test_saved_note_and_script():
    text = window.SAVED_NOTE.format(name='a "b" (2).csv')
    assert text == 'Saved to Downloads as a "b" (2).csv' and "—" not in text
    script = window_mac.saved_script(text)
    assert script.endswith(f"({json.dumps(text)})")  # passed as a JSON string, never pasted raw


# ---------------------------------------------------------------- fail safe (ruling 1)


def _fake_cocoa(missing=None):
    def cls(name):
        attrs = {m: (lambda *a: None) for m in window_mac.GUARDED.get(name, []) if m != missing}
        return type(name, (), attrs)

    view = type("BrowserView", (), {"instances": {}})
    for name in window_mac.GUARDED:
        setattr(view, name, cls(name))
    return types.SimpleNamespace(BrowserView=view)


@pytest.mark.parametrize("missing", [m for ms in window_mac.GUARDED.values() for m in ms])
def test_a_missing_selector_means_no_window(missing):
    with pytest.raises(window_mac.GuardError):
        window_mac._check(_fake_cocoa(missing))


def test_all_selectors_present_passes_the_check():
    window_mac._check(_fake_cocoa())


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class _FakeWebview:
    def __init__(self):
        self.settings = {}
        self.created = []
        self.started = []

    def create_window(self, title, url, **kw):
        w = types.SimpleNamespace(
            events=types.SimpleNamespace(closing=_Event(), loaded=_Event(), moved=_Event(), resized=_Event()),
            destroy=lambda: None,
        )
        self.created.append((title, url, kw))
        return w

    def start(self, **kw):
        self.started.append(kw)


class _Worker:
    jobs = {}
    order = []


def test_guard_failure_opens_no_window_and_falls_back(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def fail(*a, **k):
        raise window_mac.GuardError("BrowserDelegate.webView_decidePolicyForNavigationAction")

    monkeypatch.setattr(window_mac, "install_guards", fail)
    monkeypatch.setattr(window.importlib, "import_module", lambda name: types.SimpleNamespace())
    wv = _FakeWebview()
    nw = window.NativeWindow(wv, "http://127.0.0.1:5000/", 5000, _Worker(), None, lambda b: None)
    assert nw.run() is False
    assert wv.created == [] and wv.started == []  # never an unguarded window


def test_show_with_a_guard_failure_opens_the_browser_with_the_plain_line(ledger, monkeypatch):
    urls = []
    monkeypatch.setattr(server.webbrowser, "open", urls.append)
    monkeypatch.setattr(server.threading, "Timer", lambda t, fn: types.SimpleNamespace(start=fn))
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    monkeypatch.setattr(window, "runtime_version", lambda: "15.3.2")
    monkeypatch.setattr(window, "load_webview", lambda: _FakeWebview())

    def fail(*a, **k):
        raise window_mac.GuardError("missing")

    monkeypatch.setattr(window_mac, "install_guards", fail)
    monkeypatch.setattr(window.importlib, "import_module", lambda name: types.SimpleNamespace())
    app = server.create_app(TOKEN, ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert urls and app.config["WINDOW_NOTE"] == window.FALLBACK_NOTE and not app.config.get("APP_WINDOW")


def test_mac_window_runs_guarded_then_wipes_web_data(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(window_mac, "install_guards", lambda *a, **k: calls.append("guards"))
    monkeypatch.setattr(window_mac, "wipe_website_data", lambda: calls.append("wipe") or True)
    monkeypatch.setattr(window_mac, "screens", lambda: [(0, 25, 1440, 805)])
    monkeypatch.setattr(window_mac, "keep_working", lambda reason: calls.append("awake") or "token")
    monkeypatch.setattr(window_mac, "end_working", lambda token: calls.append("asleep"))
    monkeypatch.setattr(window.importlib, "import_module", lambda name: types.SimpleNamespace())
    wv = _FakeWebview()
    nw = window.NativeWindow(wv, "http://127.0.0.1:5000/", 5000, _Worker(), None, lambda b: None)
    nw.loaded.set()  # the watchdog sees a loaded page
    assert nw.run() is True
    # guards before the window; App Nap kept away while it is open; data wiped after it closed
    assert calls == ["guards", "awake", "asleep", "wipe"]
    assert wv.settings["ALLOW_DOWNLOADS"] is True and wv.started[0] == {"gui": "cocoa", "private_mode": True}
    assert wv.created[0][2]["width"] == 1280 and wv.created[0][2]["height"] == 740


def test_mac_quit_question_and_focus_use_the_cocoa_parts(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    asked = []
    monkeypatch.setattr(window_mac, "ask_quit", lambda q, t: asked.append((q, t)) or False)
    nw = window.NativeWindow(None, "http://127.0.0.1:5000/", 5000, _Worker(), None, lambda b: None)
    assert nw._ask_quit() is False and asked == [(window.QUIT_QUESTION, window.TITLE)]
    nw.window = object()
    monkeypatch.setattr(window.NativeWindow, "_focus_mac", lambda self: True)
    assert nw.focus() is True


def test_mac_download_toast_reaches_the_page(monkeypatch):
    nw = window.NativeWindow(None, "http://127.0.0.1:5000/", 5000, _Worker(), None, lambda b: None)
    scripts = []
    nw.window = types.SimpleNamespace(evaluate_js=scripts.append)
    nw._saved("notebooklm-history (2).csv")
    assert scripts == [window_mac.saved_script("Saved to Downloads as notebooklm-history (2).csv")]


# ---------------------------------------------------------------- the real pywebview on a Mac


mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="pywebview's Cocoa backend is Mac only")


@mac_only
def test_pinned_pywebview_still_has_every_cocoa_method_we_replace():
    """If a pywebview update renames one, the guard would silently stop working: fail here instead."""
    cocoa = pytest.importorskip("webview.platforms.cocoa")
    window_mac._check(cocoa)


@mac_only
def test_guards_install_on_the_real_cocoa_classes(tmp_path):
    cocoa = pytest.importorskip("webview.platforms.cocoa")
    window_mac.install_guards(cocoa, 5000, _allowed, lambda name: None, lambda: tmp_path)
    view = cocoa.BrowserView
    expected = {
        ("BrowserDelegate", "webView_decidePolicyForNavigationAction_decisionHandler_"): "nav",
        (
            "BrowserDelegate",
            "webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_",
        ): "popup",
        ("BrowserDelegate", "webView_decidePolicyForNavigationResponse_decisionHandler_"): "response",
        (
            "DownloadDelegate",
            "download_decideDestinationUsingResponse_suggestedFilename_completionHandler_",
        ): ("destination"),
        ("WebKitHost", "keyDown_"): "key_down",
    }
    for (cls, method), name in expected.items():
        assert getattr(getattr(view, cls), method).callable.__name__ == name
    assert view.BrowserDelegate.instancesRespondToSelector_(b"webView:navigationResponse:didBecomeDownload:")


@mac_only
def test_this_mac_qualifies_or_says_why():
    v = window_mac.macos_version()
    assert v is None or window_mac.parse_version(v) >= window_mac.MIN_MACOS


@mac_only
def test_keep_working_begins_and_ends_an_activity():
    token = window_mac.keep_working("test")
    assert token is not None
    window_mac.end_working(token)


@pytest.mark.parametrize(("plat", "capable"), [("darwin", True), ("linux", False)])
def test_state_says_where_the_open_in_choice_applies(ledger, monkeypatch, plat, capable):
    """Settings shows "Open the app in" on every system that has the app window (Mac and Windows)."""
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    monkeypatch.setattr(server.sys, "platform", plat)
    st = app.test_client().get("/api/state", headers={"X-NLH-Token": TOKEN}).get_json()
    assert st["window_capable"] is capable
