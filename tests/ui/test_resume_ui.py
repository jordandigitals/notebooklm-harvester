"""Resume playback (P2-J) in a real browser, demo mode with a 3 minute synthetic sample.

Each check reads the stored position back from the server or the ledger, not only the page.
"""

import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

import pytest

from tests.ui.demo_server import DemoServer

pw = pytest.importorskip("playwright.sync_api")
CHANNEL = os.environ.get("NLH_UI_CHANNEL", "chrome")
SAMPLE_S = 180
ENV = {"NLH_DEMO_SAMPLE_S": str(SAMPLE_S)}
LOADED = "() => { const a = document.querySelector('#player audio'); return a && a.readyState >= 1; }"
PLAYING = (
    "() => { const a = document.querySelector('#player audio'); return a && a.readyState >= 1 && !a.paused; }"
)


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


@pytest.fixture(scope="module")
def srv():
    with DemoServer(rows=30, env=ENV) as s:
        yield s


def first_row(srv) -> dict:
    rows = [r for r in srv.api("/api/history/page")["rows"] if r["playable"]]
    assert rows, "the demo needs a playable saved file"
    return rows[0]


def stored(srv, harvest_id) -> float:
    return srv.api(f"/api/position/{harvest_id}")["position_s"]


def post(srv, harvest_id, seconds):
    req = urllib.request.Request(
        f"{srv.base}/api/position/{harvest_id}",
        data=json.dumps({"position_s": seconds}).encode(),
        headers={"X-NLH-Token": srv.token, "Content-Type": "application/json"},
        method="POST",
    )
    return json.load(urllib.request.urlopen(req, timeout=10))


def open_history(page, srv):
    page.goto(srv.url)
    page.wait_for_selector("body[data-ready='1']")
    page.click(".tab[data-view='history']")
    page.wait_for_selector(".htable tbody tr")


def play_first(page, srv, row):
    first = page.locator(".htable tbody tr").first
    assert row["title"] in first.inner_text()
    first.locator(".hactions button").first.click()
    page.wait_for_function(PLAYING)
    time.sleep(0.6)  # the saved place is applied once the length is known


def audio_time(page) -> float:
    return page.evaluate("() => document.querySelector('#player audio').currentTime")


def seek(page, seconds):
    page.evaluate(f"() => {{ document.querySelector('#player audio').currentTime = {seconds}; }}")


def wait_stored(srv, harvest_id, cond, timeout=6.0) -> float:
    end = time.time() + timeout
    while time.time() < end:
        v = stored(srv, harvest_id)
        if cond(v):
            return v
        time.sleep(0.2)
    return stored(srv, harvest_id)


def test_seek_reload_resumes_five_seconds_back_with_the_line(browser, srv):
    row = first_row(srv)
    post(srv, row["id"], 0)
    time.sleep(1.1)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    open_history(page, srv)
    play_first(page, srv, row)
    assert audio_time(page) < 5  # nothing saved: from the start
    seek(page, 100)
    v = wait_stored(srv, row["id"], lambda x: 99 <= x <= 103)
    assert 99 <= v <= 103, v  # the stored row, not the page
    page.evaluate("() => document.querySelector('#player audio').pause()")
    time.sleep(1.2)
    saved = stored(srv, row["id"])
    open_history(page, srv)  # reload
    play_first(page, srv, row)
    t = audio_time(page)
    assert saved - 5 - 0.5 <= t <= saved - 5 + 1.5, (saved, t)
    line = page.locator("#pl-resume")
    assert line.is_visible()
    shown = f"Continuing from {int(saved) // 60}:{int(round(saved)) % 60:02d}"
    assert page.locator("#pl-resume-text").inner_text() == shown
    assert page.locator("#pl-sub").is_hidden()
    # the row marker shows the saved place without a reload
    marker = page.locator(f"progress.row-progress[data-hid='{row['id']}']").first
    assert "Listened to" in marker.get_attribute("aria-label")
    page.click("#pl-restart")
    time.sleep(0.4)
    assert audio_time(page) < 2
    assert line.is_hidden()
    ctx.close()


def test_finished_clears_and_next_play_starts_at_zero(browser, srv):
    row = first_row(srv)
    post(srv, row["id"], 60)
    time.sleep(1.1)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    open_history(page, srv)
    play_first(page, srv, row)
    time.sleep(1.1)
    seek(page, SAMPLE_S - 8)  # inside the 15 s tail
    v = wait_stored(srv, row["id"], lambda x: x == 0)
    assert v == 0
    page.wait_for_function("() => document.querySelector('#player audio').ended", timeout=20000)
    time.sleep(0.5)
    assert stored(srv, row["id"]) == 0
    open_history(page, srv)
    play_first(page, srv, row)
    assert audio_time(page) < 3
    assert page.locator("#pl-resume").is_hidden()
    ctx.close()


def test_a_changed_file_drops_the_position(browser, srv):
    row = first_row(srv)
    time.sleep(1.1)
    assert post(srv, row["id"], 90)["saved"] is True
    assert stored(srv, row["id"]) == 90
    path = Path(row["path"])
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\0" * 64)  # the same row now points at a different file
        assert stored(srv, row["id"]) == 0
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        open_history(page, srv)
        play_first(page, srv, row)
        assert audio_time(page) < 3
        ctx.close()
    finally:
        path.write_bytes(original)


def test_another_page_continues_where_the_first_left(browser, srv):
    row = first_row(srv)
    post(srv, row["id"], 0)
    time.sleep(1.1)
    a = browser.new_context(viewport={"width": 1280, "height": 900})
    pa = a.new_page()
    open_history(pa, srv)
    play_first(pa, srv, row)
    seek(pa, 75)
    wait_stored(srv, row["id"], lambda x: 74 <= x <= 78)
    pa.evaluate("() => document.querySelector('#player audio').pause()")
    time.sleep(1.2)
    saved = stored(srv, row["id"])
    a.close()
    b = browser.new_context(viewport={"width": 375, "height": 800})  # a fresh browser: nothing local
    pb = b.new_page()
    open_history_phone(pb, srv)
    pb.locator(".hcard").first.locator(".hactions button").first.click()
    pb.wait_for_function(LOADED)
    time.sleep(0.8)
    t = audio_time(pb)
    assert saved - 5.5 <= t <= saved - 3.5, (saved, t)
    b.close()


def open_history_phone(page, srv):
    page.goto(srv.url)
    page.wait_for_selector("body[data-ready='1']")
    page.click(".tab[data-view='history']")
    page.wait_for_selector(".hcard")


def test_quit_during_play_keeps_the_last_seconds(browser):
    with DemoServer(rows=30, env=ENV) as own:  # its own server: Quit really stops it
        row = first_row(own)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        open_history(page, own)
        play_first(page, own, row)
        seek(page, 120)
        wait_stored(own, row["id"], lambda x: 119 <= x <= 123)
        time.sleep(3.0)  # play on, less than one 5 s save interval
        at_quit = audio_time(page)
        page.click("#quit")
        page.locator("dialog[open] .btn-primary").click()
        page.wait_for_selector("main.empty h2")
        ctx.close()
        home = Path(own.home)
        own.proc.wait(timeout=20)
        c = sqlite3.connect(home / "ledger.db")
        sql = "SELECT position_s FROM play_positions WHERE harvest_id = ?"
        kept = c.execute(sql, (row["id"],)).fetchone()[0]
        c.close()
        assert at_quit - 1.5 <= kept <= at_quit + 1.5, (at_quit, kept)
        assert kept >= 122  # newer than the last regular save at 120
