"""P2-B "Shared with me" in a real browser (demo mode, synthetic editor and viewer shares):
badge, Owner facet counts, shared: prefix, Select new skipping shared, Studio view-only rows,
History owner filter, axe."""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=600) as srv:
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


def page_at(browser, server, query="", width=1440):
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, bypass_csp=True)
    pg = ctx.new_page()
    pg.goto(server.url + (("&" + query) if query else ""))
    pg.wait_for_selector("body[data-ready='1']")
    return pg


def test_owner_filter_counts_and_badge(browser, server):
    rows = server.api("/api/library")["rows"]
    shared = {r["artifact_id"] for r in rows if r["shared"]}
    assert shared and len(shared) < len(rows)  # demo has both kinds
    pg = page_at(browser, server, "own=shared")
    pg.wait_for_selector("#vspacer .vrow")
    assert set(pg.evaluate("window.__nlhTest.filteredIds()")) == shared
    assert pg.locator("#vspacer .vrow .rbadge-shared").count() == pg.locator("#vspacer .vrow").count()
    assert "Shared with me" in pg.inner_text("#chips")
    pg.goto(server.url + "&q=shared:yes")
    pg.wait_for_selector("#vspacer .vrow")
    assert set(pg.evaluate("window.__nlhTest.filteredIds()")) == shared
    pg.goto(server.url + "&q=shared:no")
    pg.wait_for_selector("#vspacer .vrow")
    assert not set(pg.evaluate("window.__nlhTest.filteredIds()")) & shared
    pg.context.close()


def test_select_new_skips_shared_unless_ticked(browser, server):
    rows = server.api("/api/library")["rows"]
    new_own = {r["artifact_id"] for r in rows if r["status"] == "new" and not r["shared"]}
    new_all = {r["artifact_id"] for r in rows if r["status"] == "new"}
    pg = page_at(browser, server)
    pg.wait_for_selector("#vspacer .vrow")
    pg.click("#sel-new")
    assert set(pg.evaluate("window.__nlhTest.selectedInView()")) == new_own
    pg.click("#sel-clear")
    pg.check("#sel-include-shared")
    pg.click("#sel-new")
    assert set(pg.evaluate("window.__nlhTest.selectedInView()")) == new_all
    pg.context.close()


def test_studio_view_only_rows_disabled(browser, server):
    pg = page_at(browser, server, "v=studio")
    pg.wait_for_selector("#studio-account")
    pg.select_option("#studio-account", index=1)
    pg.wait_for_selector("#studio-nb-list .nb-option")
    acct = server.api("/api/studio/state")["accounts"][0]["id"]
    nbs = server.api(f"/api/studio/notebooks?account_id={acct}")["notebooks"]
    viewers = [n for n in nbs if n["role"] == "viewer"]
    assert viewers
    pg.fill("#studio-nb-find", viewers[0]["title"])
    opt = pg.locator(".nb-option.is-disabled").first
    assert opt.locator("input").is_disabled()
    assert "View only" in opt.inner_text()
    pg.context.close()


def test_history_owner_filter(browser, server):
    pg = page_at(browser, server, "v=history")
    pg.wait_for_selector(".htable")
    counts = server.api("/api/history/page")["shared_counts"]
    pg.click("#h-filters")
    pg.locator("#popover-root .filters label", has_text="Shared with me").locator("input").click()
    pg.wait_for_timeout(800)
    assert pg.evaluate("window.__nlhTest.history.total()") == counts["shared"]
    if counts["shared"]:
        assert pg.locator(".htable tbody tr .rbadge-shared").count() > 0
    pg.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 1440])
def test_axe_shared_views(browser, server, width, scheme):
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=scheme, bypass_csp=True)
    for q, ready in (("group=notebook&own=shared", "#vspacer .vrow"), ("v=studio", "#studio-account")):
        pg = ctx.new_page()
        pg.goto(server.url + "&" + q)
        pg.wait_for_selector("body[data-ready='1']")
        pg.wait_for_selector(ready)
        if "studio" in q:
            pg.select_option("#studio-account", index=1)
            pg.wait_for_selector(".nb-option.is-disabled")
        pg.add_script_tag(content=AXE.read_text(encoding="utf-8"))
        result = pg.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
        bad = [
            f"{v['id']} ({v['impact']})"
            for v in result["violations"]
            if v["impact"] in ("serious", "critical")
        ]
        assert bad == [], q
        assert not pg.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        pg.close()
    ctx.close()
