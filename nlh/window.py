"""Native app window (D-035): the app's own local page in the system web view, with a browser fallback.

A thin shell. The app stays the same local web page served by Flask; on Windows a pywebview window shows
it through Microsoft's WebView2 runtime instead of a browser tab. Rules:
- The window shows only http://127.0.0.1:<port>. Any other address opens in the default browser and never
  inside the window. Google sign-in stays in its own Chrome or Edge window (D-029).
- If WebView2 is missing, the library does not load, or the window fails to open, the app opens in the
  default browser exactly as before, and the page says so in one plain line. NLH_BROWSER=1, --browser, or
  the Settings choice "Open the app in: Browser" force the browser.
- Closing the window quits the app. A running harvest asks first; Yes stops it after the current safe step
  (never a force-kill), and the window says "Finishing the current file, then closing." until then.
- A second start brings this window to the front (POST /api/focus).
Every child process still goes through nlh.procs (D-031); this module starts none.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from nlh import APP_NAME
from nlh.log import get_logger

log = get_logger()

WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # Microsoft's id for the Evergreen runtime
FALLBACK_NOTE = "The app window could not open on this computer, so the app opened in your browser."
CLOSING_NOTE = "Finishing the current file, then closing."
QUIT_QUESTION = "A harvest is running. Quit anyway?"
NOTIFY_NOTE = "Notifications work when the app is open in your browser."
MIN_SIZE = (380, 560)
DEFAULT_SIZE = (1280, 820)
LOAD_TIMEOUT_S = 25
TITLE = APP_NAME


# ---------------------------------------------------------------- the decision (pure, tested)


def webview2_version() -> str | None:
    """Installed WebView2 runtime version from Microsoft's documented registry keys, or None."""
    if sys.platform != "win32":
        return None
    import winreg

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
    ]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                value = str(winreg.QueryValueEx(k, "pv")[0]).strip()
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return value
    return None


def forced_browser(argv: list[str], env: dict, open_in: str) -> bool:
    """True when the person (or a test run) asked for the browser instead of the window."""
    return "--browser" in argv or env.get("NLH_BROWSER") == "1" or open_in == "browser"


def decide(*, platform: str, forced: bool, webview2: str | None, importable: bool) -> str | None:
    """None to open the window, else why not: forced, not_windows, no_webview2 or no_library.

    Only no_webview2 and no_library (and a window that fails at run time) are fallbacks that the page
    reports; forced and not_windows are today's browser behaviour on purpose.
    """
    if forced:
        return "forced"
    if platform != "win32":
        return "not_windows"
    if not webview2:
        return "no_webview2"
    if not importable:
        return "no_library"
    return None


def is_fallback(reason: str | None) -> bool:
    """Whether the page should say that the window could not open."""
    return reason in ("no_webview2", "no_library", "failed")


def load_webview():
    """The pywebview module with its WebView2 backend loaded, or None if that fails (logged by class)."""
    try:
        webview = importlib.import_module("webview")
        importlib.import_module("webview.platforms.edgechromium")  # loads .NET and the WebView2 interop
        return webview
    except Exception as exc:  # any import or .NET load problem means: use the browser
        log.info("app window library not available: %s", type(exc).__name__)
        return None


def allowed_url(url: str, port: int) -> bool:
    """Only the app's own page may load inside the window."""
    if url in ("about:blank", ""):
        return True
    p = urlparse(url)
    return p.scheme == "http" and p.hostname == "127.0.0.1" and p.port == port


def unique_path(folder: Path, name: str) -> Path:
    """A new file name in folder: name, then "name (2)", "name (3)"... (never overwrites)."""
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1] or "download"  # either separator, any system
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = folder / name
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def downloads_folder() -> Path:
    """The person's Downloads folder (Windows shell folder setting, else ~/Downloads)."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            ) as k:
                return Path(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
        except OSError:
            pass
    return Path.home() / "Downloads"


def clamp_bounds(bounds, screens: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    """Saved (x, y, width, height) if its top bar is on a screen and its size is sane, else None."""
    try:
        x, y, w, h = (int(v) for v in bounds)
    except (TypeError, ValueError):
        return None
    w, h = max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1])
    for sx, sy, sw, sh in screens:
        fw, fh = min(w, sw), min(h, sh)  # never larger than the screen
        # the middle of the title bar must be on this screen, so the window can be grabbed
        if sx <= x + fw // 2 <= sx + sw and sy <= y + 20 <= sy + sh:
            return x, y, fw, fh
    return None


def _scale() -> float:
    """Display scale (1.25 at 125 %); pywebview sizes and positions are in these logical pixels."""
    try:
        return max(ctypes.windll.user32.GetDpiForSystem() / 96.0, 1.0)
    except Exception:
        return 1.0


def first_size(work: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """The window's place on its first start: DEFAULT_SIZE, shrunk to fit the first screen's work area
    (with a margin, so it is never under the taskbar), and centred."""
    if not work:  # screen sizes unknown: the plain default
        return 100, 100, *DEFAULT_SIZE
    sx, sy, sw, sh = work[0]
    w = max(MIN_SIZE[0], min(DEFAULT_SIZE[0], int(sw * 0.92)))
    h = max(MIN_SIZE[1], min(DEFAULT_SIZE[1], int(sh * 0.92)))
    return sx + max(0, (sw - w) // 2), sy + max(0, (sh - h) // 2), w, h


def screens() -> list[tuple[int, int, int, int]]:
    """Work areas of the attached screens (Windows), as (x, y, width, height) in logical pixels, the same
    units pywebview uses (the primary screen first)."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    found = []
    proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def cb(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            area = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            if mi.dwFlags & 1:  # MONITORINFOF_PRIMARY
                found.insert(0, area)
            else:
                found.append(area)
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    k = _scale()
    return [(int(x / k), int(y / k), int(w / k), int(h / k)) for x, y, w, h in found]


def allow_focus_from_other_process() -> None:
    """Let the running app bring its window to the front when this (second) start asks it to."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass


def message_box(text: str) -> None:
    """A plain Windows message (used when a second start can neither focus nor open the app)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40 | 0x40000)  # info icon, topmost
            return
        except Exception:
            pass
    print(text)


# ---------------------------------------------------------------- the window


def own_window_handle() -> int | None:
    """Handle of this process's app window (by process and title), or None."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == TITLE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def raise_window(hwnd: int) -> None:
    """Restore a minimized window and bring it to the front (Windows allows it after the second start
    called AllowSetForegroundWindow)."""
    user32 = ctypes.windll.user32
    sw_restore, sw_show = 9, 5
    user32.ShowWindow(hwnd, sw_restore if user32.IsIconic(hwnd) else sw_show)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)


def install_guards(edgechromium, port: int) -> None:
    """Hook the WebView2 backend's own handlers (pywebview 6.2.1, pinned; tests check the names exist).

    - Navigation: anything that is not the app's own page is cancelled and, if it is a web address,
      opened in the default browser. Covers clicked links, redirects and script navigation.
    - Downloads: saved straight into Downloads under a name that does not exist yet ("name (2).csv"),
      so a download never replaces a file there.
    """
    edge = edgechromium.EdgeChrome
    if getattr(edge, "_nlh_guarded", False):
        return
    original_nav = edge.on_navigation_start

    def on_navigation_start(self, sender, args):
        uri = str(args.Uri)
        if not allowed_url(uri, port):
            args.Cancel = True
            parsed = urlparse(uri)
            log.info(
                "app window: outside address opened in the browser (%s)", parsed.hostname or parsed.scheme
            )
            if parsed.scheme in ("http", "https"):
                webbrowser.open(uri)
            return None
        return original_nav(self, sender, args)

    def on_download_starting(self, sender, args):
        target = unique_path(downloads_folder(), str(args.ResultFilePath))
        args.ResultFilePath = str(target)
        log.info("app window: download saved as %s", target.name)

    edge.on_navigation_start = on_navigation_start
    edge.on_download_starting = on_download_starting
    edge._nlh_guarded = True


class NativeWindow:
    """One app window; run() blocks the main thread until it closes."""

    def __init__(self, webview, url: str, port: int, worker, bounds, save_bounds):
        self.webview = webview
        self.url = url
        self.port = port
        self.worker = worker
        self.bounds = clamp_bounds(bounds, screens()) if bounds else None
        self.save_bounds = save_bounds
        self.window = None
        self.loaded = threading.Event()
        self.failed = False
        self.quitting = False
        self.may_close = False

    # -- events

    def _remember(self, *_a):
        w = self.window
        try:
            if w and w.width and w.height and w.x > -10000 and w.y > -10000:  # not while minimized
                self.bounds = (w.x, w.y, w.width, w.height)
        except Exception:
            pass

    def harvest_running(self) -> bool:
        """A harvest (not a refresh or check) is queued or running."""
        return any(
            self.worker.jobs[j].kind == "harvest" and self.worker.jobs[j].state in ("queued", "running")
            for j in self.worker.order
        )

    def _cancel_all(self) -> None:
        """The same cancel the queue's Stop uses: each job stops after its current safe step."""
        for j in list(self.worker.order):
            job = self.worker.jobs[j]
            if job.state in ("queued", "running"):
                job.cancel.set()

    def _ask_quit(self) -> bool:
        """Yes/No question owned by the window (runs on the window's thread)."""
        hwnd = 0
        try:
            hwnd = int(self.window.native.Handle.ToInt64())
        except Exception:
            pass
        mb_yesno, mb_question, idyes = 0x4, 0x20, 6
        return ctypes.windll.user32.MessageBoxW(hwnd, QUIT_QUESTION, TITLE, mb_yesno | mb_question) == idyes

    def _on_closing(self):
        """Window close asked. False keeps the window open."""
        if self.may_close or self.failed:
            return True
        if self.quitting:
            return False  # already finishing the current file
        if self.harvest_running():
            if not self._ask_quit():
                return False
            self.quitting = True
            self._cancel_all()
            threading.Thread(target=self._finish_then_close, name="nlh-quit", daemon=True).start()
            return False
        self._cancel_all()  # a refresh or check simply ends with the app
        self.may_close = True
        return True

    def _finish_then_close(self):
        """Show the closing note, wait for the worker's safe step (never a force-kill), then close."""
        try:
            self.window.evaluate_js("window.__nlhClosing && window.__nlhClosing()")
        except Exception:
            pass
        while self.harvest_running():
            time.sleep(0.3)
        self.may_close = True
        try:
            self.window.destroy()
        except Exception:
            pass

    def _watchdog(self):
        """If the page never loads (WebView2 failed to start), close and fall back to the browser."""
        if not self.loaded.wait(LOAD_TIMEOUT_S):
            log.info("app window: the page did not load in %d s; using the browser", LOAD_TIMEOUT_S)
            self.failed = True
            try:
                self.window.destroy()
            except Exception:
                pass

    # -- public

    def focus(self) -> bool:
        """Bring the window to the front, restoring it if minimized. False if there is no window.

        Uses the Windows calls on the window's own handle: pywebview's restore() waits for the page,
        which does not load while the window is minimized, so a second start could time out.
        """
        if not self.window or self.failed:
            return False
        hwnd = own_window_handle()
        if not hwnd:
            return False
        try:
            raise_window(hwnd)
            return True
        except Exception as exc:
            log.info("app window: focus failed: %s", type(exc).__name__)
            return False

    def run(self) -> bool:
        """Open the window and block until it closes. False if it could not open (use the browser)."""
        try:
            install_guards(importlib.import_module("webview.platforms.edgechromium"), self.port)
            x, y, w, h = first_size(screens())
            kw = {"x": x, "y": y, "width": w, "height": h}
            if self.bounds:
                kw = {
                    "x": self.bounds[0],
                    "y": self.bounds[1],
                    "width": self.bounds[2],
                    "height": self.bounds[3],
                }
            self.window = self.webview.create_window(TITLE, self.url, min_size=MIN_SIZE, **kw)
            self.window.events.closing += self._on_closing
            self.window.events.loaded += lambda: self.loaded.set()
            self.window.events.moved += self._remember
            self.window.events.resized += self._remember
            threading.Thread(target=self._watchdog, name="nlh-window-watchdog", daemon=True).start()
            self.webview.start(gui="edgechromium", private_mode=True)
        except Exception as exc:
            log.info("app window could not open: %s", type(exc).__name__)
            self.failed = True
        if self.bounds and not self.failed:
            try:
                self.save_bounds(list(self.bounds))
            except Exception:
                pass
        self.window = None
        return not self.failed
