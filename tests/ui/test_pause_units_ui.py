"""Settings > Studio "Pause between requests" with a unit choice (demo mode, never Google):
conversion both ways, the largest-unit rule, bounds with a plain message, save and reload, axe."""

import json
import os
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe-core" / "axe.min.js"
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")


@pytest.fixture(scope="module")
def server():
    with DemoServer(rows=20) as srv:
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


def settings_page(browser, server, width=1440, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, bypass_csp=True)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url + "&v=settings")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#pause-value")
    return page


def saved_pause(page):
    js = "async () => (await (await fetch('/api/settings', {credentials: 'same-origin'})).json())"
    return page.evaluate(js)["studio_pause_s"]


def test_conversion_rules(browser, server):
    page = settings_page(browser, server)
    got = page.evaluate(
        """async () => {
          const d = await import('/static/js/duration.js');
          return {
            show: [45, 90, 120, 3600, 5400, 86400, 30].map(s => d.toDisplay(s)),
            save: [[2, 'minutes'], [1, 'hours'], [45, 'seconds'], [24, 'hours'],
                   ['', 'minutes'], ['1.5', 'hours'], ['-3', 'seconds']]
                    .map(([v, u]) => d.toSeconds(v, u)),
            bounds: [[29, 'seconds'], [25, 'hours'], [1, 'minutes'], [24, 'hours'], ['', 'hours']]
                    .map(([v, u]) => d.checkPause(v, u, 30, 86400)),
          };
        }"""
    )
    assert got["show"] == [
        {"value": 45, "unit": "seconds"},
        {"value": 90, "unit": "seconds"},
        {"value": 2, "unit": "minutes"},
        {"value": 1, "unit": "hours"},
        {"value": 90, "unit": "minutes"},
        {"value": 24, "unit": "hours"},
        {"value": 30, "unit": "seconds"},
    ]
    assert got["save"] == [120, 3600, 45, 86400, None, None, None]
    b = got["bounds"]
    assert "error" in b[0] and "error" in b[1] and "error" in b[4]
    assert b[2] == {"seconds": 60} and b[3] == {"seconds": 86400}
    assert "30 seconds to 24 hours" in b[0]["error"]
    page.context.close()


def test_old_45_shows_as_seconds_then_minutes_save_and_reload(browser, server):
    page = settings_page(browser, server)
    assert page.input_value("#pause-value") == "45" and page.input_value("#pause-unit") == "seconds"
    page.fill("#pause-value", "3")
    page.select_option("#pause-unit", "minutes")  # change on the select commits
    page.wait_for_function("document.querySelector('.toast') !== null")
    assert saved_pause(page) == 180
    page.reload()
    page.wait_for_selector("#pause-value")
    assert page.input_value("#pause-value") == "3" and page.input_value("#pause-unit") == "minutes"
    # an hour
    page.fill("#pause-value", "1")
    page.select_option("#pause-unit", "hours")
    page.wait_for_timeout(400)
    assert saved_pause(page) == 3600
    page.reload()
    page.wait_for_selector("#pause-value")
    assert page.input_value("#pause-value") == "1" and page.input_value("#pause-unit") == "hours"
    assert page.errors == []
    page.context.close()


def test_out_of_range_and_empty_show_a_message_and_do_not_save(browser, server):
    page = settings_page(browser, server)
    before = saved_pause(page)
    page.select_option("#pause-unit", "hours")
    page.fill("#pause-value", "25")
    page.press("#pause-value", "Tab")
    page.wait_for_function("document.querySelector('#pause-msg').textContent.length > 0")
    assert "30 seconds to 24 hours" in page.text_content("#pause-msg")
    assert page.get_attribute("#pause-value", "aria-invalid") == "true"
    assert saved_pause(page) == before
    page.fill("#pause-value", "")
    page.press("#pause-value", "Tab")
    assert "whole number" in page.text_content("#pause-msg")
    assert saved_pause(page) == before
    page.fill("#pause-value", "10")
    page.select_option("#pause-unit", "seconds")
    assert "30 seconds to 24 hours" in page.text_content("#pause-msg")
    assert saved_pause(page) == before
    page.context.close()


@pytest.mark.parametrize("width", [320, 375, 1440])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_layout_targets_and_axe(browser, server, width, scheme):
    page = settings_page(browser, server, width=width, scheme=scheme)
    for sel in ("#pause-value", "#pause-unit"):
        box = page.locator(sel).bounding_box()
        assert box["height"] >= 40, sel
        assert box["x"] + box["width"] <= width, sel
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    over = page.evaluate(
        "[...document.querySelectorAll('.set-card')].filter(c => c.scrollWidth > c.clientWidth).length"
    )
    assert over == 0  # no settings card wider than itself (Studio was 8 px over at 375)
    # labels for screen readers
    assert page.evaluate("document.getElementById('pause-value').labels.length === 0")
    assert page.get_attribute("#pause-value", "aria-labelledby") == "pause-label"
    assert page.get_attribute("#pause-unit", "aria-label")
    page.add_script_tag(path=str(AXE))
    js = (
        "async () => (await axe.run(document.querySelector('.pause-field'),"
        " {runOnly: ['wcag2a','wcag2aa']})).violations.map(v => v.id)"
    )
    res = page.evaluate(js)
    assert res == []
    page.context.close()


def test_api_stores_seconds(browser, server):
    page = settings_page(browser, server)
    page.fill("#pause-value", "90")
    page.select_option("#pause-unit", "seconds")
    page.press("#pause-value", "Tab")
    page.wait_for_timeout(400)
    assert saved_pause(page) == 90
    assert json.dumps(saved_pause(page)) == "90"
    page.context.close()
