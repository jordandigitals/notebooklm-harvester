"""Capture review screenshots from demo mode (synthetic data only).

Usage: python -m tests.ui.screenshots <out-dir> <milestone>
Writes one PNG per state: 375 px and 1440 px side by side (ffmpeg hstack), plus
768 and 1024 px for the main view, light and dark. Also checks sideways overflow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from nlh import media
from tests.ui.demo_server import DemoServer


def _combine(left: Path, right: Path, out: Path) -> None:
    """Put two screenshots side by side on one canvas (pads the shorter one)."""
    subprocess.run(
        [
            media.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(left),
            "-i",
            str(right),
            "-filter_complex",
            "[0]pad=iw+24:ih:0:0:color=0x9a9a9a[l];[l][1]hstack=inputs=2[o]",
            "-map",
            "[o]",
            "-frames:v",
            "1",
            "-compression_level",
            "9",
            str(out),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _pad_to_same_height(a: Path, b: Path) -> tuple[Path, Path]:
    """hstack needs equal heights: pad both to the taller one."""

    def height(p):
        out = subprocess.run(
            [
                media.tool("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "stream=height",
                "-of",
                "csv=p=0",
                str(p),
            ],
            capture_output=True,
            text=True,
        ).stdout
        return int(out.strip())

    h = max(height(a), height(b))
    res = []
    for p in (a, b):
        q = p.with_name(p.stem + "-pad.png")
        subprocess.run(
            [
                media.tool("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(p),
                "-vf",
                f"pad=iw:{h}:0:0:color=0x9a9a9a",
                str(q),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        res.append(q)
    return res[0], res[1]


def capture(out_dir: Path, prefix: str) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_raw"
    tmp.mkdir(exist_ok=True)
    notes = []
    with DemoServer(rows=2000) as srv, sync_playwright() as p:
        b = p.chromium.launch(
            channel="chrome", headless=True, args=["--use-mock-keychain", "--password-store=basic"]
        )

        def shot(name, width, scheme, query="", setup=None, full=False, height=900):
            ctx = b.new_context(
                viewport={"width": width, "height": height},
                color_scheme=scheme,
                bypass_csp=True,
                device_scale_factor=1,
            )
            pg = ctx.new_page()
            pg.goto(srv.url + (("&" + query) if query else ""))
            pg.wait_for_selector("body[data-ready='1']", timeout=60000)
            pg.wait_for_timeout(500)
            if setup:
                setup(pg)
                pg.wait_for_timeout(500)
            sw = pg.evaluate("document.documentElement.scrollWidth")
            if sw > width:
                notes.append(f"OVERFLOW {name} {width}px {scheme}: scrollWidth {sw}")
            wide = pg.evaluate(
                "() => [...document.querySelectorAll("
                "'.views, .view:not(.is-hidden), .view:not(.is-hidden) *')]"
                ".filter(e => e.clientWidth > 1 && e.scrollWidth > e.clientWidth + 1"
                " && getComputedStyle(e).overflowX !== 'visible'"
                " && !e.matches('.hwrap, .sr-only, .rtitle, .rmeta, .rmeta > span, .lbl, .gname, .an, .ae,"
                " .pl-title, .pl-sub, .hbar-name, .chip .lbl, .check .lbl, .folder code, .tt,"
                " .jt, .jerr, .jh > span, .bar'))"
                ".map(e => e.tagName + '#' + e.id + '.' + e.className)"
            )
            if wide:
                notes.append(f"OVERFLOW {name} {width}px {scheme}: {wide[:3]}")
            path = tmp / f"{name}-{width}-{scheme}.png"
            pg.screenshot(path=str(path), full_page=full)
            ctx.close()
            return path

        def pair(n, name, scheme, **kw):
            a = shot(name, 375, scheme, **kw)
            c = shot(name, 1440, scheme, **kw)
            pa, pc = _pad_to_same_height(a, c)
            out = out_dir / f"{prefix}-shot-{n:02d}-{name}-{scheme}.png"
            _combine(pa, pc, out)
            return out.name

        made = []
        if prefix.endswith("-studio-s2-mac"):
            post = (
                "async (b) => { await fetch('/api/settings', {method: 'POST', headers: {'X-NLH-Req': '1',"
                " 'Content-Type': 'application/json'}, body: JSON.stringify(b)}); }"
            )

            def kept(pg, focus=None):
                pg.evaluate(post, {"studio_ask_warned": True, "studio_daily_cap": 20})
                # Clear what earlier shots left queued, so each shot shows only its own run.
                pg.evaluate(
                    "async () => { const q = await (await fetch('/api/studio/queue')).json();"
                    " const h = {method: 'POST', headers: {'X-NLH-Req': '1'}};"
                    " for (const r of q.rows) if (r.state === 'queued')"
                    " await fetch(`/api/studio/queue/${r.id}/drop`, h); }"
                )
                pg.reload()
                pg.wait_for_selector("body[data-ready='1']")
                pg.wait_for_selector("#studio-account")
                pg.select_option("#studio-account", index=1)
                pg.wait_for_selector("#studio-nb-list .nb-option")
                pg.fill("#studio-nb-find", "comets")
                pg.locator("#studio-nb-list .nb-option").first.click()
                pg.click("#studio-ask")
                pg.wait_for_selector("#studio-plan", timeout=20000)
                if pg.locator(".plan-row").count() == 0:
                    pg.click("#studio-add-row")
                    pg.locator(".plan-row input").fill("Manual overview")
                for i, text in enumerate(focus or []):
                    if i >= pg.locator(".plan-row").count():
                        pg.click("#studio-add-row")
                        pg.locator(".plan-row").last.locator("input").fill(f"Extra part {i}")
                    pg.locator(".plan-row").nth(i).locator("textarea").fill(text)
                pg.click("#studio-keep")
                pg.wait_for_selector(".kept-plan")

            def make_panel(pg):
                kept(pg)
                pg.locator(".kept-plan").first.locator("button", has_text="Make overviews").click()
                pg.wait_for_selector(".make-panel")
                pg.locator(".make-panel").scroll_into_view_if_needed()

            def queued(pg, focus=None):
                kept(pg, focus)
                pg.locator(".kept-plan").first.locator("button", has_text="Make overviews").click()
                pg.click("#studio-enqueue")
                pg.wait_for_selector("#studio-queue")

            def started(pg, focus=None):
                queued(pg, focus)
                pg.click("#studio-start")
                pg.locator("#dlg[open]").wait_for()

            def running(pg):
                started(pg)
                pg.locator("#dlg .btn-primary").click()
                pg.wait_for_selector(".queue-row[data-state='generating']", timeout=15000)
                pg.evaluate("document.querySelector('#view-studio').scrollTop = 0")

            def finished(pg, focus=None):
                started(pg, focus)
                pg.locator("#dlg .btn-primary").click()
                pg.wait_for_function(
                    "window.__nlhTest.studio.queue() && !window.__nlhTest.studio.queue().running"
                    " && window.__nlhTest.studio.queue().states.every(s => s !== 'queued')",
                    timeout=120000,
                )
                pg.wait_for_timeout(600)
                pg.evaluate("document.querySelector('#view-studio').scrollTop = 0")

            q = "v=studio"
            made.append(pair(1, "studio-make-panel", "light", query=q, setup=make_panel, height=1600))
            made.append(pair(2, "studio-start-confirm", "dark", query=q, setup=started))
            made.append(pair(3, "studio-queue-running", "light", query=q, setup=running, height=1300))
            made.append(pair(4, "studio-queue-done-play", "dark", query=q, setup=finished, height=1300))
            path = shot("studio-queue", 320, "light", query=q, setup=running, height=1500)
            out = out_dir / f"{prefix}-shot-06-studio-queue-320-light.png"
            out.write_bytes(path.read_bytes())
            made.append(out.name)
            made.append(
                pair(
                    5,
                    "studio-queue-limit",
                    "light",
                    query=q,
                    setup=lambda pg: finished(pg, ["short one", "this one hits the limit", "never sent"]),
                    height=1300,
                )
            )
            b.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return made + notes
        if prefix.endswith("-studio-s1-mac"):
            post = (
                "async (b) => { await fetch('/api/settings', {method: 'POST', headers: {'X-NLH-Req': '1',"
                " 'Content-Type': 'application/json'}, body: JSON.stringify(b)}); }"
            )

            def choose(pg):
                pg.wait_for_selector("#studio-account")
                pg.select_option("#studio-account", index=1)
                pg.wait_for_selector("#studio-nb-list .nb-option")
                pg.locator("#studio-nb-list .nb-option").first.click()

            def asked(pg, warn=False):
                pg.evaluate(post, {"studio_ask_warned": not warn})
                choose(pg)
                pg.click("#studio-ask")
                if warn:
                    pg.wait_for_selector("#dlg[open]")
                    return
                pg.wait_for_selector("#studio-plan", timeout=20000)
                pg.evaluate("document.querySelector('#view-studio').scrollTop = 0")

            def editing(pg):
                asked(pg)
                pg.click("#studio-add-row")
                last = pg.locator(".plan-row").last
                last.locator("input").fill("Bees and pollination")
                last.locator("textarea").fill("Only the parts about bees, flowers and honey")
                pg.locator(".plan-row").first.locator("input").fill("Soil, worms and fungi")

            def kept(pg):
                editing(pg)
                pg.click("#studio-keep")
                pg.wait_for_selector(".kept-plan")
                choose(pg)

            def shared(pg):
                accts = pg.evaluate("async () => (await (await fetch('/api/studio/state')).json()).accounts")
                pg.evaluate(post, {"studio_shared_accounts": [accts[1]["id"]]})
                pg.reload()
                pg.wait_for_selector("#studio-account")
                pg.select_option("#studio-account", str(accts[1]["id"]))
                pg.wait_for_selector(".studio-card .notice-warn")

            def settings_studio(pg):
                pg.wait_for_selector(".set-card")
                pg.locator(".set-card h2", has_text="Studio").scroll_into_view_if_needed()

            q = "v=studio"
            made.append(
                pair(
                    1,
                    "studio-empty",
                    "light",
                    query=q,
                    setup=lambda pg: pg.wait_for_selector("#studio-account"),
                )
            )
            made.append(pair(2, "studio-warning", "dark", query=q, setup=lambda pg: asked(pg, warn=True)))
            made.append(pair(3, "studio-plan", "light", query=q, setup=asked, height=1900))
            made.append(pair(4, "studio-editing", "dark", query=q, setup=editing, height=1900))
            made.append(pair(5, "studio-kept", "light", query=q, setup=kept, height=1700))
            made.append(pair(6, "studio-shared-account", "light", query=q, setup=shared))
            made.append(pair(7, "settings-studio", "dark", query="v=settings", setup=settings_studio))
            path = shot("studio-plan", 320, "light", query=q, setup=asked, height=2200)
            out = out_dir / f"{prefix}-shot-08-studio-plan-320-light.png"
            out.write_bytes(path.read_bytes())
            made.append(out.name)
            b.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return made + notes
        if prefix.endswith("-u4-mac"):
            made.append(pair(1, "library", "light", query="group=notebook"))
            made.append(pair(2, "library", "dark", query="status=new,failed&q=garden"))
            made.append(
                pair(
                    3,
                    "history",
                    "light",
                    query="v=history",
                    setup=lambda pg: pg.wait_for_selector(".htable, .hcard"),
                )
            )
            made.append(
                pair(
                    4,
                    "history",
                    "dark",
                    query="v=history",
                    setup=lambda pg: pg.wait_for_selector(".htable, .hcard"),
                )
            )
            made.append(
                pair(
                    5,
                    "stats",
                    "light",
                    query="v=stats",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".tile"),
                )
            )
            made.append(
                pair(
                    6,
                    "settings",
                    "dark",
                    query="v=settings",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".set-card"),
                )
            )
            made.append(
                pair(
                    7,
                    "drawer",
                    "dark",
                    query="status=saved",
                    setup=lambda pg: (
                        pg.locator("#vspacer .vrow .rbody").first.click(),
                        pg.wait_for_selector("#drawer h3:has-text('Saved file')"),
                    ),
                )
            )
            for n, (w, scheme, q, name) in enumerate(
                [
                    (768, "light", "", "library"),
                    (1024, "dark", "v=history", "history"),
                    (320, "light", "", "library"),
                ],
                start=8,
            ):
                path = shot(
                    name,
                    w,
                    scheme,
                    query=q,
                    setup=(lambda pg: pg.wait_for_selector(".htable, .hcard")) if name == "history" else None,
                )
                out = out_dir / f"{prefix}-shot-{n:02d}-{name}-{w}-{scheme}.png"
                out.write_bytes(path.read_bytes())
                made.append(out.name)
            b.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return made + notes
        if prefix.endswith("-u3-mac"):

            def start_harvest(pg, q):
                pg.goto(srv.base + "/?" + q)
                pg.wait_for_selector("body[data-ready='1']")
                pg.click("#sel-visible")
                pg.click("#sel-harvest")
                if pg.locator("dialog[open]").count():
                    pg.locator("dialog[open] .btn-primary").click()

            made.append(
                pair(
                    1,
                    "queue-running",
                    "light",
                    setup=lambda pg: (
                        start_harvest(pg, "status=new,failed&q=orchard"),
                        pg.wait_for_timeout(6000),
                    ),
                )
            )
            made.append(
                pair(
                    2,
                    "queue-failed-retry",
                    "dark",
                    setup=lambda pg: (
                        start_harvest(pg, "status=new,failed&inc=glacier"),
                        pg.locator("section.job button:has-text('Retry')").wait_for(timeout=180000),
                    ),
                )
            )
            made.append(
                pair(
                    3,
                    "settings",
                    "light",
                    query="v=settings",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".set-card"),
                )
            )
            made.append(
                pair(
                    4,
                    "settings",
                    "dark",
                    query="v=settings",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".set-card"),
                )
            )
            made.append(
                pair(
                    5,
                    "drawer",
                    "light",
                    query="status=saved",
                    setup=lambda pg: (
                        pg.locator("#vspacer .vrow .rbody").first.click(),
                        pg.wait_for_selector("#drawer h3:has-text('Saved file')"),
                    ),
                )
            )
            made.append(
                pair(
                    6,
                    "shortcuts",
                    "dark",
                    setup=lambda pg: (
                        pg.focus("#vlist"),
                        pg.keyboard.press("?"),
                        pg.wait_for_selector("dialog[open] .keys"),
                    ),
                )
            )
            made.append(
                pair(
                    7,
                    "template-preview",
                    "light",
                    query="v=settings",
                    setup=lambda pg: (
                        pg.wait_for_selector(".set-card input.input"),
                        pg.locator(".set-card input.input").first.fill("{account}/{date} {title}"),
                        pg.wait_for_timeout(700),
                    ),
                )
            )
            b.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return made + notes
        if prefix.endswith("-u2-mac"):
            hist = "v=history"
            made.append(
                pair(
                    1,
                    "history",
                    "light",
                    query=hist,
                    setup=lambda pg: pg.wait_for_selector(".hcard, .htable"),
                )
            )
            made.append(
                pair(
                    2, "history", "dark", query=hist, setup=lambda pg: pg.wait_for_selector(".hcard, .htable")
                )
            )
            made.append(
                pair(
                    3,
                    "history-filters",
                    "light",
                    query=hist,
                    setup=lambda pg: (pg.wait_for_selector("#h-filters"), pg.click("#h-filters")),
                )
            )
            made.append(
                pair(
                    4,
                    "export-menu",
                    "dark",
                    query=hist,
                    setup=lambda pg: (
                        pg.wait_for_selector(".htable, .hcard"),
                        pg.locator("#view-history button", has_text="Export").click(),
                    ),
                )
            )
            made.append(
                pair(
                    5,
                    "player",
                    "light",
                    query=hist,
                    setup=lambda pg: (
                        pg.wait_for_selector(".hactions"),
                        pg.locator(".hactions button[aria-label='Play']").first.click(),
                        pg.wait_for_timeout(1500),
                    ),
                )
            )
            made.append(
                pair(
                    6,
                    "stats",
                    "light",
                    query="v=stats",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".tile"),
                )
            )
            made.append(
                pair(
                    7,
                    "stats",
                    "dark",
                    query="v=stats",
                    full=True,
                    setup=lambda pg: pg.wait_for_selector(".tile"),
                )
            )
            made.append(
                pair(
                    8,
                    "history-search-empty",
                    "light",
                    query=hist,
                    setup=lambda pg: (
                        pg.wait_for_selector("#view-history input"),
                        pg.fill("#view-history input[type=search]", "zzznothing"),
                        pg.wait_for_timeout(800),
                    ),
                )
            )
            b.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
            return made + notes
        made.append(pair(1, "library", "light"))
        made.append(pair(2, "library", "dark"))
        made.append(
            pair(
                3,
                "filtered-grouped",
                "light",
                query="status=new,failed&group=notebook&q=garden",
                setup=lambda pg: pg.evaluate("document.getElementById('vlist').scrollTop = 300"),
            )
        )
        made.append(
            pair(
                4,
                "selection-bar",
                "dark",
                query="status=new&group=account",
                setup=lambda pg: pg.click("#sel-new"),
            )
        )
        made.append(pair(5, "empty-state", "light", query="q=zzzznothing"))
        made.append(pair(6, "search-help", "light", setup=lambda pg: pg.click("#q-help")))
        made.append(
            pair(
                7,
                "account-scope",
                "light",
                query="scope=2",
            )
        )
        made.append(pair(8, "views-menu", "dark", setup=lambda pg: pg.click("#views-btn")))
        for n, (w, scheme) in enumerate([(768, "light"), (1024, "dark")], start=9):
            path = shot("library", w, scheme)
            out = out_dir / f"{prefix}-shot-{n:02d}-library-{w}-{scheme}.png"
            out.write_bytes(path.read_bytes())
            made.append(out.name)
        mob = shot("sidebar-mobile", 375, "dark", setup=lambda pg: pg.click("#sidebar-open"), height=1100)
        out = out_dir / f"{prefix}-shot-11-sidebar-mobile-375-dark.png"
        out.write_bytes(mob.read_bytes())
        made.append(out.name)
        b.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    return made + notes


if __name__ == "__main__":
    for line in capture(Path(sys.argv[1]), sys.argv[2]):
        print(line)
