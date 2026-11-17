"""The search box placeholder is never cut mid word: the page picks the longest wording that fits the box
(it read "...accounts, fil" at the app window's first width, and "...notebook" at 320 px because Chromium
keeps room for its clear button). Checked at the smallest phone, the window's minimum, a tablet, the window's
first width and a wide screen."""

import os

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
WORDINGS = [
    "Search titles, notebooks, accounts, files",
    "Search titles, notebooks, accounts",
    "Search titles, notebooks",
    "Search",
]
FITS = """() => { const q = document.getElementById('q'); const cs = getComputedStyle(q);
  const c = document.createElement('canvas').getContext('2d');
  c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const room = q.getBoundingClientRect().width
    - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth)
    - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    - 4;  // the page's own margin; the browser's clear button is hidden and takes no room
  return {text: q.placeholder, width: c.measureText(q.placeholder).width, room}; }"""


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


@pytest.mark.parametrize("width", [320, 380, 768, 1280, 1440])
def test_placeholder_fits_and_ends_at_a_word(browser, server, width):
    ctx = browser.new_context(viewport={"width": width, "height": 800}, bypass_csp=True)
    page = ctx.new_page()
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_timeout(300)
    r = page.evaluate(FITS)
    assert r["text"] in WORDINGS, r
    assert r["text"] == "Search" or r["width"] <= r["room"], r
    if width >= 1440:
        assert r["text"] == WORDINGS[0]  # a wide screen shows the full wording
    if width == 1280:
        assert r["text"] != "Search"  # the window's first width has room for words (the old cut was at "fil")
    ctx.close()


def test_placeholder_follows_a_resize(browser, server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 800}, bypass_csp=True)
    page = ctx.new_page()
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    wide = page.evaluate(FITS)["text"]
    page.set_viewport_size({"width": 400, "height": 800})
    page.wait_for_timeout(400)
    narrow = page.evaluate(FITS)
    assert wide == WORDINGS[0] and narrow["text"] in WORDINGS[1:]
    assert narrow["text"] == "Search" or narrow["width"] <= narrow["room"]
    ctx.close()


@pytest.mark.parametrize("width", [320, 375, 380, 1280, 1440])
def test_no_browser_clear_button_takes_room(browser, server, width):
    """Only the app's clear button: the browser's own is hidden, so a long placeholder runs to the text edge.
    Measured from pixels (no browser reports where a placeholder is cut)."""
    import io

    from PIL import Image

    ctx = browser.new_context(
        viewport={"width": width, "height": 800}, device_scale_factor=1, bypass_csp=True
    )
    page = ctx.new_page()
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    page.evaluate(
        "() => { const q = document.getElementById('q'); q.placeholder = 'M'.repeat(200); q.blur(); }"
    )
    page.wait_for_timeout(200)
    content_end = page.evaluate(
        """() => { const q = document.getElementById('q'); const cs = getComputedStyle(q);
        const r = q.getBoundingClientRect();
        return r.width - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight); }"""
    )
    im = Image.open(io.BytesIO(page.locator("#q").screenshot())).convert("L")
    bg = im.getpixel((5, 4))
    end = 0
    for x in range(min(im.width, int(content_end) + 6)):
        if any(abs(im.getpixel((x, y)) - bg) > 40 for y in range(im.height // 4, im.height * 3 // 4)):
            end = x
    assert content_end - (end + 1) <= 3, (content_end, end)  # was about 14 px with the browser's button shown
    ctx.close()
