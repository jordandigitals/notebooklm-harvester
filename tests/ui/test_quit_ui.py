"""The page's Quit: in a browser the page says the harvester stopped and the tab can be closed; in the app
window it must never say "tab" (the window closes by itself; the text shows only if it cannot)."""

import json
import os

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
WINDOW_ANSWER = json.dumps({"ok": True, "window": True})


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        kw = {"headless": True}
        if CHANNEL == "chrome":
            kw.update(channel="chrome", args=["--use-mock-keychain", "--password-store=basic"])
        try:
            b = p.chromium.launch(**kw)
        except Exception as exc:
            pytest.skip(f"no browser for UI tests: {exc}")
        yield b
        b.close()


def _quit(page):
    page.click("#quit")
    page.locator("dialog[open] .btn-primary").click()


def test_quit_in_the_browser_says_close_this_tab(browser):
    with DemoServer(rows=3) as srv:  # its own server: this test really stops it
        ctx = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
        page = ctx.new_page()
        page.goto(srv.url)
        page.wait_for_selector("body[data-ready='1']")
        _quit(page)
        page.wait_for_selector("main.empty h2")
        text = page.locator("main.empty").inner_text()
        assert "The harvester has stopped" in text and "close this tab" in text
        ctx.close()


def test_quit_in_the_app_window_never_says_tab(browser):
    with DemoServer(rows=3) as srv:
        ctx = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
        page = ctx.new_page()
        # The window answer, without closing anything: what the page shows if the window cannot close.
        page.route(
            "**/api/quit",
            lambda r: r.fulfill(status=200, content_type="application/json", body=WINDOW_ANSWER),
        )
        page.goto(srv.url)
        page.wait_for_selector("body[data-ready='1']")
        _quit(page)
        page.wait_for_selector("main.empty h2")
        text = page.locator("main.empty").inner_text()
        assert "close this window" in text and "tab" not in text
        ctx.close()


def test_quit_refused_while_a_job_runs_keeps_the_page(browser):
    with DemoServer(rows=3) as srv:
        ctx = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
        page = ctx.new_page()
        msg = "A job is still running. Wait for it to finish, then quit."
        page.route(
            "**/api/quit",
            lambda r: r.fulfill(status=400, content_type="application/json", body=json.dumps({"error": msg})),
        )
        page.goto(srv.url)
        page.wait_for_selector("body[data-ready='1']")
        _quit(page)
        page.wait_for_selector(f"text={msg}")
        assert page.locator("main.empty").count() == 0  # the app stays as it was
        ctx.close()
