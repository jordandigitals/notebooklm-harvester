"""D-033 in a real browser (demo mode): the folder picker lists remembered folders with
one-click Use and Forget, marks a missing folder, notes when another account uses a folder,
and a new account without a folder gets one-click picks (nothing assigned silently)."""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


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


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=50) as srv:
        yield srv


def open_account(browser, server, label, width=1280, scheme="light", touch=False):
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
    page.locator("#acct-list button", has_text=label).first.click()
    if page.locator("#sidebar.is-open").count():
        page.locator("#sidebar .close-sidebar").first.click()
    page.wait_for_selector("#account-card .card-toggle")
    return page


def open_picker(page):
    toggle = page.locator("#account-card .card-toggle")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.locator("#account-more button:has-text('Choose folder')").click()
    page.wait_for_selector("dialog[open] .recent-list li")
    return page.locator("dialog[open]")


def test_recent_folders_listed_and_missing_marked(browser, server):
    page = open_account(browser, server, "Home account")
    dlg = open_picker(page)
    rows = dlg.locator(".recent-list li")
    assert rows.count() == 4
    missing = dlg.locator(".recent-list li.is-missing")
    assert missing.count() == 1 and "Not available" in missing.inner_text()
    assert missing.locator("button:has-text('Use')").count() == 0
    assert dlg.locator(".recent-list li:not(.is-missing) button:has-text('Use')").count() == 3
    # The picker opens at the account's own folder.
    assert dlg.locator("input[aria-label='Folder path']").input_value().endswith("Home")
    page.context.close()


def test_notice_when_another_account_uses_the_folder(browser, server):
    page = open_account(browser, server, "Home account")
    dlg = open_picker(page)
    work = next(f["path"] for f in server.api("/api/folders") if f["path"].endswith("Work"))
    dlg.locator("input[aria-label='Folder path']").fill(work)
    dlg.locator("button:has-text('Go')").click()
    note = dlg.locator(".notice")
    note.wait_for(state="visible")
    assert note.inner_text() == (
        "Also used by Work account. Files from both accounts are saved together; "
        "existing files are never overwritten."
    )
    # A folder nobody else uses: no notice.
    shared = next(f["path"] for f in server.api("/api/folders") if f["path"].endswith("Shared library"))
    dlg.locator("input[aria-label='Folder path']").fill(shared)
    dlg.locator("button:has-text('Go')").click()
    page.wait_for_function(
        "() => document.querySelector('dialog[open] .notice').classList.contains('is-hidden')"
    )
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
def test_picker_axe_clean(browser, server, width, scheme):
    page = open_account(browser, server, "Home account", width, scheme)
    dlg = open_picker(page)
    work = next(f["path"] for f in server.api("/api/folders") if f["path"].endswith("Work"))
    dlg.locator("input[aria-label='Folder path']").fill(work)
    dlg.locator("button:has-text('Go')").click()
    dlg.locator(".notice").wait_for(state="visible")
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run('dialog[open]', {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    over = page.evaluate(
        "() => { const d = document.querySelector('dialog[open]');"
        " return d.scrollWidth > d.clientWidth + 1; }"
    )
    assert not over
    page.context.close()


def test_picker_touch_targets_40px(browser, server):
    page = open_account(browser, server, "Home account", 390, touch=True)
    dlg = open_picker(page)
    hs = dlg.evaluate(
        "d => [...d.querySelectorAll('.recent-list button, .dlg-actions button')]"
        ".filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height)"
    )
    assert hs and all(h >= 40 for h in hs), hs
    page.context.close()


def test_use_and_forget_then_new_account_quick_picks(browser):
    with DemoServer(rows=20) as srv:  # its own server: this test changes folders
        page = open_account(browser, srv, "Home account")
        dlg = open_picker(page)
        # Forget the missing folder: the list loses it; nothing else changes.
        dlg.locator(".recent-list li.is-missing button:has-text('Forget')").click()
        page.wait_for_function("() => document.querySelectorAll('dialog[open] .recent-list li').length === 3")
        assert len(srv.api("/api/folders")) == 3
        # One click on Use for the Work folder: saved, with the notice in the message.
        dlg.locator(".recent-list li", has_text="Work").locator("button:has-text('Use')").click()
        toast = page.locator(".toast", has_text="Also used by Work account")
        toast.wait_for(timeout=10000)
        home = next(a for a in srv.api("/api/state")["accounts"] if a["label"] == "Home account")
        assert home["output_dir"].endswith("Work")
        assert srv.api("/api/folders")[0]["path"].endswith("Work")  # most recent first
        # A new account gets no folder, only one-click picks.
        if page.locator("#sidebar-open").is_visible():
            page.click("#sidebar-open")
        page.click("#add-toggle")
        page.click("#signin-add")
        page.wait_for_selector("#acct-list >> text=demo.new@example.com", timeout=20000)
        new = next(a for a in srv.api("/api/state")["accounts"] if a["email"] == "demo.new@example.com")
        assert not new["output_dir"]
        page.locator("#acct-list button", has_text="demo.new@example.com").first.click()
        if page.locator("#sidebar.is-open").count():
            page.locator("#sidebar .close-sidebar").first.click()
        picks = page.locator("#account-card .quick-picks button")
        picks.first.wait_for(timeout=10000)
        assert 1 <= picks.count() <= 3
        picks.first.click()
        # Wait for this pick's own save. The "Folder saved" toast from the Use step above can still be on
        # screen for 7 s, so waiting for a toast could return before this save (flaky on CI, 2026-11-17).
        new = {}
        for _ in range(100):
            new = next(a for a in srv.api("/api/state")["accounts"] if a["email"] == "demo.new@example.com")
            if new["output_dir"]:
                break
            page.wait_for_timeout(100)
        assert new["output_dir"]
        page.context.close()
