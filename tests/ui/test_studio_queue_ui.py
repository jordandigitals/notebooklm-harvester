"""Studio S2 and S3 in a real browser (demo mode, never Google): queue from a kept plan,
progress, auto-harvest with Play, the limit stop, Cancel, and the page after a restart."""

import os
import tempfile
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
FAST = {"NLH_DEMO_ASK_S": "0.3", "NLH_DEMO_GEN_S": "2", "NLH_DEMO_POLL_S": "0.4", "NLH_DEMO_PAUSE_S": "1"}


@pytest.fixture(scope="module", autouse=True)
def fast_demo():
    old = {k: os.environ.get(k) for k in FAST}
    os.environ.update(FAST)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


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


def open_studio(browser, srv, width=1440, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=scheme, bypass_csp=True)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(srv.url + "&v=studio")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#studio-account")
    return page


def keep_plan(page, focus_override=None):
    """Choose the first account and notebook, ask, keep the plan (optionally with new focus)."""
    page.select_option("#studio-account", index=1)
    page.wait_for_selector("#studio-nb-list .nb-option")
    page.locator("#studio-nb-list .nb-option").first.click()
    page.click("#studio-ask")
    try:
        page.locator("#dlg[open]").wait_for(timeout=2500)
        page.locator("#dlg .btn-primary").click()
    except Exception:
        pass
    page.wait_for_selector("#studio-plan", timeout=20000)
    if page.locator(".plan-row").count() == 0:
        page.click("#studio-add-row")
        page.locator(".plan-row input").fill("Manual overview")
    for i, text in enumerate(focus_override or []):
        if i >= page.locator(".plan-row").count():
            page.click("#studio-add-row")
            page.locator(".plan-row").last.locator("input").fill(f"Extra {i}")
        page.locator(".plan-row").nth(i).locator("textarea").fill(text)
    page.click("#studio-keep")
    page.wait_for_selector(".kept-plan")


def enqueue(page, n=None):
    page.locator(".kept-plan").first.locator("button", has_text="Make overviews").click()
    page.wait_for_selector(".make-panel")
    rows = page.locator(".make-row")
    if n is not None:
        for i in range(n, rows.count()):
            rows.nth(i).locator("input[type=checkbox]").uncheck()
    page.click("#studio-enqueue")
    page.wait_for_selector("#studio-queue")


def start(page):
    page.click("#studio-start")
    page.locator("#dlg[open]").wait_for()
    page.locator("#dlg .btn-primary").click()


def wait_idle(page, timeout=60000):
    page.wait_for_function(
        "window.__nlhTest.studio.queue() && !window.__nlhTest.studio.queue().running", timeout=timeout
    )
    page.wait_for_timeout(300)


def test_queue_runs_one_at_a_time_and_saves_mp3(browser):
    with DemoServer(rows=200) as srv:
        page = open_studio(browser, srv)
        keep_plan(page)
        enqueue(page, n=2)
        q = srv.api("/api/studio/queue")
        n = len([r for r in q["rows"] if r["state"] == "queued"])
        assert n >= 1 and q["runner"]["running"] is False  # nothing starts on its own
        start(page)
        page.wait_for_selector(".queue-row[data-state='generating']", timeout=15000)
        assert (
            page.locator(".queue-row[data-state='generating'], .queue-row[data-state='submitting']").count()
            == 1
        )
        wait_idle(page)
        q = srv.api("/api/studio/queue")
        done = [r for r in q["rows"] if r["state"] == "done"]
        assert len(done) == n and all(r["harvest_id"] for r in done)
        hist = srv.api("/api/history/page?size=250")
        assert {r["artifact_id"] for r in hist["rows"]} >= {r["task_id"] for r in done}
        assert sum(1 for x in srv.api("/api/studio/state")["used_today"].values() if x) == 1
        page.locator(".queue-row[data-state='done'] button", has_text="Play").first.click()
        page.wait_for_function("window.__nlhTest.player().id !== null", timeout=10000)
        assert not page.errors
        page.context.close()


def test_limit_pauses_the_account(browser):
    with DemoServer(rows=200) as srv:
        page = open_studio(browser, srv)
        keep_plan(page, focus_override=["a short one", "this one hits the limit", "never sent"])
        enqueue(page)
        start(page)
        wait_idle(page)
        states = [r["state"] for r in srv.api("/api/studio/queue")["rows"]]
        assert states[0] == "done" and states[1] == "paused_limit"
        assert all(s == "paused_limit" for s in states[2:])
        assert "usage limit was reached today" in page.inner_text("#studio-queue")
        assert (
            page.locator(".queue-row[data-state='paused_limit'] button", has_text="Queue again").count() >= 1
        )
        page.context.close()


def test_cancel_stops_after_the_current_step(browser):
    with DemoServer(rows=200) as srv:
        page = open_studio(browser, srv)
        keep_plan(page, focus_override=["first", "second", "third"])
        enqueue(page)
        start(page)
        page.wait_for_selector("#studio-cancel", timeout=15000)
        page.click("#studio-cancel")
        wait_idle(page)
        states = [r["state"] for r in srv.api("/api/studio/queue")["rows"]]
        assert states.count("cancelled") >= 1 and "queued" not in states
        assert set(states) <= {"cancelled", "generating", "done"}
        page.context.close()


def test_page_after_restart_shows_resume_and_sends_nothing(browser):
    home = tempfile.mkdtemp(prefix="nlh-ui-restart-")
    with DemoServer(rows=200, home=home) as srv:
        page = open_studio(browser, srv)
        keep_plan(page, focus_override=["one", "two"])
        enqueue(page)
        start(page)
        page.wait_for_selector(".queue-row[data-state='generating']", timeout=15000)
        page.context.close()
    # The app stopped mid-run. On the next start nothing is sent until Jordan clicks Resume.
    with DemoServer(rows=200, home=home) as srv:
        page = open_studio(browser, srv)
        page.wait_for_selector("#studio-queue")
        q = srv.api("/api/studio/queue")
        assert q["runner"]["running"] is False
        states = [r["state"] for r in q["rows"]]
        assert "generating" in states or "needs_review" in states
        assert page.locator("#studio-start").count() == 1
        assert page.locator("#studio-start").inner_text().startswith("Resume")
        page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 1440])
def test_axe_queue(browser, width, scheme):
    with DemoServer(rows=100) as srv:
        page = open_studio(browser, srv, width, scheme)
        keep_plan(page)
        enqueue(page)
        page.locator(".kept-plan").first.locator("button", has_text="Make overviews").click()
        page.wait_for_selector(".make-panel")
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
