"""The Mac parts of the native app window (D-035): pywebview's Cocoa backend (WKWebView), made safe.

pywebview 6.2.1's Cocoa code has three defaults that break the window rules, so this module replaces
those methods on its delegate classes (objc.classAddMethods, the counterpart of the Windows hooks on
EdgeChrome):
- Navigation allows every address, and window.open is silently dropped: here anything that is not the
  app's own page is cancelled, and web addresses open in the default browser.
- A download (History export) is re-fetched outside the web view, without its cookie, so the server
  refuses it; and text files open inside the window. Here WebKit downloads them itself (cookie kept)
  straight into Downloads under a name that does not exist yet, and the page says where it went.
- Private mode wipes WebKit's data only at start; wipe_website_data() also runs at close.
Plus: Cmd+Q from the page goes through the quit rule (pywebview would stop the app outright), Cmd+R
reloads (as WebView2's own key does on Windows), the quit question is an NSAlert, and screen areas come
from NSScreen. Fail safe: if any guard cannot be installed, install_guards raises and the app opens in
the browser instead of an unguarded window.
pyobjc is imported inside the functions, so this module loads on every system (tests use fakes).
"""

from __future__ import annotations

import json
import platform
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from nlh.log import get_logger

log = get_logger()

MIN_MACOS = (11, 3)  # WKNavigationResponsePolicyDownload (the web view downloads with its own cookie)
POLICY_CANCEL, POLICY_ALLOW, POLICY_DOWNLOAD = 0, 1, 2  # WKNavigation{Action,Response}Policy values

# Every pywebview method replaced (class attribute -> selector name); a missing one means no window.
GUARDED = {
    "BrowserDelegate": [
        "webView_decidePolicyForNavigationAction_decisionHandler_",
        "webView_decidePolicyForNavigationResponse_decisionHandler_",
        "webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_",
    ],
    "DownloadDelegate": ["download_decideDestinationUsingResponse_suggestedFilename_completionHandler_"],
    "WebKitHost": ["keyDown_"],
}


class GuardError(RuntimeError):
    """A guard could not be installed: the window must not open (the browser is used instead)."""


# ---------------------------------------------------------------- pure parts (tested on every system)


def parse_version(text: str) -> tuple[int, ...]:
    """'11.3.1' -> (11, 3, 1); anything unreadable -> ()."""
    parts = []
    for p in str(text or "").split("."):
        if not p.isdigit():
            break
        parts.append(int(p))
    return tuple(parts)


def runtime_ok(version_text: str) -> str | None:
    """The macOS version if it can run the window (11.3 or later), else None."""
    v = parse_version(version_text)
    return version_text if v and v >= MIN_MACOS else None


def macos_version() -> str | None:
    """This Mac's version when it is new enough for the window, else None."""
    return runtime_ok(platform.mac_ver()[0])


def nav_decision(url: str, allowed) -> str:
    """'allow' for the app's own page, 'browser' for another web address, 'block' for anything else."""
    if allowed(url):
        return "allow"
    return "browser" if urlparse(url).scheme in ("http", "https") else "block"


def should_download(content_disposition: str, can_show: bool) -> bool:
    """WebKit should save this response instead of showing it (History export is an attachment)."""
    return "attachment" in (content_disposition or "").lower() or not can_show


def flip_screens(frames: list[tuple[float, ...]], primary_top: float) -> list[tuple[int, int, int, int]]:
    """Cocoa screen areas (origin bottom left) as (x, y, w, h) measured from the top of the primary
    screen (primary_top = its full frame's top edge), the way pywebview places windows."""
    return [(int(x), int(primary_top - (y + h)), int(w), int(h)) for x, y, w, h in frames]


# ---------------------------------------------------------------- Cocoa (Mac only)


def screens() -> list[tuple[int, int, int, int]]:
    """Visible areas of the attached screens (menu bar and Dock left out), in points, top-left based."""
    try:
        import AppKit

        all_screens = list(AppKit.NSScreen.screens())
        full0 = all_screens[0].frame()
        frames = []
        for scr in all_screens:
            f = scr.visibleFrame()
            frames.append((f.origin.x, f.origin.y, f.size.width, f.size.height))
        # flipped against the primary screen's full frame (not its visible part)
        return flip_screens(frames, full0.origin.y + full0.size.height)
    except Exception:
        return []


def _check(cocoa) -> None:
    """Every method we replace must exist in this pywebview, or no window opens."""
    view = getattr(cocoa, "BrowserView", None)
    if view is None or not hasattr(view, "instances"):
        raise GuardError("BrowserView")
    classes = {name: value for name, value in vars(view).items() if name in GUARDED}
    for cls_name, methods in GUARDED.items():
        cls = classes.get(cls_name)
        if cls is None:
            raise GuardError(cls_name)
        for m in methods:
            if not hasattr(cls, m):
                raise GuardError(f"{cls_name}.{m}")


_installed: dict = {}


def install_guards(cocoa, port: int, allowed, on_saved, downloads) -> None:
    """Replace pywebview's unsafe Cocoa methods. Raises GuardError if anything is missing or fails.

    allowed(url) says whether an address is the app's own page; on_saved(name) is told the file name
    of a finished download; downloads() is the folder downloads go to.
    """
    _check(cocoa)
    _installed.update(port=port, allowed=allowed, on_saved=on_saved, downloads=downloads)
    if _installed.get("done"):
        return  # replaced once per process; the callbacks above are read at call time
    try:
        import objc

        from nlh.window import unique_path

        view = cocoa.BrowserView
        bd, dd, host = view.BrowserDelegate, view.DownloadDelegate, view.WebKitHost
        orig_nav = bd.webView_decidePolicyForNavigationAction_decisionHandler_.callable
        orig_resp = bd.webView_decidePolicyForNavigationResponse_decisionHandler_.callable
        orig_key = host.keyDown_.callable

        def nav(self, webview, action, handler):
            url = str(action.request().URL().absoluteString())
            what = nav_decision(url, _installed["allowed"])
            if what == "allow":
                return orig_nav(self, webview, action, handler)
            _outside(url, what)
            handler(POLICY_CANCEL)
            return None

        def popup(self, webview, config, action, features):
            # target="_blank" links and window.open: never a second window; web addresses go to the browser
            url = str(action.request().URL().absoluteString())
            what = nav_decision(url, _installed["allowed"])
            _outside(url, "block" if what == "allow" else what)
            return None

        def response(self, webview, nav_response, handler):
            r = nav_response.response()
            try:
                cd = str(r.allHeaderFields().get("Content-Disposition") or "")
            except Exception:
                cd = ""
            if should_download(cd, bool(nav_response.canShowMIMEType())):
                handler(POLICY_DOWNLOAD)  # WebKit downloads it with the web view's own cookie
                return None
            return orig_resp(self, webview, nav_response, handler)

        def became_download(self, webview, nav_response, download):
            download.setDelegate_(view.DownloadDelegate.alloc().init().retain())

        def destination(self, download, resp, suggested, completion):
            import Foundation

            target = unique_path(Path(_installed["downloads"]()), str(suggested or "download"))
            log.info("app window: download saved as %s", target.name)
            completion(Foundation.NSURL.fileURLWithPath_(str(target)))
            threading.Timer(1.0, lambda: _installed["on_saved"](target.name)).start()

        def key_down(self, event):
            import AppKit

            if event.modifierFlags() & AppKit.NSEventModifierFlagCommand:
                ch = str(event.charactersIgnoringModifiers() or "").lower()
                if ch == "q":  # through the quit rule, never pywebview's outright stop
                    self.window().performClose_(None)
                    return None
                if ch == "r":  # reload, as on Windows
                    self.reload_(None)
                    return None
            return orig_key(self, event)

        def replace(cls, sel, fn):
            objc.classAddMethods(cls, [objc.selector(fn, selector=sel.selector, signature=sel.signature)])

        replace(bd, bd.webView_decidePolicyForNavigationAction_decisionHandler_, nav)
        replace(bd, bd.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_, popup)
        replace(bd, bd.webView_decidePolicyForNavigationResponse_decisionHandler_, response)
        replace(
            dd, dd.download_decideDestinationUsingResponse_suggestedFilename_completionHandler_, destination
        )
        replace(host, host.keyDown_, key_down)
        objc.classAddMethods(
            bd,
            [
                objc.selector(
                    became_download,
                    selector=b"webView:navigationResponse:didBecomeDownload:",
                    signature=b"v@:@@@",
                )
            ],
        )
        _installed["done"] = True
    except GuardError:
        raise
    except Exception as exc:
        raise GuardError(type(exc).__name__) from exc


def _outside(url: str, what: str) -> None:
    parsed = urlparse(url)
    if what == "browser":
        log.info("app window: outside address opened in the browser (%s)", parsed.hostname)
        webbrowser.open(url)
    else:
        log.info("app window: blocked a %s address", parsed.scheme or "blank")


def saved_script(text: str) -> str:
    """The page call that shows the download toast (the text is a JSON string, never raw)."""
    return f"window.__nlhSaved && window.__nlhSaved({json.dumps(text)})"


def run_js(pywebview_window, script: str, timeout_s: float = 5.0):
    """Run script in the page with WebKit's own call and return its result (None on error).

    pywebview's Cocoa evaluate_js wraps the script in eval(), which the app's Content Security Policy
    (script-src 'self') refuses; WebKit's evaluateJavaScript is not subject to the page's policy."""
    import webview.platforms.cocoa as cocoa
    from PyObjCTools import AppHelper

    done, box = threading.Event(), {}

    def handler(result, error):
        box["v"] = None if error is not None else result
        done.set()

    def run():
        try:
            wk = cocoa.BrowserView.instances[pywebview_window.uid].webview
            wk.evaluateJavaScript_completionHandler_(script, handler)
        except Exception:
            done.set()

    AppHelper.callAfter(run)
    done.wait(timeout_s)
    return box.get("v")


def _spin_until(done: threading.Event, seconds: float) -> None:
    """Let the main run loop deliver WebKit's answer (the GUI loop has already stopped)."""
    import Foundation

    loop = Foundation.NSRunLoop.currentRunLoop()
    end = time.monotonic() + seconds
    while not done.is_set() and time.monotonic() < end:
        loop.runMode_beforeDate_(
            Foundation.NSDefaultRunLoopMode, Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05)
        )


def wipe_website_data(timeout_s: float = 5.0) -> bool:
    """Remove this app's WebKit data (cookies, storage, cache) from its own data store, on the main
    thread after the window closed. WebKit keeps one default store per app, so this never touches
    Safari's or any other app's data. True when WebKit confirmed it."""
    try:
        import WebKit

        done = threading.Event()
        store = WebKit.WKWebsiteDataStore.defaultDataStore()
        store.removeDataOfTypes_modifiedSince_completionHandler_(
            WebKit.WKWebsiteDataStore.allWebsiteDataTypes(),
            WebKit.NSDate.dateWithTimeIntervalSince1970_(0),
            done.set,
        )
        _spin_until(done, timeout_s)
        if not done.is_set():
            log.info("app window: WebKit did not confirm the data wipe in %.0f s", timeout_s)
        return done.is_set()
    except Exception as exc:
        log.info("app window: data wipe failed: %s", type(exc).__name__)
        return False


def keep_working(reason: str):
    """Tell macOS the app is doing work the person started, so App Nap does not slow the server and the
    harvest while the window is behind other windows (measured: up to 3.3 s main-thread stalls without
    it, under 25 ms with it). Idle system sleep stays allowed. Returns a token for end_working()."""
    try:
        import Foundation

        opts = Foundation.NSActivityUserInitiatedAllowingIdleSystemSleep
        return Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(opts, reason)
    except Exception as exc:
        log.info("app window: could not keep App Nap away: %s", type(exc).__name__)
        return None


def end_working(token) -> None:
    """End keep_working()."""
    if token is None:
        return
    try:
        import Foundation

        Foundation.NSProcessInfo.processInfo().endActivity_(token)
    except Exception:
        pass


def ask_quit(question: str, title: str) -> bool:
    """'Quit' (default, Return) or 'Cancel' (Escape), on the main thread."""
    import AppKit

    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(question)
    alert.setInformativeText_(title)
    alert.setAlertStyle_(AppKit.NSAlertStyleWarning)
    alert.addButtonWithTitle_("Quit")
    cancel = alert.addButtonWithTitle_("Cancel")
    cancel.setKeyEquivalent_("\x1b")
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    return alert.runModal() == AppKit.NSAlertFirstButtonReturn


def raise_window(nswindow) -> bool:
    """Restore a minimized window and bring the app to the front (on the main thread, waited for)."""
    from PyObjCTools import AppHelper

    done, box = threading.Event(), {"ok": False}

    def run():
        try:
            import AppKit

            if nswindow.isMiniaturized():
                nswindow.deminiaturize_(None)
            nswindow.makeKeyAndOrderFront_(None)
            app = AppKit.NSApp
            if hasattr(app, "activate"):  # macOS 14 and later
                app.activate()
            app.activateIgnoringOtherApps_(True)
            box["ok"] = True
        finally:
            done.set()

    AppHelper.callAfter(run)
    done.wait(5)
    return box["ok"]


def yield_activation(bundle_id: str | None) -> None:
    """A second start hands macOS 14+'s activation to the running copy, so it may come forward."""
    if not bundle_id:
        return
    try:
        import os

        import AppKit

        me = os.getpid()
        apps = [
            a
            for a in AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
            if a.processIdentifier() != me
        ]
        app = AppKit.NSApplication.sharedApplication()
        if apps and hasattr(app, "yieldActivationToApplication_"):
            app.yieldActivationToApplication_(apps[0])
    except Exception as exc:
        log.info("app window: could not hand over activation: %s", type(exc).__name__)


def bundle_id() -> str | None:
    """This app's bundle id when it runs as the packaged .app (None from source)."""
    try:
        import Foundation

        bid = Foundation.NSBundle.mainBundle().bundleIdentifier()
        return str(bid) if bid and "notebooklm-harvester" in str(bid) else None
    except Exception:
        return None


def message_box(text: str, title: str) -> bool:
    """A plain Mac message (a second start that can neither focus nor open the app)."""
    try:
        import AppKit

        AppKit.NSApplication.sharedApplication()
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(text)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        alert.runModal()
        return True
    except Exception:
        return False
