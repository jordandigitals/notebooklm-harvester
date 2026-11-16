"""D-030 in a real browser (demo mode, invented profiles): the Chrome route lists the profiles on
this computer with email and name, has Refresh, says plainly when there are none, and stays
axe clean with 40 px touch targets at four widths in light and dark."""

import json
import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


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


def open_add(browser, server, width=1440, scheme="light", touch=False, empty=False):
    ctx = browser.new_context(
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        bypass_csp=True,
        has_touch=touch,
        is_mobile=touch,
    )
    page = ctx.new_page()
    if empty:
        page.route(
            "**/api/chrome",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"chrome": True, "profiles": []})
            ),
        )
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#vspacer .vrow", timeout=30000)
    if page.locator("#sidebar-open").is_visible():
        page.click("#sidebar-open")
    page.click("#add-toggle")
    if page.locator("#chrome-toggle").is_visible():  # D-029: the Chrome route sits behind this button
        page.click("#chrome-toggle")
    ready = "#chrome-list .chrome-item, #chrome-list p:not(:has-text('Looking'))"
    page.wait_for_selector(ready, timeout=20000)
    return page


def test_list_shows_email_and_name_and_added(browser, server):
    page = open_add(browser, server)
    rows = page.locator("#chrome-list .chrome-item")
    texts = rows.all_inner_texts()
    assert any("demo.side@example.com" in t and "Side project" in t for t in texts)
    added = page.locator("#chrome-list button:has-text('Added')")
    assert added.count() >= 1 and added.first.is_disabled()
    page.click("#chrome-refresh")
    page.wait_for_selector("#chrome-list .chrome-item")
    assert rows.count() == len(texts)
    page.context.close()


def test_empty_list_says_so(browser, server):
    page = open_add(browser, server, empty=True)
    msg = page.inner_text("#chrome-list")
    assert "No Chrome profile on this computer is signed in to Google" in msg and "Refresh" in msg
    # The own sign-in block sits above the Chrome block, and the hint says so (D-029).
    assert 'Or use "Continue with Google" above.' in msg
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
def test_add_panel_axe_clean(browser, server, width, scheme):
    page = open_add(browser, server, width, scheme)
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
    hs = page.evaluate(
        "() => [...document.querySelectorAll('#add-panel button, #add-panel input')]"
        ".filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height)"
    )
    assert hs and all(h >= 40 for h in hs), hs
    page.context.close()
