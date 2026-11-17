"""D-035 native app window: the decision, the fallback, single instance, quit rules and the navigation guard.

The window itself needs a screen and WebView2, so these tests drive the logic with fakes; the real window is
checked by hand and by the build report (screenshots of the native window).
"""

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from nlh import server, window
from nlh import settings as app_settings

TOKEN = "test-token"
H = {"X-NLH-Token": TOKEN}


# ---------------------------------------------------------------- the decision


@pytest.mark.parametrize(
    ("argv", "env", "open_in", "forced"),
    [
        ([], {}, "window", False),
        (["--browser"], {}, "window", True),
        ([], {"NLH_BROWSER": "1"}, "window", True),
        ([], {"NLH_BROWSER": "0"}, "window", False),
        ([], {}, "browser", True),
    ],
)
def test_forced_browser(argv, env, open_in, forced):
    assert window.forced_browser(argv, env, open_in) is forced


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"platform": "win32", "forced": False, "webview2": "157.0", "importable": True}, None),
        ({"platform": "win32", "forced": True, "webview2": "157.0", "importable": True}, "forced"),
        ({"platform": "darwin", "forced": False, "webview2": None, "importable": True}, "not_windows"),
        ({"platform": "win32", "forced": False, "webview2": None, "importable": True}, "no_webview2"),
        ({"platform": "win32", "forced": False, "webview2": "157.0", "importable": False}, "no_library"),
    ],
)
def test_decide(kw, reason):
    assert window.decide(**kw) == reason


def test_only_real_failures_are_reported_to_the_person():
    for reason in ("no_webview2", "no_library", "failed"):
        assert window.is_fallback(reason)
    for reason in ("forced", "not_windows", None):
        assert not window.is_fallback(reason)
    assert "browser" in window.FALLBACK_NOTE and "\u2014" not in window.FALLBACK_NOTE


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_webview2_version_reads_the_registry_without_failing():
    v = window.webview2_version()
    assert v is None or (isinstance(v, str) and v[0].isdigit())


def test_load_webview_failure_means_browser(monkeypatch):
    def boom(name):
        raise ImportError(name)

    monkeypatch.setattr(window.importlib, "import_module", boom)
    assert window.load_webview() is None


# ---------------------------------------------------------------- the navigation guard


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:5000/?t=x", True),
        ("http://127.0.0.1:5000/static/app.css", True),
        ("about:blank", True),
        ("http://127.0.0.1:5001/", False),  # another local port (the phone listener, another app)
        ("http://localhost:5000/", False),
        ("https://127.0.0.1:5000/", False),
        ("https://notebooklm.google.com/notebook/x", False),
        ("http://127.0.0.1.evil.example:5000/", False),
        ("http://127.0.0.1@evil.example:5000/", False),
        ("file:///C:/Windows/win.ini", False),
        ("javascript:alert(1)", False),
        ("data:text/html,hi", False),
    ],
)
def test_only_the_apps_own_page_may_load(url, ok):
    assert window.allowed_url(url, 5000) is ok


def _fake_edgechromium():
    calls = {"nav": 0}

    class EdgeChrome:
        def on_navigation_start(self, sender, args):
            calls["nav"] += 1

        def on_download_starting(self, sender, args):
            raise AssertionError("pywebview's own download dialog must not run")

    return types.SimpleNamespace(EdgeChrome=EdgeChrome), calls


def test_guard_cancels_outside_navigation_and_opens_the_browser(monkeypatch):
    ec, calls = _fake_edgechromium()
    opened = []
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    window.install_guards(ec, 5000)
    edge = ec.EdgeChrome()
    outside = types.SimpleNamespace(Uri="https://notebooklm.google.com/notebook/abc", Cancel=False)
    edge.on_navigation_start(None, outside)
    assert outside.Cancel is True and opened == ["https://notebooklm.google.com/notebook/abc"]
    assert calls["nav"] == 0  # never reaches pywebview's own handler
    script = types.SimpleNamespace(Uri="javascript:alert(1)", Cancel=False)
    edge.on_navigation_start(None, script)
    assert script.Cancel is True and len(opened) == 1  # cancelled, and not handed to the browser either
    own = types.SimpleNamespace(Uri="http://127.0.0.1:5000/?view=history", Cancel=False)
    edge.on_navigation_start(None, own)
    assert own.Cancel is False and calls["nav"] == 1


def test_download_gets_a_new_name_and_never_overwrites(monkeypatch, tmp_path):
    ec, _ = _fake_edgechromium()
    monkeypatch.setattr(window, "downloads_folder", lambda: tmp_path)
    window.install_guards(ec, 5000)
    (tmp_path / "notebooklm-harvest.csv").write_text("keep me", encoding="utf-8")
    args = types.SimpleNamespace(ResultFilePath=r"C:\Users\x\Downloads\notebooklm-harvest.csv")
    ec.EdgeChrome().on_download_starting(None, args)
    assert Path(args.ResultFilePath) == tmp_path / "notebooklm-harvest (2).csv"
    assert (tmp_path / "notebooklm-harvest.csv").read_text(encoding="utf-8") == "keep me"


def test_unique_path_counts_up_and_drops_folders(tmp_path):
    (tmp_path / "a.csv").write_text("1")
    (tmp_path / "a (2).csv").write_text("2")
    assert window.unique_path(tmp_path, "a.csv").name == "a (3).csv"
    assert window.unique_path(tmp_path, "../../evil.csv") == tmp_path / "evil.csv"


@pytest.mark.skipif(sys.platform != "win32", reason="the WebView2 backend is Windows only")
def test_pinned_pywebview_still_has_the_handlers_we_hook():
    """If a pywebview update renames these, the guard would silently stop working: fail here instead."""
    edgechromium = pytest.importorskip("webview.platforms.edgechromium")
    assert callable(edgechromium.EdgeChrome.on_navigation_start)
    assert callable(edgechromium.EdgeChrome.on_download_starting)


# ---------------------------------------------------------------- window place


def test_clamp_bounds():
    screens = [(0, 0, 1920, 1040)]
    assert window.clamp_bounds([100, 100, 1200, 800], screens) == (100, 100, 1200, 800)
    assert window.clamp_bounds([100, 100, 200, 200], screens) == (100, 100, 380, 560)  # minimum size
    assert window.clamp_bounds([5000, 5000, 1200, 800], screens) is None  # a screen that is gone
    assert window.clamp_bounds([-32000, -32000, 160, 28], screens) is None  # saved while minimized
    assert window.clamp_bounds("junk", screens) is None
    assert window.clamp_bounds([0, 0, 4000, 3000], screens) == (0, 0, 1920, 1040)


def test_first_size_fits_the_screen():
    """A 1280 x 820 window would run under the taskbar on a small or scaled screen: shrink and centre it."""
    assert window.first_size([(0, 0, 1536, 824)]) == (0 + (1536 - 1280) // 2, (824 - 758) // 2, 1280, 758)
    assert window.first_size([(0, 0, 2560, 1400)]) == ((2560 - 1280) // 2, (1400 - 820) // 2, 1280, 820)
    assert window.first_size([(0, 0, 300, 400)])[2:] == window.MIN_SIZE  # never below the minimum
    assert window.first_size([])[2:] == window.DEFAULT_SIZE


def test_window_settings_are_validated(home):
    s = app_settings.load()
    assert s["open_in"] == "window" and s["window_bounds"] is None
    app_settings.save({"open_in": "browser", "window_bounds": [10, 20, 1200, 800]})
    assert app_settings.load()["open_in"] == "browser"
    for bad in ({"open_in": "tab"}, {"window_bounds": [1, 2, 3]}, {"window_bounds": ["a", 2, 3, 4]}):
        with pytest.raises(app_settings.SettingsError):
            app_settings.save(bad)


# ---------------------------------------------------------------- quit rules


class _Job:
    def __init__(self, kind, state="running"):
        self.kind, self.state = kind, state
        self.cancel = threading.Event()


class _Worker:
    def __init__(self, *jobs):
        self.jobs = {str(i): j for i, j in enumerate(jobs)}
        self.order = list(self.jobs)


class _FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.scripts = []

    def destroy(self):
        self.destroyed = True

    def evaluate_js(self, script):
        self.scripts.append(script)


def _nw(worker, ask=True):
    nw = window.NativeWindow(None, "http://127.0.0.1:5000/", 5000, worker, None, lambda b: None)
    nw.window = _FakeWindow()
    nw._ask_quit = lambda: ask
    return nw


def test_close_without_a_harvest_quits_at_once_and_ends_other_jobs():
    refresh = _Job("refresh")
    nw = _nw(_Worker(refresh))
    assert nw._on_closing() is True
    assert refresh.cancel.is_set()


def test_close_during_a_harvest_asks_and_no_keeps_the_window():
    harvest = _Job("harvest")
    nw = _nw(_Worker(harvest), ask=False)
    assert nw._on_closing() is False
    assert not harvest.cancel.is_set() and not nw.quitting


def test_close_during_a_harvest_yes_finishes_the_file_then_closes():
    """Yes = the queue's own cancel; the window stays until the safe step ends; never a force-kill."""
    harvest = _Job("harvest")
    nw = _nw(_Worker(harvest), ask=True)
    assert nw._on_closing() is False  # stays open while the current file finishes
    assert harvest.cancel.is_set() and nw.quitting
    time.sleep(0.5)
    assert not nw.window.destroyed  # still finishing
    assert any("__nlhClosing" in s for s in nw.window.scripts)  # the page shows the closing note
    assert nw._on_closing() is False  # pressing close again changes nothing
    harvest.state = "cancelled"  # the worker reaches its safe step
    for _ in range(30):
        if nw.window.destroyed:
            break
        time.sleep(0.1)
    assert nw.window.destroyed and nw.may_close
    assert nw._on_closing() is True


def test_focus_restores_and_raises_the_window():
    calls = []
    fake = types.SimpleNamespace(restore=lambda: calls.append("restore"), show=lambda: calls.append("show"))
    nw = _nw(_Worker())
    nw.window = fake
    assert nw.focus() is True and calls == ["restore", "show"]
    nw.window = None
    assert nw.focus() is False


def test_window_that_never_loads_falls_back(monkeypatch):
    monkeypatch.setattr(window, "LOAD_TIMEOUT_S", 0.1)
    nw = _nw(_Worker())
    nw._watchdog()
    assert nw.failed and nw.window.destroyed


# ---------------------------------------------------------------- server: focus, state, show, second start


@pytest.fixture
def client(ledger):
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    return app, app.test_client()


def test_focus_route(client):
    app, c = client
    assert c.post("/api/focus", headers=H).status_code == 409  # browser mode: no window
    assert c.post("/api/focus").status_code in (401, 403)  # the page key is required
    app.config["focus"] = lambda: True
    assert c.post("/api/focus", headers=H).get_json() == {"ok": True}


def test_state_tells_the_page_about_the_window(client):
    app, c = client
    st = c.get("/api/state", headers=H).get_json()
    assert st["app_window"] is False and st["window_note"] == ""
    app.config["APP_WINDOW"] = True
    app.config["WINDOW_NOTE"] = window.FALLBACK_NOTE
    st = c.get("/api/state", headers=H).get_json()
    assert st["app_window"] is True and st["window_note"] == window.FALLBACK_NOTE


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(server.webbrowser, "open", urls.append)
    monkeypatch.setattr(server.threading, "Timer", lambda t, fn: types.SimpleNamespace(start=fn))
    return urls


def _app_for_show(ledger):
    app = server.create_app(TOKEN, ledger)
    return app


def test_show_missing_webview2_opens_browser_with_one_line(ledger, opened, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(window, "webview2_version", lambda: None)
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    app = _app_for_show(ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert opened == ["http://127.0.0.1:5000/?t=x"] and app.config["WINDOW_NOTE"] == window.FALLBACK_NOTE


def test_show_library_missing_opens_browser_with_one_line(ledger, opened, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(window, "webview2_version", lambda: "157.0")
    monkeypatch.setattr(window, "load_webview", lambda: None)
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    app = _app_for_show(ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert opened and app.config["WINDOW_NOTE"] == window.FALLBACK_NOTE


def test_show_window_failing_at_run_time_opens_browser_with_one_line(ledger, opened, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(window, "webview2_version", lambda: "157.0")
    monkeypatch.setattr(window, "load_webview", lambda: object())
    monkeypatch.setattr(window.NativeWindow, "run", lambda self: False)
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    app = _app_for_show(ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert opened and app.config["WINDOW_NOTE"] == window.FALLBACK_NOTE
    assert not app.config.get("APP_WINDOW") and not app.config.get("focus")


def test_show_forced_browser_says_nothing(ledger, opened, monkeypatch):
    monkeypatch.setattr(server.sys, "argv", ["nlh", "--browser"])
    app = _app_for_show(ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert opened and not app.config.get("WINDOW_NOTE")


def test_show_window_runs_and_closes(ledger, opened, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(window, "webview2_version", lambda: "157.0")
    monkeypatch.setattr(window, "load_webview", lambda: object())
    monkeypatch.setattr(window.NativeWindow, "run", lambda self: True)
    monkeypatch.setattr(server.sys, "argv", ["nlh"])
    app = _app_for_show(ledger)
    server._show(app, {"open_in": "window"}, "http://127.0.0.1:5000/?t=x", 5000)
    assert opened == [] and app.config["WINDOW_CLOSED"] and app.config["APP_WINDOW"]


def _serve_in_thread(app):
    from werkzeug.serving import make_server

    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_second_start_focuses_the_running_window(home, ledger):
    app = server.create_app(TOKEN, ledger)
    focused = []
    app.config["focus"] = lambda: focused.append(1) or True
    srv = _serve_in_thread(app)
    try:
        (home / "server.json").parent.mkdir(parents=True, exist_ok=True)
        cfg = {"port": srv.server_port, "token": TOKEN}
        (home / "server.json").write_text(json.dumps(cfg), encoding="utf-8")
        assert server._focus_running() is True and focused == [1]
        app.config["focus"] = None  # browser mode: the second start opens the page instead
        assert server._focus_running() is False
    finally:
        srv.shutdown()
    assert server._focus_running() is False  # gone: never a silent success


# ---------------------------------------------------------------- leftover .part (ruling 2b)


def test_leftover_part_from_a_crash_is_never_renamed(tmp_path):
    """A .part left by a crash or a closed window counts as taken: a new save gets another name, and the
    leftover is never renamed into place. Cancel leaves none (test_cancel_leaves_no_part_and_no_row)."""
    from nlh import naming

    (tmp_path / "Weekly Review.mp3.part").write_bytes(b"half")
    assert naming.unique_path(tmp_path, "Weekly Review").name == "Weekly Review (2).mp3"
    assert (tmp_path / "Weekly Review.mp3.part").read_bytes() == b"half"
    assert not (tmp_path / "Weekly Review.mp3").exists()
