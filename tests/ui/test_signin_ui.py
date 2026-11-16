"""D-029 in a real browser (demo mode): Continue with Google is first and primary on every
system, the Chrome route opens from "Use a Chrome profile instead", and with neither Chrome nor
Edge the app says so plainly under the button."""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
HELPER = "A Google window opens here. The app never sees your password."


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=50) as srv:
        yield srv


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


def open_add(browser, server, width=1440, scheme="light", touch=False):
    ctx = browser.new_context(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        bypass_csp=True,
        has_touch=touch,
        is_mobile=touch,
    )
    page = ctx.new_page()
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#vspacer .vrow", timeout=30000)
    if page.locator("#sidebar-open").is_visible():
        page.click("#sidebar-open")
    page.click("#add-toggle")
    page.wait_for_selector("#signin-add")
    return page


def test_continue_with_google_is_first_and_primary(browser, server):
    page = open_add(browser, server)
    first = page.evaluate("document.querySelector('#add-panel').firstElementChild.id")
    assert first == "add-signin"
    btn = page.locator("#signin-add")
    assert btn.inner_text().strip() == "Continue with Google"
    assert "btn-primary" in btn.get_attribute("class")
    assert page.inner_text("#signin-helper") == HELPER
    assert page.locator("#add-chrome").is_hidden()
    assert page.inner_text("#chrome-toggle").strip() == "Use a Chrome profile instead"
    page.context.close()


def test_chrome_route_opens_behind_the_button(browser, server):
    page = open_add(browser, server)
    page.click("#chrome-toggle")
    page.wait_for_selector("#chrome-list .chrome-item")
    assert page.get_attribute("#chrome-toggle", "aria-expanded") == "true"
    assert page.locator("#add-chrome").is_visible()
    page.context.close()


def test_continue_with_google_adds_in_demo(browser, server):
    page = open_add(browser, server)
    page.click("#signin-add")
    page.wait_for_selector("#acct-list >> text=demo.new@example.com", timeout=20000)
    page.context.close()


def test_no_chrome_or_edge_says_so(browser, monkeypatch):
    monkeypatch.setenv("NLH_DEMO_NO_BROWSER", "1")
    with DemoServer(rows=20) as srv:
        page = open_add(browser, srv)
        page.click("#signin-add")
        page.wait_for_function("() => document.querySelector('#signin-msg').textContent.length > 0")
        assert page.inner_text("#signin-msg") == (
            "This needs Google Chrome or Microsoft Edge on this computer. Install one, then try again."
        )
        assert page.locator("#signin-add").is_visible()
        page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
def test_add_panel_axe_clean(browser, server, width, scheme):
    page = open_add(browser, server, width, scheme)
    page.click("#chrome-toggle")
    page.wait_for_selector("#chrome-list .chrome-item")
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run('#sidebar', {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    page.context.close()


def test_add_panel_touch_targets_40px(browser, server):
    page = open_add(browser, server, 390, touch=True)
    page.click("#chrome-toggle")
    page.wait_for_selector("#chrome-list .chrome-item")
    hs = page.evaluate(
        "() => [...document.querySelectorAll('#add-panel button, #add-panel input')]"
        ".filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height)"
    )
    assert hs and all(h >= 40 for h in hs), hs
    page.context.close()
