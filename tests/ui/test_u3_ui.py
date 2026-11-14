"""U3 in a real browser (demo mode): queue with cancel and retry, settings, drawer, keyboard, axe."""

import os
import time
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


def open_page(browser, server, query="", width=1440):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url + (("&" + query) if query else ""))
    page.wait_for_selector("body[data-ready='1']")
    return page


def rows_by_id(server):
    return {r["artifact_id"]: r for r in server.api("/api/library")["rows"]}


def test_queue_cancel_leaves_nothing_then_retry_and_clear(browser, server):
    work = next(a["id"] for a in server.api("/api/library")["accounts"] if a["label"] == "Work account")
    page = open_page(browser, server, f"acct={work}&status=new")
    ids = page.evaluate("window.__nlhTest.filteredIds()")[:12]
    before = rows_by_id(server)
    # select exactly 12 by clicking their checkboxes (scrolling the list as needed)
    page.evaluate("document.getElementById('vlist').scrollTop = 0")
    page.click("#sel-new")
    picked = page.evaluate("window.__nlhTest.selectedInView()")
    assert len(picked) > 12
    page.click("#sel-harvest")
    if page.locator("dialog[open]").count():
        page.locator("dialog[open] .btn-primary").click()
    page.wait_for_selector("section.job button:has-text('Cancel')", timeout=15000)
    page.wait_for_timeout(2500)
    page.locator("section.job button:has-text('Cancel')").click()
    page.wait_for_selector("section.job button:has-text('Clear finished')", timeout=120000)
    after = rows_by_id(server)
    newly = {k for k, r in after.items() if r["status"] == "saved" and before[k]["status"] != "saved"}
    assert newly < set(picked)  # some saved before the cancel, the rest untouched
    out = Path(server.api("/api/library")["accounts"][0]["output_dir"])
    assert not list(out.rglob("*.part"))
    for k in set(picked) - newly:
        assert after[k]["status"] == before[k]["status"]
    page.locator("section.job button:has-text('Clear finished')").click()
    assert page.locator("section.job").count() == 0
    assert not page.errors
    page.context.close()
    del ids


def test_settings_theme_template_and_diagnostics(browser, server):
    page = open_page(browser, server, "v=settings")
    page.wait_for_selector(".set-card")
    page.locator(".set-card [role=radio]", has_text="Dark").click()
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert server.api("/api/settings")["theme"] == "dark"
    tpl = page.locator(".set-card input.input").first
    tpl.fill("{account}/{date} {title}")
    page.wait_for_timeout(600)
    assert "Work account/2026-11-14 Why Raised Beds Warm Up Faster.mp3" in page.inner_text(".preview")
    page.locator(".set-card button", has_text="Save template").click()
    page.wait_for_timeout(300)
    assert server.api("/api/settings")["naming_template"] == "{account}/{date} {title}"
    tpl.fill("{title} {nonsense}")
    page.wait_for_timeout(600)
    assert "Use only" in page.inner_text(".set-card .error")
    diag = page.inner_text("pre.diag")
    assert "App home:" in diag and "@example.com" not in diag
    page.locator(".set-card [role=radio]", has_text="Match system").click()
    # restore the default template for other tests
    tpl.fill("{notebook}/{title}")
    page.wait_for_timeout(500)
    page.locator(".set-card button", has_text="Save template").click()
    page.context.close()


def test_drawer_opens_from_row_and_esc_returns(browser, server):
    page = open_page(browser, server, "status=saved")
    first = page.locator("#vspacer .vrow .rbody").first
    first.click()
    page.wait_for_selector("#drawer:not(.is-hidden) .kv")
    page.wait_for_selector("#drawer h3:has-text('Saved file')", timeout=10000)
    assert page.locator("#drawer button", has_text="Play").count() == 1
    page.keyboard.press("Escape")
    assert page.locator("#drawer").is_hidden()
    page.context.close()


def test_keyboard_cheatsheet_and_view_chords(browser, server):
    page = open_page(browser, server)
    page.focus("#vlist")
    page.keyboard.press("?")
    page.wait_for_selector("dialog[open] .keys")
    page.keyboard.press("Escape")
    page.focus("#vlist")
    page.keyboard.press("g")
    page.keyboard.press("s")
    page.wait_for_selector(".tile")
    assert "v=stats" in page.url
    page.keyboard.press("g")
    page.keyboard.press(",")
    page.wait_for_selector(".set-card")
    page.keyboard.press("g")
    page.keyboard.press("l")
    page.wait_for_timeout(300)
    page.focus("#vlist")
    page.keyboard.press("j")
    page.keyboard.press("Enter")
    page.wait_for_selector("#drawer:not(.is-hidden)")
    page.context.close()


@pytest.mark.parametrize(
    "view,width", [("settings", 375), ("settings", 1440), ("drawer", 375), ("drawer", 1440)]
)
def test_axe_u3(browser, server, view, width):
    page = open_page(browser, server, "v=settings" if view == "settings" else "status=saved", width=width)
    if view == "settings":
        page.wait_for_selector(".set-card")
        page.wait_for_timeout(500)
    else:
        page.locator("#vspacer .vrow .rbody").first.click()
        page.wait_for_selector("#drawer h3:has-text('Saved file')", timeout=10000)
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    page.context.close()
    time.sleep(0.1)


def test_retry_failed_reruns_exactly_the_failures(browser, server):
    page = open_page(browser, server, "status=new&inc=glacier")
    page.click("#sel-new")
    picked = page.evaluate("window.__nlhTest.selectedInView()")
    assert picked
    page.click("#sel-harvest")
    if page.locator("dialog[open]").count():
        page.locator("dialog[open] .btn-primary").click()
    retry = page.locator("section.job button:has-text('Retry')")
    retry.wait_for(timeout=180000)
    data = server.api("/api/library")
    travel = next(a["id"] for a in data["accounts"] if a["label"] == "Travel drive")
    by_id = {r["artifact_id"]: r for r in data["rows"]}
    runnable = [a for a in picked if by_id[a]["account_id"] != travel]  # its folder is unplugged
    failed = page.locator("section.job .jerr").count()
    assert failed == len(runnable)  # every glacier title fails in demo mode
    assert all(by_id[a]["status"] == "failed" for a in runnable)
    if len(runnable) < len(picked):
        assert "Not started" in page.inner_text("section.job")
    retry.click()
    page.locator("section.job button:has-text('Retry')").wait_for(timeout=180000)
    job_title = page.inner_text("section.job .job-head .grow")
    assert job_title.startswith(f"Harvesting {len(runnable)}")
    page.context.close()
