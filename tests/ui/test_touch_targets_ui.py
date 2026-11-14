"""Small buttons grow to 40 px only on coarse pointers (touch); mouse layouts keep 32 px."""

import os

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=200) as srv:
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


def sizes(browser, server, touch):
    ctx = browser.new_context(viewport={"width": 390, "height": 900}, has_touch=touch, is_mobile=touch)
    page = ctx.new_page()
    page.goto(server.url + "&v=history")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector(".hcard .btn-sm, .htable .btn-sm")
    out = page.evaluate(
        "() => ({coarse: matchMedia('(pointer: coarse)').matches,"
        " sm: [...document.querySelectorAll('.btn-sm:not(.btn-icon)')].filter(e => e.offsetParent)"
        ".map(e => e.getBoundingClientRect().height),"
        " icon: [...document.querySelectorAll('.btn-icon.btn-sm')].filter(e => e.offsetParent)"
        ".map(e => [e.getBoundingClientRect().width, e.getBoundingClientRect().height])})"
    )
    ctx.close()
    return out


def test_touch_grows_small_buttons_to_40px(browser, server):
    s = sizes(browser, server, touch=True)
    assert s["coarse"] is True
    assert s["icon"] and all(w >= 40 and h >= 40 for w, h in s["icon"])
    assert all(h >= 40 for h in s["sm"])


def test_mouse_keeps_32px(browser, server):
    s = sizes(browser, server, touch=False)
    assert s["coarse"] is False
    assert s["icon"] and all(round(w) == 32 for w, _h in s["icon"])
