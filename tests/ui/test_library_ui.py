"""U1 library view in a real browser, demo mode only (synthetic data, no network).

Every filter result is compared with tests/oracle.py, computed independently from the
server's rows. Skipped when Playwright or a browser is not available.
"""

import json
import os
import statistics
import time
import urllib.parse
from pathlib import Path

import pytest

from tests import oracle
from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")  # CI sets "chromium"


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=2000) as srv:
        yield srv


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        kw = {"headless": True}
        if CHANNEL == "chrome":
            kw.update(channel="chrome", args=["--use-mock-keychain", "--password-store=basic"])
        try:
            b = p.chromium.launch(**kw)
        except Exception as exc:  # no browser installed
            pytest.skip(f"no browser for UI tests: {exc}")
        yield b
        b.close()


def open_page(browser, server, query="", width=1440, height=900):
    ctx = browser.new_context(viewport={"width": width, "height": height}, bypass_csp=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server.url + (("&" + query) if query else ""))
    page.wait_for_selector("body[data-ready='1']", timeout=60000)
    page.errors = errors
    return page


def shown(page):
    return page.evaluate("window.__nlhTest.filteredIds()")


@pytest.fixture(scope="module")
def data(server):
    return server.api("/api/library")


def acct_id(data, label):
    return next(a["id"] for a in data["accounts"] if a["label"] == label)


CASES = [
    ("", {}, "newest"),
    ("q=garden", {"query": "garden"}, "newest"),
    (
        "q=" + urllib.parse.quote("status:new after:2026-10-01 orchard"),
        {"query": "status:new after:2026-10-01 orchard"},
        "newest",
    ),
    ("q=cafe", {"query": "cafe"}, "newest"),  # accent-blind: finds Café
    (
        "status=new,failed&multi=1&sort=title_asc",
        {"filters": {"status": ["new", "failed"], "multi": True}},
        "title_asc",
    ),
    ("status=saved&saved=30d&sort=size", {"filters": {"status": ["saved"], "saved": "30d"}}, "size"),
    (
        "created=2026-06-01..2026-09-30&exc=garden&inc=the",
        {
            "filters": {
                "created": {"from": "2026-06-01", "to": "2026-09-30"},
                "exclude": "garden",
                "include": "the",
            }
        },
        "newest",
    ),
    ("moved=1", {"filters": {"moved": True}}, "newest"),
    ("clash=1&sort=oldest", {"filters": {"clash": True}}, "oldest"),
    (
        "status=folder_unreachable,saved_but_missing",
        {"filters": {"status": ["folder_unreachable", "saved_but_missing"]}},
        "newest",
    ),
]


@pytest.mark.parametrize("qs,spec,sort", CASES)
def test_filter_results_equal_oracle(browser, server, data, qs, spec, sort):
    page = open_page(browser, server, qs)
    got = shown(page)
    want = oracle.expected(data, spec.get("query", ""), spec.get("filters"), None, sort)
    assert got == want
    count_text = page.inner_text("#result-count").replace(",", "")
    assert str(len(want)) in count_text
    assert not page.errors
    page.context.close()


def test_account_filter_and_scope(browser, server, data):
    work = acct_id(data, "Work account")
    page = open_page(browser, server, f"acct={work}&status=new")
    assert shown(page) == oracle.expected(data, "", {"account": [work], "status": ["new"]})
    page.context.close()
    page = open_page(browser, server, f"scope={work}")
    assert shown(page) == oracle.expected(data, "", {}, scope=work)
    page.context.close()


def test_typing_and_filter_clicks_match_oracle(browser, server, data):
    page = open_page(browser, server)
    page.fill("#q", "account:work bees")
    page.wait_for_timeout(300)
    assert shown(page) == oracle.expected(data, "account:work bees")
    page.locator("#filters label.check", has_text="New").first.click()
    page.wait_for_timeout(300)
    assert shown(page) == oracle.expected(data, "account:work bees", {"status": ["new"]})
    chips = page.locator("#chips .chip")
    assert chips.count() == 1
    chips.first.locator("button").click()
    page.wait_for_timeout(200)
    assert shown(page) == oracle.expected(data, "account:work bees")
    assert "q=account%3Awork+bees" in page.url or "q=account:work" in urllib.parse.unquote(page.url)
    page.context.close()


def test_url_state_survives_reload_and_back(browser, server, data):
    page = open_page(browser, server, "status=new&sort=title_asc&group=notebook")
    first = shown(page)
    import re

    assert "status=new" in page.url and not re.search(r"[?&]t=", page.url)  # key never in the address
    page.reload()
    page.wait_for_selector("body[data-ready='1']")
    assert shown(page) == first
    page.click(".tab[data-view='history']")
    page.wait_for_timeout(300)
    page.go_back()
    page.wait_for_timeout(500)
    assert shown(page) == first
    page.context.close()


def _window_with(data, label, low=21, high=50):
    """A created-date window where the account has between low and high new overviews."""
    work = acct_id(data, label)
    months = sorted({r["created_at"][:7] for r in data["rows"] if r["created_at"]})
    for i in range(len(months)):
        for j in range(i, len(months)):
            spec = {"from": f"{months[i]}-01", "to": f"{months[j]}-31"}
            # titles with "glacier" fail on purpose in demo mode, so leave them out here
            ids = oracle.expected(
                data, "", {"account": [work], "status": ["new"], "created": spec, "exclude": "glacier"}
            )
            if low <= len(ids) <= high:
                return work, spec, ids
    pytest.skip("no suitable window in this demo data")


def test_harvest_these_n_beyond_rendered_rows(browser, server, data):
    """Pagination ruling (b): Harvest these N with N above the rendered rows saves exactly the set."""
    work, spec, want = _window_with(data, "Work account")
    qs = f"acct={work}&status=new&created={spec['from']}..{spec['to']}&exc=glacier"
    page = open_page(browser, server, qs, height=480)
    assert shown(page) == want
    rendered = page.evaluate("window.__nlhTest.renderedRowCount()")
    assert rendered < len(want)
    page.click("#sel-new")
    assert sorted(page.evaluate("window.__nlhTest.selectedInView()")) == sorted(want)
    assert page.inner_text("#sel-summary").startswith(f"{len(want):,} selected")
    page.click("#sel-harvest")
    page.wait_for_selector("dialog[open]")  # more than 20: a confirm summary first
    page.locator("dialog[open] .btn-primary").click()
    deadline = time.time() + 600
    rows = {}
    while time.time() < deadline:
        rows = {r["artifact_id"]: r for r in server.api("/api/library")["rows"]}
        if all(rows[w]["status"] == "saved" for w in want):
            break
        time.sleep(2)
    assert all(rows[w]["status"] == "saved" for w in want)
    before_saved = {r["artifact_id"] for r in data["rows"] if r["status"] == "saved"}
    now_saved = {k for k, r in rows.items() if r["status"] == "saved"}
    assert now_saved - before_saved == set(want)  # nothing outside the filtered selection
    for w in want:
        assert Path(rows[w]["saved_path"]).exists()
    page.context.close()


def test_saved_view_roundtrip(browser, server):
    page = open_page(browser, server, "status=failed&group=status")
    page.click("#views-btn")
    page.locator(".popover button", has_text="Save current view").click()
    page.fill("dialog[open] input", "Only failures")
    page.locator("dialog[open] .btn-primary").click()
    page.wait_for_timeout(300)
    settings = server.api("/api/settings")
    view = next(v for v in settings["views"] if v["name"] == "Only failures")
    assert view["filters"]["status"] == ["failed"] and view["group"] == "status"
    page.goto(server.base + "/")
    page.wait_for_selector("body[data-ready='1']")
    page.click("#views-btn")
    page.locator(".popover button", has_text="Only failures").click()
    page.wait_for_timeout(300)
    assert "status=failed" in page.url
    page.context.close()


def test_keyboard_search_focus_and_row_moves(browser, server):
    page = open_page(browser, server)
    page.focus("#vlist")
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "q"
    page.keyboard.press("Escape")
    page.keyboard.press("Control+k")
    assert page.evaluate("document.activeElement.id") == "q"
    page.keyboard.press("Escape")
    page.focus("#vlist")
    page.keyboard.press("j")
    page.keyboard.press("j")
    assert page.locator(".vrow.is-focus").count() == 1
    page.context.close()


def _measure(page, word="garden soil"):
    page.evaluate("window.__nlhTest.perf().length")
    page.click("#q")
    for ch in word:
        page.keyboard.type(ch)
        page.wait_for_timeout(60)
    page.wait_for_timeout(300)
    times = page.evaluate("window.__nlhTest.perf()")[-len(word) :]
    return statistics.median(times), max(times)


def test_filter_keystroke_budget_2000(browser, server):
    page = open_page(browser, server)
    med, worst = _measure(page)
    limit = 100 if os.environ.get("CI") else 50
    print(f"\n2,000 rows: median {med:.1f} ms, worst {worst:.1f} ms")
    assert med < limit
    page.context.close()


def test_filter_keystroke_10000(browser):
    with DemoServer(rows=10000) as big:
        page = open_page(browser, big)
        assert page.evaluate("window.__nlhTest.filteredIds().length") == 10000
        med, worst = _measure(page)
        rendered = page.evaluate("window.__nlhTest.renderedRowCount()")
        print(f"\n10,000 rows: median {med:.1f} ms, worst {worst:.1f} ms, rendered rows {rendered}")
        Path(os.environ.get("NLH_PERF_OUT", "/tmp/nlh-perf.json")).write_text(
            json.dumps({"rows": 10000, "median_ms": med, "worst_ms": worst})
        )
        assert rendered < 60
        assert med < 250
        page.context.close()


@pytest.mark.parametrize("width", [375, 1440])
def test_axe_library(browser, server, width):
    page = open_page(browser, server, width=width)
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    page.context.close()
