"""U4 in a real browser (demo mode): every view in light and dark at 375, 768, 1024 and 1440
through axe-core, first paint and layout shift, and Chat's U2 polish nits."""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
VIEWS = {
    "library": "",
    "history": "v=history",
    "studio": "v=studio",
    "stats": "v=stats",
    "settings": "v=settings",
}
READY = {
    "library": "#vspacer .vrow",
    "history": ".htable, .hcard",
    "studio": "#studio-account",
    "stats": ".tile",
    "settings": ".set-card",
}


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=800) as srv:
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


OPEN = []


@pytest.fixture(autouse=True)
def close_contexts():
    """Close every context a test opened, even when it fails (keeps the shared server light)."""
    yield
    while OPEN:
        try:
            OPEN.pop().close()
        except Exception:
            pass


def open_view(browser, server, view, width=1440, scheme="light", query=""):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, bypass_csp=True)
    OPEN.append(ctx)
    page = ctx.new_page()
    q = "&".join(x for x in (VIEWS[view], query) if x)
    page.goto(server.url + ("&" + q if q else ""))
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector(READY[view], timeout=30000)
    page.wait_for_timeout(300)
    return page


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
@pytest.mark.parametrize("view", list(VIEWS))
def test_axe_every_view(browser, server, view, width, scheme):
    page = open_view(browser, server, view, width, scheme)
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    assert not overflow
    page.context.close()


def test_first_paint_and_no_layout_shift(browser, server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    page.add_init_script(
        "window.__cls = 0; new PerformanceObserver(l => { for (const e of l.getEntries())"
        " if (!e.hadRecentInput) window.__cls += e.value; }).observe({type: 'layout-shift', buffered: true});"
    )
    page.goto(server.url)
    page.wait_for_selector("#vspacer .vrow")
    page.wait_for_timeout(1500)
    fcp = page.evaluate("performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0")
    cls = page.evaluate("window.__cls")
    print(f"\nfirst contentful paint {fcp:.0f} ms, layout shift {cls:.3f}")
    assert 0 < fcp < (2000 if os.environ.get("CI") else 1000)
    assert cls < 0.1
    ctx.close()


def test_history_cards_have_no_full_path(browser, server):
    page = open_view(browser, server, "history", 375)
    cards = page.locator(".hcard")
    assert cards.count() > 0
    assert page.locator(".hcard .hp").count() == 0  # no monospace full path on phone cards
    assert page.locator(".hcard .folder-line").count() == cards.count()
    page.context.close()


def test_history_actions_fixed_order_one_row(browser, server):
    page = open_view(browser, server, "history", 1440)
    rows = page.locator(".htable tbody tr")
    for i in range(min(rows.count(), 12)):
        buttons = rows.nth(i).locator(".hactions > button")
        labels = [buttons.nth(k).get_attribute("aria-label") for k in range(buttons.count())]
        assert labels[1:] == [labels[1], "Copy path", "More actions"] and len(labels) == 4
        tops = {round(buttons.nth(k).bounding_box()["y"]) for k in range(buttons.count())}
        assert len(tops) == 1  # one line, no wrapping
    # file name line only where it differs from "<title>.mp3"
    for i in range(min(rows.count(), 30)):
        cell = rows.nth(i).locator("td.tt")
        title = cell.locator(".ht").inner_text()
        if cell.locator(".hp").count():
            assert cell.locator(".hp").inner_text() != f"{title}.mp3"
    page.context.close()


def test_stats_two_columns_wide_one_column_phone(browser, server):
    page = open_view(browser, server, "stats", 1440)
    cards = page.locator(".stats-grid > .chart-card")
    a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
    assert abs(a["y"] - b["y"]) < 2 and b["x"] > a["x"]  # side by side
    assert "files" in page.locator(".chart-card h2").first.inner_text()
    page.context.close()
    page = open_view(browser, server, "stats", 375)
    cards = page.locator(".stats-grid > .chart-card")
    a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
    assert b["y"] > a["y"] + a["height"] - 1  # stacked
    page.context.close()


def test_group_headers_highlight_search(browser, server):
    data = server.api("/api/library")
    word = data["rows"][0]["notebook"].split()[0].lower()
    page = open_view(browser, server, "library", 1440, query=f"group=notebook&q={word}")
    assert page.locator(".vgroup mark").count() > 0
    page.context.close()


def test_selection_controls_look_like_buttons(browser, server):
    page = open_view(browser, server, "library", 1440)
    for bid in ("sel-visible", "sel-new", "sel-invert"):
        border = page.eval_on_selector(
            f"#{bid}", "e => getComputedStyle(e).borderTopStyle + ' ' + getComputedStyle(e).borderTopWidth"
        )
        assert border.startswith("solid") and not border.endswith(" 0px")
    page.context.close()


def test_phone_account_card_is_compact(browser, server):
    page = open_view(browser, server, "library", 375)
    assert page.locator(".card-notes").is_hidden()
    toggle = page.locator(".notes-toggle")
    if toggle.count():
        toggle.click()
        assert page.locator(".card-notes").is_visible()
    first_row_top = page.locator("#vspacer .vrow").first.bounding_box()["y"]
    assert first_row_top < 900 * 1.2  # the list starts within about one screen
    page.context.close()
