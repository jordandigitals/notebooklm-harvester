"""Studio S1 in a real browser (demo mode, never Google): empty state, the one-time
warning, plan shown with its raw answer, editing rows, keeping and hiding a plan, axe."""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


@pytest.fixture(scope="module")
def server():
    os.environ["NLH_DEMO_ASK_S"] = "0.6"
    with DemoServer(rows=300) as srv:
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


def studio(browser, server, width=1440, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, bypass_csp=True)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url + "&v=studio")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#studio-account")
    return page


def pick(page, account_index=1, notebook_text=None):
    page.select_option("#studio-account", index=account_index)
    page.wait_for_selector("#studio-nb-list .nb-option")
    if notebook_text:
        page.fill("#studio-nb-find", notebook_text)
    page.locator("#studio-nb-list .nb-option").first.click()


def ask(page):
    page.click("#studio-ask")
    dlg = page.locator("#dlg[open]")
    try:
        dlg.wait_for(timeout=3000)
        page.locator("#dlg .btn-primary").click()
    except Exception:
        pass  # warning already shown once in this demo home
    page.wait_for_selector("#studio-plan", timeout=20000)


def test_empty_state_has_no_account_chosen(browser, server):
    page = studio(browser, server)
    assert page.input_value("#studio-account") == ""
    assert "Choose an account first" in page.inner_text("#view-studio")
    assert page.locator("#studio-ask").count() == 0
    assert not page.errors
    page.context.close()


def test_first_ask_shows_warning_and_plan_with_raw_answer(browser, server):
    page = studio(browser, server)
    pick(page)
    assert page.is_enabled("#studio-ask")
    assert page.locator("#studio-cap").inner_text().startswith("0 of 4 overviews")
    ask(page)
    raw = page.inner_text("#studio-raw")
    assert len(raw) > 40  # the answer as received is always visible
    rows = page.locator(".plan-row").count()
    summary = page.inner_text("#studio-plan")
    assert (rows and "read from the answer" in summary) or (not rows and "No list could be read" in summary)
    assert not page.errors
    page.context.close()


def test_edit_rows_keep_and_hide(browser, server):
    page = studio(browser, server)
    pick(page)
    ask(page)
    before = page.locator(".plan-row").count()
    page.click("#studio-add-row")
    assert page.locator(".plan-row").count() == before + 1
    last = page.locator(".plan-row").last
    last.locator("input").fill("A brand new overview")
    last.locator("textarea").fill("Only the parts about bees")
    if before:
        page.locator(".plan-row").first.locator("button[aria-label^='Remove']").click()
        assert page.locator(".plan-row").count() == before
    page.click("#studio-keep")
    page.wait_for_selector(".kept-plan")
    kept = page.inner_text("#studio-kept")
    assert "A brand new overview" in kept and "Only the parts about bees" in kept
    plans = server.api("/api/studio/state")["plans"]
    assert plans and plans[0]["items"][-1] == {
        "title": "A brand new overview",
        "focus": "Only the parts about bees",
    }
    page.locator(".kept-plan").first.locator("button", has_text="Hide").click()
    page.wait_for_timeout(500)
    assert len(server.api("/api/studio/state")["plans"]) == len(plans) - 1
    assert not page.errors
    page.context.close()


def test_keep_refuses_empty_title(browser, server):
    page = studio(browser, server)
    pick(page)
    ask(page)
    page.click("#studio-add-row")
    n = len(server.api("/api/studio/state")["plans"])
    page.click("#studio-keep")
    page.wait_for_selector(".toast")
    assert "needs a title" in page.inner_text("#toasts")
    assert len(server.api("/api/studio/state")["plans"]) == n
    page.context.close()


def test_shared_account_label(browser, server):
    page = studio(browser, server)
    accounts = server.api("/api/studio/state")["accounts"]
    page.evaluate(
        "async (id) => { await fetch('/api/settings', {method: 'POST', headers: {'X-NLH-Req': '1',"
        " 'Content-Type': 'application/json'}, body: JSON.stringify({studio_shared_accounts: [id]})}); }",
        accounts[1]["id"],
    )
    page.reload()
    page.wait_for_selector("#studio-account")
    page.select_option("#studio-account", str(accounts[1]["id"]))
    page.wait_for_selector(".studio-card .notice-warn")
    assert "used by other automated tools" in page.inner_text(".studio-card .notice-warn")
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 1440])
def test_axe_studio_with_a_plan(browser, server, width, scheme):
    page = studio(browser, server, width, scheme)
    pick(page)
    ask(page)
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    page.context.close()
