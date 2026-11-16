"""D-032 in a real browser (demo mode): the Library scrolls as one page, only small bars are
pinned (the toolbar, and the harvest line while it runs, with Stop), the compact account card
keeps every control behind More (remembered), and keyboard focus never lands under a pinned bar.
"""

import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
SC = "document.querySelector('#view-library .content')"
TB = "document.querySelector('#view-library .toolbar')"


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
        except Exception as exc:
            pytest.skip(f"no browser for UI tests: {exc}")
        yield b
        b.close()


def first_account(server):
    return next(a for a in server.api("/api/library")["accounts"] if a["output_dir"] and a["status"] == "ok")


def open_lib(browser, server, width=1280, height=720, scheme="light", touch=False, account=True):
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        bypass_csp=True,
        has_touch=touch,
        is_mobile=touch,
    )
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url)
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#vspacer .vrow", timeout=30000)
    if account:
        acct = first_account(server)
        if page.locator("#sidebar-open").is_visible():
            page.click("#sidebar-open")
        page.locator("#acct-list button", has_text=acct["label"]).first.click()
        if page.locator("#sidebar.is-open").count():
            page.locator("#sidebar .close-sidebar").first.click()
        page.wait_for_selector("#account-card .card-toggle")
    return page


def idle(server, timeout_s=60):
    """Wait until the app runs no job (a finished job redraws the account card)."""
    import time

    end = time.time() + timeout_s
    while time.time() < end:
        if not any(j["state"] in ("queued", "running") for j in server.api("/api/state").get("jobs", [])):
            return
        time.sleep(0.3)


def scroll_to(page, y):
    """Scroll the column and wait until the list has redrawn the rows for that place."""
    page.evaluate(f"{SC}.scrollTop = {y}")
    page.wait_for_function(
        f"""() => {{ const sc = {SC}, l = document.getElementById('vlist').getBoundingClientRect();
        if (l.top > innerHeight || !document.querySelector('#vspacer .vrow')) return true;
        return [...document.querySelectorAll('#vspacer .vrow')].some(r => {{
          const b = r.getBoundingClientRect(); return b.bottom > 0 && b.top < innerHeight; }}); }}""",
        timeout=10000,
    )
    page.wait_for_timeout(100)


def pinned_bottom(page):
    """Bottom edge (viewport px) of the bars pinned at the top right now."""
    return page.evaluate(
        f"""() => {{ let b = {SC}.getBoundingClientRect().top;
        for (const n of [{TB}, document.getElementById('job-box')]) {{
          if (n.offsetHeight && getComputedStyle(n).position === 'sticky')
            b = Math.max(b, n.getBoundingClientRect().bottom);
        }} return b; }}"""
    )


def start_harvest(page, n_min=12):
    """Select new rows on the page and start a harvest (confirms the big-harvest dialog)."""
    scroll_to(page, 0)
    page.click("#sel-new")
    page.click("#sel-harvest")
    if page.locator("dialog[open]").count():
        page.locator("dialog[open] .btn-primary").click()
    page.wait_for_selector("#job-box section.job")


def test_page_scrolls_into_the_list_and_back(browser, server):
    page = open_lib(browser, server)
    m = page.evaluate(
        f"() => {{ const sc = {SC}, l = document.getElementById('vlist');"
        " return {sh: sc.scrollHeight, ch: sc.clientHeight, inner: getComputedStyle(l).overflowY,"
        " doc: document.scrollingElement.scrollHeight > innerHeight}; }"
    )
    assert m["sh"] > m["ch"] * 5  # one long page holds the whole list
    assert m["inner"] == "visible" and not m["doc"]  # no second scroll box, no body scroll
    scroll_to(page, 6000)
    rows = page.evaluate(
        "() => [...document.querySelectorAll('#vspacer .vrow')].filter(r => {"
        " const b = r.getBoundingClientRect(); return b.bottom > 0 && b.top < innerHeight; }).length"
    )
    assert rows >= 5
    assert page.evaluate("window.__nlhTest.renderedRowCount()") < 60  # still only the rows in view
    assert page.locator("#account-card").bounding_box()["y"] < 0  # the card scrolled away
    scroll_to(page, 0)
    assert page.locator("#account-card").bounding_box()["y"] > 0
    assert not page.errors
    page.context.close()


def test_toolbar_is_the_only_pinned_bar_when_idle(browser, server):
    page = open_lib(browser, server)
    scroll_to(page, 4000)
    tb = page.locator("#view-library .toolbar").bounding_box()
    top = page.evaluate(f"{SC}.getBoundingClientRect().top")
    assert abs(tb["y"] - top) < 1.5  # toolbar stays at the top of the column
    assert tb["height"] < 80  # and it is small
    assert pinned_bottom(page) - top < 80
    page.context.close()


def test_click_after_scrolling_hits_the_row_under_the_pointer(browser, server):
    page = open_lib(browser, server)
    scroll_to(page, 3000)
    hit = page.evaluate(
        """() => { for (let y = 300; y < innerHeight - 40; y += 20) {
          const r = document.elementFromPoint(innerWidth - 200, y)?.closest('.vrow');
          if (r && r.querySelector('input')) { const b = r.querySelector('input').getBoundingClientRect();
            return {id: r.dataset.id, x: b.x + b.width / 2, y: b.y + b.height / 2}; } } return null; }"""
    )
    assert hit
    page.mouse.click(hit["x"], hit["y"])
    page.wait_for_timeout(200)
    cls = page.locator(f"#vspacer .vrow[data-id='{hit['id']}']").get_attribute("class")
    assert "is-selected" in cls
    page.click("#sel-clear")
    page.context.close()


def test_running_harvest_line_is_pinned_compact_and_has_stop(browser, server):
    page = open_lib(browser, server)
    start_harvest(page)
    page.locator("#job-box button:has-text('Stop')").wait_for(timeout=15000)
    scroll_to(page, 2500)
    box = page.locator("#job-box").bounding_box()
    tb = page.locator("#view-library .toolbar").bounding_box()
    assert abs(box["y"] - (tb["y"] + tb["height"])) < 1.5  # right under the toolbar
    assert box["height"] < 110  # one compact line plus the slim bar
    assert "done" in page.inner_text("#job-box .job-sum")
    page.locator("#job-box button:has-text('Stop')").click()
    page.locator("#job-box button:has-text('Clear finished')").wait_for(timeout=120000)
    assert page.evaluate("getComputedStyle(document.getElementById('job-box')).position") != "sticky"
    page.locator("#job-box button:has-text('Clear finished')").click()
    assert page.locator("#job-box section.job").count() == 0
    assert not page.errors
    page.context.close()


def test_details_opens_the_items_and_is_remembered(browser, server):
    page = open_lib(browser, server)
    start_harvest(page)
    page.locator("#job-box button:has-text('Clear finished')").wait_for(timeout=180000)
    toggle = page.locator("#job-box .job-toggle")
    details = page.locator("#job-details")
    was = toggle.get_attribute("aria-expanded") == "true"
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == str(not was).lower()
    assert details.is_visible() is (not was)
    if not was:
        assert page.locator("#job-details .job-items li").count() >= 1
    page.wait_for_timeout(300)
    assert server.api("/api/settings")["job_details_open"] is (not was)
    toggle.click()  # leave it as it was
    page.wait_for_timeout(300)
    assert server.api("/api/settings")["job_details_open"] is was
    page.locator("#job-box button:has-text('Clear finished')").click()
    page.context.close()


def test_more_shows_every_control_and_is_remembered(browser, server):
    page = open_lib(browser, server)
    toggle = page.locator("#account-card .card-toggle")
    if toggle.get_attribute("aria-expanded") == "true":
        toggle.click()
    assert not page.locator("#account-more").is_visible()
    head = page.inner_text("#account-card .head")
    assert "Refresh notebooks" in head and "Save to" in head
    toggle.click()
    more = page.locator("#account-more")
    assert more.is_visible()
    for label in ("Check sign-in", "Choose folder", "Open", "Scan folder"):
        assert more.locator(f"button:has-text('{label}')").is_visible(), label
    page.wait_for_timeout(300)
    assert server.api("/api/settings")["account_card_open"] is True
    page.reload()
    page.wait_for_selector("#vspacer .vrow", timeout=30000)
    page.wait_for_selector("#account-card .card-toggle")
    assert page.locator("#account-more").is_visible()  # remembered across a reload
    page.locator("#account-card .card-toggle").click()
    page.wait_for_timeout(300)
    assert server.api("/api/settings")["account_card_open"] is False
    page.context.close()


def test_moved_controls_still_work(browser, server):
    page = open_lib(browser, server)
    toggle = page.locator("#account-card .card-toggle")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    more = page.locator("#account-more")
    opened = []
    # Open asks the computer to show the folder: answered here, so no Explorer window appears.
    page.route("**/api/open", lambda route: (opened.append(1), route.fulfill(status=200, body="{}")))
    more.locator("button:has-text('Open')").click()
    page.wait_for_timeout(300)
    assert opened
    more.locator("button:has-text('Choose folder')").click()
    page.wait_for_selector("dialog[open]")
    page.keyboard.press("Escape")
    page.wait_for_selector("dialog[open]", state="detached")
    more.locator("button:has-text('Check sign-in')").click()
    page.locator("#job-box section.job, .toast").first.wait_for(timeout=15000)
    page.wait_for_function("() => !document.querySelector('#account-more button').disabled", timeout=30000)
    more.locator("button:has-text('Scan folder')").click()
    page.locator(".toast").first.wait_for(timeout=30000)
    # When a job finishes the card redraws and replaces the name box, so rename only once no job runs.
    idle(server)
    page.wait_for_timeout(500)

    def rename(to):
        name = page.locator("#account-card .name")
        name.fill(to)
        name.press("Enter")
        name.blur()
        for _ in range(50):  # the save is a request: allow a slow machine up to 10 s
            if to in [a["label"] for a in server.api("/api/library")["accounts"]]:
                return True
            page.wait_for_timeout(200)
        return False

    old = page.locator("#account-card .name").input_value()
    assert rename(old + " x")
    assert rename(old)
    if toggle.get_attribute("aria-expanded") == "true":
        toggle.click()
    assert not page.errors
    page.context.close()


@pytest.mark.parametrize("group", ["none", "notebook"])
def test_jk_focus_never_lands_under_a_pinned_bar(browser, server, group):
    page = open_lib(browser, server)
    page.select_option("#group", group)
    page.wait_for_timeout(300)
    page.focus("#vlist")
    head = 44 if group == "notebook" else 0
    for _ in range(40):
        page.keyboard.press("j")
    for direction, presses in (("j", 0), ("k", 25)):
        for _ in range(presses):
            page.keyboard.press(direction)
        page.wait_for_timeout(150)
        row = page.locator("#vspacer .vrow.is-focus").bounding_box()
        assert row, "the focused row is rendered"
        assert row["y"] >= pinned_bottom(page) + head - 1, (direction, row["y"])
        assert row["y"] + row["height"] <= page.viewport_size["height"] + 1
    pad = page.evaluate(f"getComputedStyle({SC}).scrollPaddingTop")
    pin = page.evaluate(f"{SC}.style.getPropertyValue('--pin-h')")
    assert pad == pin and pin != "0px"  # Tab focus is kept clear of the pinned bars too
    page.select_option("#group", "none")
    page.context.close()


def test_back_to_top(browser, server):
    page = open_lib(browser, server)
    scroll_to(page, 5000)
    page.locator("#backtop").click()
    page.wait_for_timeout(200)
    assert page.evaluate(f"{SC}.scrollTop") == 0
    page.context.close()


def test_phone_width_pins_nothing_and_still_scrolls(browser, server):
    page = open_lib(browser, server, width=375, height=667, touch=True)
    scroll_to(page, 3000)
    assert page.evaluate(f"getComputedStyle({TB}).position") != "sticky"
    assert page.evaluate("window.__nlhTest.renderedRowCount()") > 3
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    # the column scrolls by itself, so check it too: nothing wider than the column (a clipped button)
    assert page.evaluate(f"{SC}.scrollWidth <= {SC}.clientWidth + 1")
    page.context.close()


@pytest.mark.parametrize("width", [320, 375])
def test_phone_harvest_line_buttons_fit(browser, server, width):
    """The finished harvest line on a phone: every button inside the card, nothing clipped."""
    page = open_lib(browser, server, width=width, height=700, touch=True)
    # Earlier tests harvest every New row of the first account: pick from all accounts instead.
    page.click("[data-scope=all]")
    page.wait_for_timeout(300)
    boxes = page.locator("#vlist input[type=checkbox]")
    for i in range(2):
        boxes.nth(i).click()
    page.click("#sel-harvest")
    page.locator("#job-box button:has-text('Clear finished')").wait_for(timeout=120000)
    card = page.locator("#job-box section.job").bounding_box()
    for b in page.locator("#job-box section.job button").all():
        if b.is_visible():
            r = b.bounding_box()
            inside = r["x"] >= card["x"] - 1 and r["x"] + r["width"] <= card["x"] + card["width"] + 1
            assert inside, b.inner_text()
    assert page.evaluate(f"{SC}.scrollWidth <= {SC}.clientWidth + 1")
    page.locator("#job-box button:has-text('Clear finished')").click()
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 768, 1024, 1440])
def test_library_axe_clean_with_card_and_job(browser, server, width, scheme):
    page = open_lib(browser, server, width=width, height=900, scheme=scheme)
    toggle = page.locator("#account-card .card-toggle")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
    run = "async () => await axe.run('#view-library .content', {resultTypes: ['violations']})"
    result = page.evaluate(run)
    bad = [
        f"{v['id']} ({v['impact']}): {v['help']} x{len(v['nodes'])}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]
    assert bad == []
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    # the column scrolls by itself, so check it too: nothing wider than the column (a clipped button)
    assert page.evaluate(f"{SC}.scrollWidth <= {SC}.clientWidth + 1")
    toggle.click()
    page.context.close()


def test_card_and_job_touch_targets_40px(browser, server):
    page = open_lib(browser, server, width=390, height=844, touch=True)
    toggle = page.locator("#account-card .card-toggle")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    hs = page.evaluate(
        "() => [...document.querySelectorAll('#account-card button, #job-box button')]"
        ".filter(e => e.offsetParent).map(e => [e.textContent.trim(), e.getBoundingClientRect().height])"
    )
    assert hs and all(h >= 40 for _, h in hs), hs
    toggle.click()
    page.context.close()
