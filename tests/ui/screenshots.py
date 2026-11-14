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
                " .pl-title, .pl-sub, .hbar-name, .chip .lbl, .check .lbl, .folder code, .tt'))"
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
                        pg.locator(".hactions button", has_text="Play").first.click(),
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
