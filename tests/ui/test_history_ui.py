"""U2 in a real browser (demo mode): history paging, export, player, statistics, axe."""

import csv
import io
import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=2000) as srv:
        yield srv


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        kw = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
        if CHANNEL == "chrome":
            kw.update(channel="chrome")
            kw["args"] += ["--use-mock-keychain", "--password-store=basic"]
        try:
            b = p.chromium.launch(**kw)
        except Exception as exc:
            pytest.skip(f"no browser for UI tests: {exc}")
        yield b
        b.close()


def history_page(browser, server, width=1440):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url + "&v=history")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_function("window.__nlhTest && window.__nlhTest.history.total() > 0", timeout=30000)
    return page


def ids(page):
    return page.evaluate("window.__nlhTest.history.ids()")


def api_page(server, page, size=50, sort="saved_desc"):
    return [
        r["artifact_id"] for r in server.api(f"/api/history/page?page={page}&size={size}&sort={sort}")["rows"]
    ]


def test_history_first_middle_last_page(browser, server):
    page = history_page(browser, server)
    total = server.api("/api/history/page?size=50")["total"]
    pages = -(-total // 50)
    assert page.evaluate("window.__nlhTest.history.total()") == total
    assert ids(page) == api_page(server, 1)
    page.locator("#h-pager button", has_text="Next").click()
    page.wait_for_timeout(600)
    assert ids(page) == api_page(server, 2)
    page.fill("#h-pager input", str(pages))
    page.press("#h-pager input", "Enter")
    page.wait_for_timeout(600)
    assert ids(page) == api_page(server, pages)
    assert len(ids(page)) == total - 50 * (pages - 1)
    assert not page.errors
    page.context.close()


def test_history_page_size_is_remembered(browser, server):
    page = history_page(browser, server)
    page.select_option("#h-pager select", "100")
    page.wait_for_timeout(600)
    assert len(ids(page)) == 100
    assert server.api("/api/settings")["history_page_size"] == 100
    page.reload()
    page.wait_for_function("window.__nlhTest && window.__nlhTest.history.total() > 0")
    assert len(ids(page)) == 100
    page.select_option("#h-pager select", "50")
    page.context.close()


def test_phone_load_more(browser, server):
    page = history_page(browser, server, width=375)
    first = len(ids(page))
    page.locator("#h-pager button", has_text="Load more").click()
    page.wait_for_timeout(800)
    assert len(ids(page)) == first * 2
    assert ids(page) == api_page(server, 1, first) + api_page(server, 2, first)
    page.context.close()


def test_export_contains_every_filtered_row(browser, server):
    page = history_page(browser, server)
    page.fill("#view-history input[type=search]", "garden")
    page.wait_for_timeout(800)
    total = page.evaluate("window.__nlhTest.history.total()")
    resp = page.context.request.get(server.base + "/api/export.csv?q=garden")  # page cookie, not a header
    rows = list(csv.DictReader(io.StringIO(resp.text())))
    assert resp.status == 200 and len(rows) == total
    assert all(
        "garden" in (r["overview_title"] + r["notebook_title"] + r["saved_path"]).lower() for r in rows
    )
    page.context.close()


def test_player_streams_with_range_and_seeks(browser, server):
    page = history_page(browser, server)
    statuses = []
    page.on("response", lambda r: statuses.append(r.status) if "/api/play/" in r.url else None)
    page.locator(".hactions button[aria-label='Play']").first.click()
    page.wait_for_function(
        "window.__nlhTest.player().id !== null && !window.__nlhTest.player().paused", timeout=15000
    )
    pid = page.evaluate("window.__nlhTest.player().id")
    # Some Chromium builds do not report media range requests to the page listener, so ask
    # with the page's own cookie: the stream must honour Range.
    ranged = page.context.request.get(f"{server.base}/api/play/{pid}", headers={"Range": "bytes=0-99"})
    assert ranged.status == 206 and len(ranged.body()) == 100
    assert 206 in statuses or not statuses
    page.wait_for_timeout(800)
    page.locator("#player button[aria-label='Back 15 seconds']").click()
    assert page.evaluate("window.__nlhTest.player().time") < 1.5
    page.locator("#player button[aria-label='Close player']").click()
    assert page.locator("#player").is_hidden()
    page.context.close()


def test_stats_tiles_match_api(browser, server):
    page = history_page(browser, server)
    page.click(".tab[data-view='stats']")
    page.wait_for_selector(".tile")
    s = server.api("/api/stats")
    first = page.locator(".tile-v").first.inner_text().replace(",", "")
    assert first == str(s["totals"]["files"])
    assert page.locator(".hbar").count() >= 3
    page.context.close()


@pytest.mark.parametrize("view,width", [("history", 375), ("history", 1440), ("stats", 375), ("stats", 1440)])
def test_axe_history_and_stats(browser, server, view, width):
    page = history_page(browser, server, width=width)
    if view == "stats":
        page.click(".tab[data-view='stats']")
        page.wait_for_selector(".tile")
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    page.context.close()
