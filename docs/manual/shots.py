"""Capture the manual's screenshots from DEMO MODE only (synthetic accounts and notebooks, no
Google, no real titles or emails). Light theme. Each shot is cropped to the part that matters
and marked with numbered circles that match the numbered steps in the manual.

Usage (from the repo root):  python -m docs.manual.shots [out_dir]
Writes PNG files to docs/manual/shots/ by default. The demo server runs with a made-up HOME
folder, so the folder picker never shows a real user name. Pairing codes shown are used up
before the run ends, so none of them works.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.ui.demo_server import DemoServer  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "shots"
MARK = "#e8590c"  # annotation colour: stands out from the app's blue
FAST = {"NLH_DEMO_ASK_S": "0.4", "NLH_DEMO_GEN_S": "3", "NLH_DEMO_POLL_S": "0.4", "NLH_DEMO_PAUSE_S": "1"}

MARK_JS = """
(items) => {
  document.querySelectorAll('.manual-mark').forEach(e => e.remove());
  for (const [r, n, side] of items) {
    const box = document.createElement('div');
    box.className = 'manual-mark';
    Object.assign(box.style, {position: 'fixed', left: (r.x - 4) + 'px', top: (r.y - 4) + 'px',
      width: (r.width + 8) + 'px', height: (r.height + 8) + 'px', border: '3px solid MARK',
      borderRadius: '8px', zIndex: 99998, pointerEvents: 'none', boxSizing: 'border-box'});
    document.body.appendChild(box);
    if (!n) continue;
    const c = document.createElement('div');
    c.className = 'manual-mark';
    c.textContent = String(n);
    const size = 28;
    let x = r.x - size - 8, y = r.y + r.height / 2 - size / 2;
    if (side === 'right') x = r.x + r.width + 8;
    if (side === 'top') { x = r.x + r.width / 2 - size / 2; y = r.y - size - 8; }
    if (side === 'bottom') { x = r.x + r.width / 2 - size / 2; y = r.y + r.height + 8; }
    if (side === 'inside') { x = r.x + r.width - size - 6; y = r.y + r.height / 2 - size / 2; }
    x = Math.max(2, Math.min(x, innerWidth - size - 2));
    y = Math.max(2, y);
    Object.assign(c.style, {position: 'fixed', left: x + 'px', top: y + 'px', width: size + 'px',
      height: size + 'px', borderRadius: '50%', background: 'MARK', color: '#fff',
      font: '700 16px/28px -apple-system, system-ui, sans-serif', textAlign: 'center', zIndex: 99999,
      boxShadow: '0 1px 4px rgba(0,0,0,.35)', pointerEvents: 'none'});
    document.body.appendChild(c);
  }
}
""".replace("MARK", MARK)


def _boxes(pg, selector):
    """Visible boxes for a Playwright selector (text selectors allowed)."""
    out = []
    for loc in pg.locator(selector).all():
        try:
            b = loc.bounding_box()
        except Exception:
            b = None
        if b and b["width"] and b["height"]:
            out.append(b)
    return out


class Shots:
    """One demo server and browser for the whole run."""

    def __init__(self, srv, browser):
        self.srv, self.b = srv, browser
        self.made: list[str] = []

    def api(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.srv.base + path,
            data=data,
            method="POST" if body is not None else "GET",
            headers={"X-NLH-Token": self.srv.token, "Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(req, timeout=60))

    def page(self, query="", width=1280, height=900, phone=False, ready=None):
        ctx = self.b.new_context(
            viewport={"width": width, "height": height},
            color_scheme="light",
            bypass_csp=True,
            device_scale_factor=2,
            has_touch=phone,
            is_mobile=phone,
        )
        pg = ctx.new_page()
        pg.goto(self.srv.url + (("&" + query) if query else ""))
        pg.wait_for_selector("body[data-ready='1']", timeout=60000)
        if ready:
            pg.wait_for_selector(ready, timeout=60000)
        pg.wait_for_timeout(400)
        return pg

    def save(self, pg, name, marks=(), crop=None, pad=14, full=False):
        """Mark, crop and save. marks: (selector, number or None, side, nth). crop: selectors whose
        union is kept, plus pad. Missing elements are reported, never guessed."""
        items = []
        for m in marks:
            m = list(m)
            sel, n = m[0], (m[1] if len(m) > 1 else None)
            side = m[2] if len(m) > 2 else "left"
            nth = m[3] if len(m) > 3 else 0
            boxes = _boxes(pg, sel)
            if len(boxes) > (nth or 0):
                items.append([boxes[nth or 0], n, side])
            else:
                print(f"  MISSING mark {n}: {sel}", flush=True)
        pg.evaluate(MARK_JS, items)
        pg.wait_for_timeout(150)
        clip = None
        if crop:
            boxes = [b for sel in crop for b in _boxes(pg, sel)]
            if boxes:
                left = min(b["x"] for b in boxes)
                top = min(b["y"] for b in boxes)
                right = max(b["x"] + b["width"] for b in boxes)
                bottom = max(b["y"] + b["height"] for b in boxes)
                vw, vh = pg.viewport_size["width"], pg.viewport_size["height"]
                x, y = max(0, left - pad - 36), max(0, top - pad - 36)
                clip = {
                    "x": x,
                    "y": y,
                    "width": min(vw, right + pad + 36) - x,
                    "height": min(vh, bottom + pad + 36) - y,
                }
            else:
                print(f"  MISSING crop: {crop}", flush=True)
        path = OUT / f"{name}.png"
        pg.screenshot(path=str(path), clip=clip, full_page=full and not clip)
        self.made.append(path.name)
        print("shot", path.name, flush=True)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.png"):
        f.unlink()
    fake_home = tempfile.mkdtemp(prefix="nlh-manual-home-")
    old = {k: os.environ.get(k) for k in [*FAST, "HOME"]}
    os.environ.update(FAST)
    os.environ["HOME"] = fake_home  # the folder picker's "home" is synthetic too
    try:
        with DemoServer(rows=400) as srv, sync_playwright() as p:
            b = p.chromium.launch(
                channel="chrome", headless=True, args=["--use-mock-keychain", "--password-store=basic"]
            )
            s = Shots(srv, b)
            capture(s)
            b.close()
            return s.made
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def capture(s: Shots):
    # ---------------- first start and accounts
    pg = s.page(ready="#vspacer .vrow")
    s.save(
        pg,
        "01-first-start",
        marks=[(".tabs", 1, "bottom"), ("#add-toggle", 2, "left"), ("#q", 3, "bottom")],
        crop=["header.top", "#sidebar", "#vspacer .vrow"],
        pad=0,
    )
    pg.click("#add-toggle")
    pg.wait_for_selector("#chrome-list .btn, #chrome-list button", timeout=20000)
    s.save(
        pg,
        "02-add-account",
        marks=[
            ("#add-toggle", 1, "left"),
            ("#chrome-list button >> text=/^Add$/", 2, "right"),
            ("#chrome-refresh", 3, "right"),
            ("#signin-add", 4, "right"),
        ],
        crop=["#add-panel", "#add-toggle"],
    )
    pg.context.close()

    pg = s.page(ready="#vspacer .vrow")
    pg.locator("#acct-list .acct").first.click()
    pg.wait_for_selector("#account-card button", timeout=20000)
    if pg.locator("#account-card .card-toggle").get_attribute("aria-expanded") != "true":
        pg.click("#account-card .card-toggle")  # More open: every control in the picture (D-032)
    s.save(
        pg,
        "03-account-card",
        marks=[
            ("#account-card button:has-text('Choose folder')", 1, "top"),
            ("#account-card button:has-text('Refresh notebooks')", 2, "bottom"),
            ("#account-card button:has-text('Scan folder')", 3, "top"),
        ],
        crop=["#account-card"],
    )
    pg.locator("#account-card button:has-text('Choose folder')").click()
    pg.wait_for_selector("#dlg[open]", timeout=20000)
    pg.wait_for_timeout(600)
    s.save(pg, "04-choose-folder", marks=[("#dlg .btn-primary", 1, "left")], crop=["#dlg"])
    pg.context.close()

    # ---------------- library
    pg = s.page(query="group=notebook", ready="#vspacer .vrow")
    s.save(
        pg,
        "05-library-statuses",
        marks=[(".vrow .pill", 1, "left", 0), ("#group", 2, "bottom"), ("#views-btn", 3, "left")],
        crop=["#vlist", ".toolbar"],
        pad=0,
    )
    pg.context.close()
    pg = s.page(query="q=garden%20status:new", ready="#vspacer .vrow")
    s.save(
        pg,
        "06-search",
        marks=[("#q", 1, "bottom"), (".vrow mark", 2, "left", 0)],
        crop=["#q", "#vspacer .vrow"],
    )
    pg.click("#q-help")
    pg.wait_for_timeout(500)
    s.save(pg, "07-search-help", crop=["#popover-root .helplist, #popover-root"])
    pg.context.close()
    pg = s.page(ready="#vspacer .vrow", height=1500)
    s.save(
        pg,
        "08-filters",
        marks=[
            ("#filters h3:has-text('Status')", 1, "left"),
            ("#filters h3:has-text('Owner')", 2, "left"),
            ("#filters-clear", 3, "left"),
        ],
        crop=["#sidebar"],
        pad=0,
    )
    pg.context.close()
    pg = s.page(query="status=new", ready="#vspacer .vrow")
    pg.click("#sel-new")
    s.save(
        pg,
        "09-select-new",
        marks=[("#sel-new", 1, "bottom"), ("#sel-harvest", 2, "left"), ("#sel-include-shared", 3, "top")],
        crop=[".toolbar, #sel-new", "#actionbar"],
        pad=6,
    )
    # harvest a few and show the queue
    pg.click("#sel-clear")
    for i in range(3):
        pg.locator("#vspacer .vrow input[type=checkbox]").nth(i).check()
    pg.click("#sel-harvest")
    pg.wait_for_selector("#job-box .job", timeout=20000)
    if pg.locator("#job-box .job-toggle").get_attribute("aria-expanded") != "true":
        pg.click("#job-box .job-toggle")  # Details open: each item's step (D-032)
    pg.wait_for_timeout(1500)
    s.save(pg, "10-harvest-queue", marks=[("#job-box button:has-text('Stop')", 1, "left")], crop=["#job-box"])
    pg.wait_for_function("!document.querySelector('#job-box .pill.st-running')", timeout=120000)
    pg.context.close()

    # ---------------- history, player, export, drawer, stats
    pg = s.page(query="v=history", ready=".htable")
    s.save(
        pg,
        "11-history",
        marks=[
            (".hactions button[aria-label='Play']", 1, "left", 0),
            (".hactions button[aria-label='Copy path']", 2, "top", 0),
            (".hactions button[aria-label='More actions']", 3, "right", 0),
            ("#h-filters", 4, "bottom"),
        ],
        crop=[".htable tbody tr:nth-child(-n+4)", "#h-filters", ".htable thead"],
    )
    pg.locator(".hactions button[aria-label='Play']").first.click()
    pg.wait_for_selector("#player:not(.is-hidden)")
    pg.wait_for_timeout(1200)
    s.save(
        pg,
        "12-player",
        marks=[("#player button[aria-label='Back 15 seconds']", 1, "top"), ("#player select", 2, "top")],
        crop=["#player"],
    )
    pg.locator("button", has_text="Export").first.click()
    pg.wait_for_timeout(400)
    s.save(pg, "13-export", crop=["#popover-root .menu"])
    pg.context.close()
    pg = s.page(query="status=saved", ready="#vspacer .vrow")
    pg.locator("#vspacer .vrow .rbody").first.click()
    pg.wait_for_selector("#drawer h3:has-text('Saved file')", timeout=20000)
    s.save(pg, "14-details", crop=["#drawer"], pad=0)
    pg.context.close()
    pg = s.page(query="v=stats", ready=".tile", height=1300)
    s.save(pg, "15-stats", crop=[".tile", ".stats-grid"])
    pg.context.close()

    # ---------------- settings
    pg = s.page(query="v=settings", ready=".set-card", height=1800)
    s.save(
        pg,
        "16-settings-naming",
        marks=[
            (".set-card:has(h2:has-text('File names')) input", 1, "top"),
            (".set-card:has(h2:has-text('File names')) .preview", 2, "left"),
        ],
        crop=[".set-card:has(h2:has-text('Appearance'))", ".set-card:has(h2:has-text('File names'))"],
    )
    s.save(pg, "17-settings-studio", crop=[".set-card:has(h2:has-text('Studio'))"])
    pg.context.close()

    # ---------------- studio
    s.api("/api/settings", {"studio_ask_warned": False, "studio_daily_cap": 4})
    pg = s.page(query="v=studio", ready="#studio-account", height=1400)
    pg.select_option("#studio-account", index=1)
    pg.wait_for_selector("#studio-nb-list .nb-option")
    pg.locator("#studio-nb-list .nb-option:not(.is-disabled)").first.click()
    s.save(
        pg,
        "19-studio-pick",
        marks=[
            ("#studio-account", 1, "top"),
            ("#studio-nb-find", 2, "top"),
            ("#studio-ask", 3, "right"),
            (".nb-option.is-disabled", 4, "inside", 0),
        ],
        crop=[".studio-card:nth-of-type(1)", "#studio-ask", "#studio-cap"],
    )
    pg.click("#studio-ask")
    pg.wait_for_selector("#dlg[open]")
    s.save(pg, "20-studio-warning", crop=["#dlg"])
    pg.locator("#dlg .btn-primary").click()
    pg.wait_for_selector("#studio-plan", timeout=20000)
    if pg.locator(".plan-row").count() == 0:
        pg.click("#studio-add-row")
        pg.locator(".plan-row input").fill("Soil and compost")
    pg.evaluate("document.querySelector('#studio-plan').scrollIntoView()")
    s.save(
        pg,
        "21-studio-plan",
        marks=[
            (".plan-row input", 1, "left", 0),
            ("#studio-add-row", 2, "top"),
            ("#studio-keep", 3, "top"),
            ("#studio-raw", 4, "left"),
        ],
        crop=[".studio-plan-card"],
        pad=4,
    )
    pg.click("#studio-keep")
    pg.wait_for_selector(".kept-plan")
    pg.locator(".kept-plan").first.locator("button", has_text="Make overviews").click()
    pg.wait_for_selector(".make-panel")
    pg.evaluate("document.querySelector('.make-panel').scrollIntoView()")
    s.save(
        pg,
        "22-studio-make",
        marks=[
            (".make-row input[type=checkbox]", 1, "left", 0),
            (".make-opts select", 2, "top", 0),
            ("#studio-enqueue", 3, "right"),
        ],
        crop=[".make-panel"],
    )
    pg.click("#studio-enqueue")
    pg.wait_for_selector("#studio-start")
    pg.evaluate("document.querySelector('#view-studio').scrollTop = 0")
    s.save(pg, "23-studio-queue", marks=[("#studio-start", 1, "left")], crop=["#studio-queue"])
    pg.click("#studio-start")
    pg.wait_for_selector("#dlg[open]")
    s.save(pg, "24-studio-start-confirm", crop=["#dlg"])
    pg.locator("#dlg .btn-primary").click()
    pg.wait_for_selector(".queue-row[data-state='generating']", timeout=20000)
    s.save(pg, "25-studio-running", marks=[("#studio-cancel", 1, "left")], crop=["#studio-queue"])
    pg.wait_for_function(
        "window.__nlhTest.studio.queue() && !window.__nlhTest.studio.queue().running", timeout=180000
    )
    pg.wait_for_timeout(1500)
    s.save(
        pg,
        "26-studio-done",
        marks=[(".queue-row[data-state='done'] button:has-text('Play')", 1, "left", 0)],
        crop=["#studio-queue"],
    )
    pg.context.close()
    # usage limit: focus text with "limit" stands in for Google's refusal in demo mode
    pg = s.page(query="v=studio", ready="#studio-account", height=1400)
    s.api("/api/settings", {"studio_daily_cap": 20})  # the earlier shots used up the demo cap of 4
    plan = s.api("/api/studio/state")["plans"][0]
    s.api(
        "/api/studio/queue",
        {
            "plan_id": plan["id"],
            "items": [
                {"title": "First part", "focus": "a short one"},
                {"title": "Second part", "focus": "this one hits the limit"},
                {"title": "Third part", "focus": "never sent"},
            ],
        },
    )
    pg.reload()
    pg.wait_for_selector("#studio-start")
    pg.click("#studio-start")
    pg.locator("#dlg .btn-primary").click()
    pg.wait_for_function(
        "window.__nlhTest.studio.queue() && window.__nlhTest.studio.queue().states.includes('paused_limit')"
        " && !window.__nlhTest.studio.queue().running",
        timeout=180000,
    )
    pg.wait_for_timeout(1200)
    s.save(
        pg,
        "27-studio-limit",
        marks=[
            ("#studio-queue .notice-warn", 1, "left"),
            (".queue-row[data-state='paused_limit'] button:has-text('Queue again')", 2, "left", 0),
        ],
        crop=["#studio-queue .notice-warn", ".queue-row[data-state='paused_limit']"],
    )
    pg.context.close()
    # check needed: a row left "submitting" when the app stopped (set directly in the demo ledger)
    s.api(
        "/api/studio/queue",
        {"plan_id": plan["id"], "items": [{"title": "Interrupted part", "focus": "after a crash"}]},
    )
    db = Path(s.srv.home) / "ledger.db"  # the demo app home (nlh.paths.ledger_path)
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE studio_queue SET state='needs_review', submitted_at=datetime('now'), submit_day=date('now'),"
        " message='The app stopped while sending this request. Check NotebookLM before sending it again.'"
        " WHERE id=(SELECT MAX(id) FROM studio_queue)"
    )
    con.commit()
    con.close()
    pg = s.page(query="v=studio", ready="#studio-queue", height=1400)
    s.save(
        pg,
        "28-studio-check-needed",
        marks=[(".queue-row[data-state='needs_review'] button:has-text('Check NotebookLM')", 1, "left")],
        crop=[".queue-row[data-state='needs_review']"],
    )
    pg.context.close()

    # ---------------- shared with me
    pg = s.page(query="group=notebook&own=shared", ready="#vspacer .vrow")
    s.save(
        pg,
        "29-shared-badge",
        marks=[
            (".vgroup .rbadge-shared", 1, "right", 0),
            ("#chips .chip", 2, "bottom", 0),
            ("#sel-include-shared", 3, "top"),
        ],
        crop=["#chips", "#sel-new", "#vspacer .vrow"],
        pad=0,
    )
    pg.context.close()

    # ---------------- phone (laptop side)
    pg = s.page(query="v=settings", ready="#phone-card", height=1800)
    pg.check("#phone-enable")
    pg.wait_for_selector("#phone-pair-btn")
    pg.click("#phone-pair-btn")
    pg.wait_for_selector("#phone-pair-link")
    link = pg.inner_text("#phone-pair-link")
    pg.locator("#phone-card").scroll_into_view_if_needed()
    pg.wait_for_timeout(300)
    s.save(
        pg,
        "30-phone-settings",
        marks=[("#phone-enable", 1, "left"), ("#phone-pair-btn", 2, "right"), (".phone-qr", 3, "right")],
        crop=["#phone-card"],
    )
    # ---------------- phone (phone side): pairing uses up the code shown above
    ph = s.page(width=390, height=844, phone=True)
    ph.goto(link)
    ph.wait_for_selector("input[name=name]")
    s.save(ph, "31-phone-pair", marks=[("input[name=name]", 1, "top"), ("button[type=submit]", 2, "bottom")])
    ph.fill("input[name=name]", "My phone")
    ph.click("button[type=submit]")
    ph.wait_for_selector("#vspacer .vrow")
    ph.wait_for_timeout(600)
    s.save(ph, "32-phone-library")
    base = link.split("/pair/")[0]
    ph.goto(base + "/?v=history")
    ph.wait_for_selector(".hcard .hactions")
    ph.locator(".hactions button[aria-label='Play']").first.click()
    ph.wait_for_selector("#player:not(.is-hidden)")
    ph.wait_for_timeout(1200)
    s.save(ph, "33-phone-player", marks=[(".hcard .hactions button[aria-label='Play']", 1, "right", 0)])
    pg.reload()
    pg.wait_for_selector("#phone-devices")
    s.save(
        pg,
        "34-phone-paired-list",
        marks=[("#phone-devices button", 1, "left", 0)],
        crop=["#phone-devices", "#phone-card h3"],
    )
    pg.locator("#phone-home-help summary").click()
    s.save(pg, "35-phone-homescreen-note", crop=["#phone-home-help"])
    s.api("/api/phone/enable", {"on": False})
    ph.evaluate("document.querySelector('.tab[data-view=stats]').click()")
    ph.wait_for_selector("#unreachable", timeout=15000)
    s.save(ph, "36-phone-unreachable", crop=["#unreachable"], pad=6)
    ph.context.close()
    pg.context.close()
    # Windows wording of the firewall note, rendered by marking the page as Windows (demo only)
    ctx = s.b.new_context(
        viewport={"width": 1280, "height": 1600}, color_scheme="light", bypass_csp=True, device_scale_factor=2
    )
    pg = ctx.new_page()
    pg.route(
        "**/api/state",
        lambda route: route.fulfill(response=(r := route.fetch()), json={**r.json(), "windows": True}),
    )
    pg.goto(s.srv.url + "&v=settings")
    pg.wait_for_selector("#phone-card")
    pg.check("#phone-enable")
    pg.wait_for_selector("#phone-firewall-hint")
    s.save(
        pg,
        "37-phone-firewall-windows",
        marks=[("#phone-firewall-hint", None)],
        crop=["#phone-firewall-hint"],
        pad=6,
    )
    s.api("/api/phone/enable", {"on": False})
    ctx.close()


if __name__ == "__main__":
    made = run()
    print(f"{len(made)} shots in {OUT}")
