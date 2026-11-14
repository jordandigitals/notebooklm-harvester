"""P2-A in real browsers (demo mode): the laptop turns phone listening on and makes a pairing
link; a second browser context (phone-sized, touch) pairs through the real LAN listener, sees
the listen-only page, plays with seek; revoke makes its next request fail."""

import os
from pathlib import Path

import pytest

from nlh import phone
from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")

pytestmark = pytest.mark.skipif(phone.lan_address() is None, reason="no private LAN address here")


@pytest.fixture(scope="module")
def server():
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


def laptop(browser, server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, bypass_csp=True)
    page = ctx.new_page()
    page.goto(server.url + "&v=settings")
    page.wait_for_selector("#phone-card")
    return page


def enable_and_pair_link(page):
    # The switch shows the saved setting; an earlier test can leave it on with no listener running,
    # so turn it off first when the Pair button is missing.
    if page.is_checked("#phone-enable") and page.locator("#phone-pair-btn").count() == 0:
        page.uncheck("#phone-enable")
        page.wait_for_timeout(500)
    if not page.is_checked("#phone-enable"):
        page.check("#phone-enable")
    page.wait_for_selector("#phone-pair-btn")
    page.click("#phone-pair-btn")
    page.wait_for_selector("#phone-pair-link")
    assert page.locator(".phone-qr svg").count() == 1
    return page.inner_text("#phone-pair-link")


def phone_ctx(browser):
    return browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, bypass_csp=True
    )


def pair(browser, server, name="Test phone"):
    lap = laptop(browser, server)
    link = enable_and_pair_link(lap)
    ctx = phone_ctx(browser)
    ph = ctx.new_page()
    ph.errors = []
    ph.on("pageerror", lambda e: ph.errors.append(str(e)))
    ph.goto(link)
    ph.fill("input[name=name]", name)
    ph.click("button[type=submit]")
    ph.wait_for_selector("body[data-ready='1']", timeout=30000)
    return lap, ph, link


def test_unpaired_phone_sees_only_the_pairing_notice(browser, server):
    lap = laptop(browser, server)
    link = enable_and_pair_link(lap)
    base = link.split("/pair/")[0]
    ctx = phone_ctx(browser)
    ph = ctx.new_page()
    r = ph.goto(base + "/")
    assert r.status == 403 and "not paired" in ph.inner_text("body")
    assert ph.request.get(base + "/api/library").status == 403
    ctx.close()
    lap.context.close()


def test_pair_listen_only_play_and_revoke(browser, server):
    lap, ph, link = pair(browser, server)
    base = link.split("/pair/")[0]
    # Listen-only: no write or control buttons on the page.
    ph.wait_for_selector("#vspacer .vrow")
    for sel in (
        "#quit",
        "#keys-help",
        ".tab[data-view='studio']",
        ".tab[data-view='settings']",
        "#add-toggle",
        "#sel-new",
        "#actionbar",
    ):
        assert not ph.locator(sel).first.is_visible(), sel
    assert ph.locator("#vspacer .vrow input[type=checkbox]").count() == 0
    # The server refuses writes even if the page were changed.
    r = ph.request.post(base + "/api/harvest", headers={"X-NLH-Req": "1"}, data="{}")
    assert r.status == 403
    assert ph.request.post(base + "/api/quit", headers={"X-NLH-Req": "1"}).status == 403
    # History: Play only, then play and seek.
    ph.goto(base + "/?v=history")
    ph.wait_for_selector(".hcard .hactions")
    assert ph.locator(".hcard").first.locator(".hactions > button").count() == 1
    ph.locator(".hactions button[aria-label='Play']").first.click()
    ph.wait_for_function("window.__nlhTest && window.__nlhTest.player().id !== null", timeout=15000)
    pid = ph.evaluate("window.__nlhTest.player().id")
    ranged = ph.request.get(f"{base}/api/play/{pid}", headers={"Range": "bytes=0-99"})
    assert ranged.status == 206 and len(ranged.body()) == 100
    ph.wait_for_timeout(1200)
    ph.locator("#player button[aria-label='Back 15 seconds']").click()
    assert ph.evaluate("window.__nlhTest.player().time") < 1.5
    assert not ph.errors
    # Revoke on the laptop: the phone's next request fails.
    lap.reload()
    lap.wait_for_selector("#phone-devices")
    lap.locator("#phone-devices button", has_text="Remove").first.click()
    lap.wait_for_timeout(800)
    assert ph.request.get(base + "/api/state").status == 403
    ph.context.close()
    lap.context.close()


def test_pairing_link_works_once(browser, server):
    lap, ph, link = pair(browser, server, "First")
    other = phone_ctx(browser).new_page()
    r = other.goto(link)
    assert r.status == 410 and "expired or was already used" in other.inner_text("body")
    other.context.close()
    ph.context.close()
    lap.context.close()


def test_turning_off_closes_the_phone_address(browser, server):
    lap, ph, link = pair(browser, server, "Off test")
    base = link.split("/pair/")[0]
    lap.reload()
    lap.wait_for_selector("#phone-enable")
    lap.uncheck("#phone-enable")
    lap.wait_for_timeout(800)
    with pytest.raises(pw.Error):  # connection refused: the phone address is closed
        ph.request.get(base + "/api/state", timeout=3000)
    ph.context.close()
    lap.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_phone_listen_mode_and_settings(browser, server, scheme):
    lap, ph, _link = pair(browser, server, f"Axe {scheme}")
    ph.emulate_media(color_scheme=scheme)
    ph.wait_for_selector("#vspacer .vrow")
    for page in (ph, lap):
        page.add_script_tag(content=AXE.read_text(encoding="utf-8"))
        result = page.evaluate("async () => await axe.run(document, {resultTypes: ['violations']})")
        bad = [
            f"{v['id']} ({v['impact']})"
            for v in result["violations"]
            if v["impact"] in ("serious", "critical")
        ]
        assert bad == []
    assert not ph.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    ph.context.close()
    lap.context.close()


def test_windows_firewall_hint(browser, server):
    """On Windows the hint says Allow (not Cancel) and covers Public networks (Chat ruling)."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, bypass_csp=True)
    page = ctx.new_page()
    page.route(
        "**/api/state",
        lambda route: route.fulfill(response=(r := route.fetch()), json={**r.json(), "windows": True}),
    )
    page.goto(server.url + "&v=settings")
    page.wait_for_selector("#phone-card")
    if not page.is_checked("#phone-enable"):
        page.check("#phone-enable")
    page.wait_for_selector("#phone-firewall-hint")
    text = page.inner_text("#phone-firewall-hint")
    assert "Choose Allow, not Cancel" in text and "Public networks" in text and "Private" in text
    page.uncheck("#phone-enable")
    ctx.close()


def test_phone_loads_manifest_and_shows_unreachable(browser, server):
    lap, ph, link = pair(browser, server, "Home screen")
    base = link.split("/pair/")[0]
    assert ph.locator("link[rel=manifest]").get_attribute("href") == "/static/manifest.webmanifest"
    assert ph.locator("link[rel=apple-touch-icon]").count() == 1
    assert ph.locator("meta[name=apple-mobile-web-app-capable]").get_attribute("content") == "yes"
    other = phone_ctx(browser)  # a fresh context without the cookie, as a browser fetching the manifest
    for path in (
        "/static/manifest.webmanifest",
        "/static/icons/icon-192.png",
        "/static/icons/apple-touch-icon.png",
    ):
        assert other.request.get(base + path).status == 200, path
    other.close()
    lap.reload()
    lap.wait_for_selector("#phone-enable")
    lap.uncheck("#phone-enable")  # the laptop stops listening while the phone page is open
    lap.wait_for_timeout(800)
    ph.evaluate("document.querySelector('.tab[data-view=history]').click()")
    ph.wait_for_selector("#unreachable", timeout=10000)
    assert "Cannot reach the laptop" in ph.inner_text("#unreachable")
    ph.context.close()
    lap.context.close()
