"""Local web page (D-015, D-016): Flask on 127.0.0.1 with a per-launch token (R8).

All slow work (listing, harvesting, sign-ins) runs on one background worker, one
job at a time, so there is never more than one download or sign-in in flight.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import os
import queue
import re
import secrets
import socket
import string
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory

from nlh import APP_NAME, __version__, accounts, adapter, demo, harvest, media, naming, paths
from nlh import settings as app_settings
from nlh.ledger import Ledger
from nlh.log import get_logger

log = get_logger()
STATIC = Path(__file__).parent / "static"

STALE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{APP_NAME}}</title>
<link rel="stylesheet" href="/static/app.css"></head><body><main class="layout">
<section class="panel"><h1 class="brand">{{APP_NAME}}</h1>
<p>This page was opened without the app's private key, or with an old one.</p>
<p>Close this tab and use the tab the app opened, or start the app again
(double-click the launcher). It opens the right page for you.</p></section></main></body></html>"""


# ---------------------------------------------------------------- jobs


@dataclass
class Job:
    """One unit of background work and its visible progress."""

    id: str
    kind: str
    account_id: int | None
    title: str
    state: str = "queued"  # queued | running | done | failed
    lines: list[str] = field(default_factory=list)
    items: dict[str, dict] = field(default_factory=dict)
    message: str = ""
    pct: float | None = None  # overall fraction for harvests
    total: int = 0
    current: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    started_at: float | None = None
    finished_at: float | None = None

    def view(self) -> dict:
        """JSON view for the page."""
        return {
            "id": self.id,
            "kind": self.kind,
            "account_id": self.account_id,
            "title": self.title,
            "state": self.state,
            "lines": self.lines[-50:],
            "items": self.items,
            "message": self.message,
            "pct": self.pct,
            "total": self.total,
            "current": self.current,
            "cancelling": self.cancel.is_set() and self.state in ("queued", "running"),
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1)
            if self.started_at
            else 0,
        }


class Worker:
    """Single background thread that runs jobs in order."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        threading.Thread(target=self._loop, daemon=True, name="nlh-worker").start()

    def submit(self, kind: str, title: str, fn, account_id: int | None = None) -> Job:
        """Queue a job; fn receives the Job to report progress."""
        job = Job(uuid.uuid4().hex[:12], kind, account_id, title)
        self.jobs[job.id] = job
        self.order.append(job.id)
        self.q.put((job, fn))
        return job

    def busy(self) -> bool:
        """True while any job is queued or running."""
        return any(self.jobs[j].state in ("queued", "running") for j in self.order)

    def _loop(self):
        while True:
            job, fn = self.q.get()
            if job.cancel.is_set():
                job.state, job.message = "cancelled", "Cancelled before it started."
                continue
            job.state = "running"
            job.started_at = time.time()
            try:
                fn(job)
                if job.state == "running":
                    job.state = "done"
            except (accounts.AccountError, adapter.AdapterError) as exc:
                job.state, job.message = "failed", getattr(exc, "message", str(exc))
            except Exception as exc:  # last resort: plain message, detail in the log
                log.exception("job %s failed: %s", job.kind, type(exc).__name__)
                job.state, job.message = "failed", "Something went wrong. Details are in the log."
            finally:
                job.finished_at = time.time()


# ---------------------------------------------------------------- app


def create_app(token: str, ledger: Ledger | None = None) -> Flask:
    """Build the Flask app. Every request needs the launch token and a local Host."""
    app = Flask(__name__, static_folder=None)
    led = ledger or Ledger()
    worker = Worker()
    library: dict[int, list] = {}  # account id -> [(Notebook, [AudioOverview])]
    cached_at: dict[int, str] = {}  # account id -> when a cached listing was read

    cookie_name = "nlh_key"

    @app.before_request
    def guard():
        """Host check, then the page key from the URL (first open), a header or the cookie."""
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.path.startswith("/static/"):
            return None
        if request.path == "/favicon.ico":
            return "", 204
        url_key = request.args.get("t") or ""
        header_key = request.headers.get("X-NLH-Token") or ""
        cookie_key = request.cookies.get(cookie_name) or ""
        given = url_key or header_key or cookie_key
        if not given or not hmac.compare_digest(given, token):
            if request.path.startswith("/api/"):
                return jsonify(error="This page's key is out of date. Reopen the app's page."), 403
            return STALE_PAGE.replace("{{APP_NAME}}", APP_NAME), 403
        if url_key and request.method == "GET" and request.path == "/":
            # Move the key out of the address bar into an HttpOnly cookie, keep any view state.
            rest = request.args.to_dict(flat=True)
            rest.pop("t", None)
            query = "&".join(f"{k}={v}" for k, v in rest.items())
            resp = redirect("/" + (f"?{query}" if query else ""), code=302)
            resp.set_cookie(
                cookie_name, token, max_age=30 * 86400, httponly=True, samesite="Strict", path="/"
            )
            return resp
        if request.method not in ("GET", "HEAD") and not header_key:
            # Cookie-only writes must also carry the page's own header (cross-site forms cannot).
            if request.headers.get("X-NLH-Req") != "1":
                return jsonify(error="Refused: missing request header."), 403
        return None

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:"
        )
        return resp

    def body() -> dict:
        return request.get_json(silent=True) or {}

    def get_account(account_id: int):
        acct = led.account(account_id)
        if not acct:
            abort(404)
        return acct

    def account_view(a) -> dict:
        saved = led.harvests(a.id)
        return {
            "id": a.id,
            "label": a.label,
            "email": a.email,
            "chrome_profile": a.chrome_profile,
            "output_dir": a.output_dir,
            "folder_ok": bool(a.output_dir and Path(a.output_dir).is_dir()),
            # D-023: warn on Windows when the output folder itself is long.
            "long_folder": paths.is_windows() and len(a.output_dir or "") > naming.LONG_FOLDER_WARNING_CHARS,
            "status": a.last_status or "unknown",
            "last_checked": a.last_checked,
            "saved_count": len(saved),
        }

    # ------------------------------------------------------------ page

    def initial_banners() -> str:
        """Banners known before any script runs, so they never push the page down later."""
        out = []
        missing = [k for k, ok in media.tools_available().items() if not ok]
        if missing:
            out.append(
                (
                    "bad",
                    f"Missing {' and '.join(missing)}. Install it (see INSTALL.md), then restart the app.",
                )
            )
        if demo.enabled():
            out.append(
                (
                    "info",
                    "Demo mode: made-up accounts and notebooks. Nothing here touches a real Google account.",
                )
            )
        icon_for = {"bad": "alert", "info": "info"}
        return "".join(
            f'<div class="notice notice-{k}">'
            f'<svg class="icon" aria-hidden="true"><use href="#i-{icon_for[k]}"/></svg>'
            f'<span class="grow">{t}</span></div>'
            for k, t in out
        )

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return (
            html.replace("{{APP_NAME}}", APP_NAME)
            .replace("{{VERSION}}", __version__)
            .replace("{{BANNERS}}", initial_banners())
        )

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    # ------------------------------------------------------------ state

    @app.get("/api/state")
    def state():
        current = [worker.jobs[j].view() for j in worker.order[-20:]]
        portable = paths.portable_home() is not None and not os.environ.get("NLH_HOME")
        try:
            notebook_base = adapter.notebooklm_url()
        except Exception:  # library missing or changed: the page hides the link
            notebook_base = ""
        return jsonify(
            notebook_base=notebook_base,
            demo=demo.enabled(),
            interactive=adapter.interactive_available(),
            windows=paths.is_windows(),
            portable=portable,
            portable_ack=(paths.app_home() / ".portable-ack").exists(),
            app_name=APP_NAME,
            version=__version__,
            tools=media.tools_available(),
            accounts=[account_view(a) for a in led.accounts()],
            jobs=current,
        )

    @app.post("/api/quit")
    def quit_app():
        """Stop the app (a packaged app has no terminal window to close)."""
        stopper = app.config.get("stop")
        if worker.busy():
            return jsonify(error="A job is still running. Wait for it to finish, then quit."), 400
        if stopper:
            threading.Timer(0.3, stopper).start()
        return jsonify(ok=True)

    @app.get("/api/trash")
    def trash_list():
        """Folders moved to the trash by failed or refused sign-ins (FINDING-3)."""
        return jsonify(accounts.trash_entries())

    @app.post("/api/trash/clear")
    def trash_clear():
        """Remove the trash folders; the page asks first and sends confirm=true."""
        if body().get("confirm") is not True:
            return jsonify(error="Confirm first."), 400
        return jsonify(ok=True, removed=accounts.clear_trash())

    @app.post("/api/portable-ack")
    def portable_ack():
        """Remember that the user read the portable-mode warning."""
        (paths.app_home() / ".portable-ack").write_text("read\n", encoding="utf-8")
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        j = worker.jobs.get(job_id)
        if not j:
            abort(404)
        return jsonify(j.view())

    # ------------------------------------------------------------ accounts

    @app.get("/api/chrome")
    def chrome():
        return jsonify(
            [
                {"folder": p.folder, "name": p.name, "email": p.email, "added": added}
                for p, added in accounts.chrome_candidates(led)
            ]
        )

    @app.post("/api/accounts/chrome")
    def add_chrome():
        folder = str(body().get("folder", ""))

        def run(job):
            acct, _ = accounts.add_from_chrome(led, folder)
            job.account_id = acct.id
            job.message = f"Added {acct.label}."

        return jsonify(worker.submit("add", f"Adding {folder}", run).view())

    @app.post("/api/accounts/signin")
    def add_signin():
        label = str(body().get("label", "")).strip() or None

        def run(job):
            job.lines.append("A Chrome window is opening. Sign in to Google there.")
            acct, _ = accounts.add_interactive(led, label)
            job.account_id = acct.id
            job.message = f"Added {acct.label}."

        return jsonify(worker.submit("add", "Adding an account by sign-in", run).view())

    @app.post("/api/accounts/<int:account_id>/check")
    def check(account_id):
        acct = get_account(account_id)

        def run(job):
            st = accounts.check(led, acct)
            job.message = st.message
            if not st.ok:
                job.state = "failed"

        return jsonify(worker.submit("check", f"Checking {acct.label}", run, acct.id).view())

    @app.post("/api/accounts/<int:account_id>/signin")
    def signin(account_id):
        acct = get_account(account_id)

        def run(job):
            if accounts.uses_own_signin(acct):
                job.lines.append("A Chrome window is opening. Sign in to Google there.")
            st = accounts.sign_in_again(led, acct)
            job.message = st.message
            if not st.ok:
                job.state = "failed"

        return jsonify(worker.submit("signin", f"Signing in {acct.label}", run, acct.id).view())

    @app.post("/api/accounts/<int:account_id>/label")
    def label(account_id):
        try:
            accounts.set_label(led, get_account(account_id), str(body().get("label", "")))
        except accounts.AccountError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True)

    @app.post("/api/accounts/<int:account_id>/output")
    def output(account_id):
        try:
            path = accounts.set_output_dir(led, get_account(account_id), str(body().get("path", "")))
        except accounts.AccountError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True, path=str(path))

    # ------------------------------------------------------------ notebooks

    @app.post("/api/accounts/<int:account_id>/refresh")
    def refresh(account_id):
        acct = get_account(account_id)

        def run(job):
            fresh = accounts.with_session(
                led, acct, lambda: adapter.list_library(acct.profile_name, job.lines.append)
            )
            library[acct.id] = fresh
            cached_at.pop(acct.id, None)
            led.save_listing(acct.id, _dump_listing(fresh))
            n = sum(len(o) for _nb, o in fresh)
            job.message = f"{len(fresh)} notebooks, {n} audio overviews."

        return jsonify(worker.submit("refresh", f"Reading {acct.label}", run, acct.id).view())

    @app.get("/api/accounts/<int:account_id>/library")
    def get_library(account_id):
        get_account(account_id)
        if account_id not in library:
            saved = led.load_listing(account_id)
            if not saved:
                return jsonify(loaded=False, notebooks=[])
            library[account_id] = _load_listing(saved[0])
            cached_at[account_id] = saved[1]
        out = []
        for nb, ovs in library[account_id]:
            items = []
            for ov in ovs:
                row = led.harvest(ov.artifact_id)
                items.append(
                    {
                        "artifact_id": ov.artifact_id,
                        "title": ov.title,
                        "created_at": ov.created_at,
                        "status": harvest.overview_status(led, ov),
                        "saved_path": row.saved_path if row else None,
                    }
                )
            out.append({"id": nb.id, "title": nb.title, "created_at": nb.created_at, "overviews": items})
        return jsonify(loaded=True, cached_at=cached_at.get(account_id), notebooks=out)

    @app.post("/api/accounts/<int:account_id>/harvest")
    def do_harvest(account_id):
        acct = get_account(account_id)
        data = body()
        if not acct.output_dir or not Path(acct.output_dir).is_dir():
            return jsonify(error="Choose an output folder first."), 400
        if account_id not in library:
            return jsonify(error="Refresh the notebook list first."), 400
        all_ovs = [ov for _nb, ovs in library[account_id] for ov in ovs]
        wanted = set(data.get("artifact_ids") or [])
        redo = set(data.get("redownload") or [])
        if data.get("all_new"):
            chosen = [ov for ov in all_ovs if harvest.overview_status(led, ov) == harvest.NEW]
        else:
            chosen = [ov for ov in all_ovs if ov.artifact_id in wanted | redo]
        if not chosen:
            return jsonify(error="Nothing to harvest."), 400
        job, err = start_harvest([(account_id, ov.artifact_id) for ov in chosen], redo)
        if err:
            return jsonify(error=err), 400
        return jsonify(job.view())

    @app.post("/api/accounts/<int:account_id>/scan")
    def scan(account_id):
        acct = get_account(account_id)
        if not acct.output_dir or not Path(acct.output_dir).is_dir():
            return jsonify(error="Choose an output folder first."), 400

        def run(job):
            if account_id not in library:
                saved = led.load_listing(account_id)
                if saved:
                    library[account_id] = _load_listing(saved[0])
                else:
                    job.lines.append("Reading notebooks first")
                    library[account_id] = accounts.with_session(
                        led, acct, lambda: adapter.list_library(acct.profile_name, job.lines.append)
                    )
                    led.save_listing(account_id, _dump_listing(library[account_id]))
            known = {ov.artifact_id: ov for _nb, ovs in library[account_id] for ov in ovs}
            r = harvest.scan_folder(led, acct, Path(acct.output_dir), known, job.lines.append)
            job.message = (
                f"Found {r.added} new and {r.relinked} moved files from this account; "
                f"{r.already} already known; {r.ignored} other files left alone."
            )

        return jsonify(worker.submit("scan", f"Scanning folder for {acct.label}", run, acct.id).view())

    # ------------------------------------------------------------ whole library (U1)

    def ensure_listing(account_id: int) -> bool:
        """Load an account's cached listing into memory; False if it was never refreshed."""
        if account_id in library:
            return True
        saved = led.load_listing(account_id)
        if not saved:
            return False
        library[account_id] = _load_listing(saved[0])
        cached_at[account_id] = saved[1]
        return True

    @app.get("/api/library")
    def whole_library():
        """One flat row per overview across all accounts (client-side search and filters)."""
        accts = led.accounts()
        harvested = {h.artifact_id: h for h in led.harvests()}
        attempts = led.last_attempts()
        rows, never = [], []
        for a in accts:
            if not ensure_listing(a.id):
                never.append(a.id)
                continue
            for nb, ovs in library[a.id]:
                multi = len(ovs) > 1
                for ov in ovs:
                    h = harvested.get(ov.artifact_id)
                    status = _row_status(ov, h, a, attempts.get(ov.artifact_id))
                    rows.append(
                        {
                            "account_id": a.id,
                            "notebook_id": nb.id,
                            "notebook": nb.title,
                            "artifact_id": ov.artifact_id,
                            "title": ov.title,
                            "created_at": ov.created_at,
                            "status": status,
                            "multi": multi,
                            "harvest_id": h.id if h else None,
                            "saved_path": h.saved_path if h else None,
                            "file_name": Path(h.saved_path).name if h else None,
                            "saved_at": h.saved_at if h else None,
                            "size_bytes": h.size_bytes if h else None,
                            "duration_s": h.duration_s if h else None,
                            "origin": h.origin if h else None,
                            "clash": bool(h and _CLASH.search(Path(h.saved_path).stem)),
                            "last_error": (attempts.get(ov.artifact_id) or {}).get("message")
                            if status == "failed"
                            else None,
                        }
                    )
        return jsonify(
            accounts=[account_view(a) for a in accts],
            never_refreshed=never,
            listed_at={str(k): v for k, v in cached_at.items()},
            rows=rows,
        )

    def start_harvest(items: list[tuple[int, str]], redo: set[str]):
        """One job that harvests the given (account id, artifact id) pairs, account by account."""
        by_account: dict[int, list] = {}
        problems = []
        skipped_count = [0]
        for account_id, artifact_id in items:
            acct = led.account(account_id)
            if not acct or not ensure_listing(account_id):
                problems.append("an account was not found or never refreshed")
                continue
            if not acct.output_dir or not Path(acct.output_dir).is_dir():
                problems.append(f"{acct.label} has no reachable output folder")
                skipped_count[0] += 1
                continue
            ov = next(
                (o for _nb, ovs in library[account_id] for o in ovs if o.artifact_id == artifact_id), None
            )
            if ov:
                by_account.setdefault(account_id, []).append(ov)
        chosen = [ov for ovs in by_account.values() for ov in ovs]
        if not chosen:
            msg = "Nothing to harvest." + (f" ({'; '.join(sorted(set(problems)))})" if problems else "")
            return None, msg

        def run(job):
            for account_id, ovs in by_account.items():
                for ov in ovs:
                    job.items[ov.artifact_id] = {
                        "title": ov.title,
                        "step": "Waiting",
                        "outcome": None,
                        "pct": 0.0,
                        "account_id": account_id,
                        "redo": ov.artifact_id in redo,
                    }
            template = app_settings.load().get("naming_template")
            job.total = len(chosen)
            done_before = 0
            all_results = []
            for account_id, ovs in by_account.items():
                acct = led.account(account_id)

                def on_item(i, n, ov, msg, pct=None, base=done_before):
                    item = job.items[ov.artifact_id]
                    item["step"], item["pct"] = msg, pct
                    total_pct = sum((it["pct"] or 0) for it in job.items.values())
                    job.pct = min(1.0, total_pct / max(1, job.total))
                    job.current = ov.artifact_id
                    if not msg.startswith(("Downloading ", "Converting to MP3 ")):
                        job.lines.append(f"[{base + i + 1}/{job.total}] {ov.title}: {msg}")

                def download(profile, ov, dest, acct=acct):
                    return accounts.with_session(
                        led, acct, lambda: adapter.download_overview(profile, ov, dest)
                    )

                results = harvest.harvest_many(
                    led,
                    acct,
                    ovs,
                    Path(acct.output_dir),
                    redownload_ids=redo,
                    downloader=download,
                    on_item=on_item,
                    cancel=job.cancel,
                    template=template,
                )
                for r in results:
                    job.items[r.artifact_id].update(outcome=r.outcome, step=r.message, pct=1.0)
                all_results += results
                done_before += len(ovs)
            job.pct, job.current = 1.0, None
            counts = {k: sum(r.outcome == k for r in all_results) for k in ("saved", "failed", "cancelled")}
            skipped = len(all_results) - sum(counts.values())
            job.message = (
                f"Saved {counts['saved']}, skipped {skipped}, failed {counts['failed']}"
                + (f", cancelled {counts['cancelled']}" if counts["cancelled"] else "")
                + "."
            )
            if skipped_count[0]:
                job.message += f" Not started: {skipped_count[0]} ({'; '.join(sorted(set(problems)))})."
            if job.cancel.is_set():
                job.state = "cancelled"
            elif counts["failed"]:
                job.state = "failed"

        account_ids = list(by_account)
        title = f"Harvesting {len(chosen)}" + (
            f" for {led.account(account_ids[0]).label}" if len(account_ids) == 1 else " across accounts"
        )
        return worker.submit("harvest", title, run, account_ids[0] if len(account_ids) == 1 else None), None

    @app.post("/api/harvest")
    def harvest_items():
        """Harvest exactly the listed items (the page sends its filtered selection)."""
        data = body()
        items = []
        for it in data.get("items") or []:
            try:
                items.append((int(it["account_id"]), str(it["artifact_id"])))
            except (KeyError, TypeError, ValueError):
                return jsonify(error="An item in the request is not valid."), 400
        job, err = start_harvest(items, {str(a) for a in data.get("redownload") or []})
        if err:
            return jsonify(error=err), 400
        return jsonify(job.view())

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id):
        """Ask a job to stop after its current safe step (FLAG-P2)."""
        j = worker.jobs.get(job_id)
        if not j:
            abort(404)
        j.cancel.set()
        return jsonify(j.view())

    @app.post("/api/naming/preview")
    def naming_preview():
        """Live preview of a naming template with a sample title (Settings)."""
        template = str(body().get("template") or naming.DEFAULT_TEMPLATE)
        try:
            template = app_settings.validate_template(template)
        except app_settings.SettingsError as exc:
            return jsonify(error=str(exc)), 400
        sample = {
            "notebook": "Garden Planning: Spring/Summer",
            "title": "Why Raised Beds Warm Up Faster?",
            "date": "2026-11-14",
            "account": "Work account",
        }
        folder, stem = naming.render_template(template, **sample)
        return jsonify(path=(f"{folder}/" if folder else "") + stem + ".mp3", sample=sample)

    @app.get("/api/diagnostics")
    def diagnostics():
        """Facts for a support message: versions and paths, redacted, no account details."""
        import platform

        from nlh.log import redact

        try:
            from importlib.metadata import version as pkg_version

            lib_version = pkg_version("notebooklm-py")
        except Exception:
            lib_version = "unknown"
        statuses = ", ".join(sorted({a.last_status or "unknown" for a in led.accounts()}))
        lines = [
            f"{APP_NAME} {__version__}",
            f"System: {platform.system()} {platform.release()} ({platform.machine()})",
            f"Python: {platform.python_version()}",
            f"notebooklm-py: {lib_version}",
            f"ffmpeg found: {media.tools_available()}",
            f"Packaged app: {bool(getattr(sys, 'frozen', False))}",
            f"Demo mode: {demo.enabled()}",
            f"App home: {paths.app_home()}",
            f"Log file: {paths.log_dir() / 'nlh.log'}",
            f"Accounts: {len(led.accounts())} (status: {statuses})",
            f"Saved files in ledger: {len(led.harvests())}",
            f"Ledger schema: {led.conn.execute('PRAGMA user_version').fetchone()[0]}",
        ]
        return jsonify(text=redact("\n".join(lines)))

    @app.get("/api/item/<path:artifact_id>")
    def item_details(artifact_id):
        """Everything known about one overview for the details drawer (tags read from the file)."""
        h = led.harvest(artifact_id)
        attempts = led.conn.execute(
            "SELECT started_at, outcome, message, download_s FROM attempts WHERE artifact_id = ? "
            "ORDER BY id DESC LIMIT 10",
            (artifact_id,),
        ).fetchall()
        out = {"artifact_id": artifact_id, "attempts": [dict(r) for r in attempts], "saved": None}
        if h:
            out["saved"] = history_row(h, {a.id: a for a in led.accounts()})
            if h.file_present:
                try:
                    out["tags"] = media.probe(Path(h.saved_path)).tags
                except media.MediaError:
                    out["tags"] = {}
        return jsonify(out)

    # ------------------------------------------------------------ settings and views

    @app.get("/api/settings")
    def get_settings():
        return jsonify(app_settings.load())

    @app.post("/api/settings")
    def put_settings():
        try:
            return jsonify(app_settings.save(body()))
        except app_settings.SettingsError as exc:
            return jsonify(error=str(exc)), 400

    # ------------------------------------------------------------ history and folders

    @app.get("/api/history")
    def history():
        account_id = request.args.get("account", type=int)
        labels = {a.id: a.label for a in led.accounts()}
        outputs = {a.id: a.output_dir for a in led.accounts()}
        return jsonify(
            [
                {
                    "account": labels.get(h.account_id, "?"),
                    "title": h.overview_title,
                    "notebook": h.notebook_title,
                    "saved_at": h.saved_at,
                    "path": h.saved_path,
                    "folder": str(Path(h.saved_path).parent),
                    "duration_s": h.duration_s,
                    "size_bytes": h.size_bytes,
                    "present": h.file_present,
                    "reachable": h.file_present
                    or harvest.folder_reachable(h.saved_path, outputs.get(h.account_id)),
                }
                for h in led.harvests(account_id)
            ]
        )

    # ------------------------------------------------------------ history pages, export, stats, player (U2)

    def history_filters() -> dict:
        """History filters from the query string (all bound as SQL parameters later)."""
        a = request.args
        f = {
            "accounts": [int(x) for x in a.get("accounts", "").split(",") if x.strip().isdigit()],
            "notebooks": [x for x in a.getlist("notebook") if x],
            "q": a.get("q", "")[:200],
        }
        for key in ("saved_from", "saved_to"):
            v = a.get(key, "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                f[key] = v
        for key in ("size_min", "size_max", "length_min", "length_max"):
            try:
                if a.get(key, "") != "":
                    f[key] = float(a[key])
            except ValueError:
                pass
        return f

    def playable(h, acct) -> tuple[bool, str]:
        """May the player stream this row? Only a ledger path inside the account's output folder."""
        if not acct or not acct.output_dir:
            return False, "No output folder is set for this account."
        try:
            path = Path(h.saved_path).resolve(strict=True)
            out = Path(acct.output_dir).resolve(strict=True)
        except (OSError, RuntimeError):
            return False, "The file is not there."
        if path.suffix.lower() != ".mp3" or not path.is_file():
            return False, "Only saved MP3 files can be played."
        if not path.is_relative_to(out):
            return False, "This file is outside the account's current output folder. Use Show in folder."
        return True, ""

    def history_row(h, accts: dict) -> dict:
        acct = accts.get(h.account_id)
        present = h.file_present
        ok, why = playable(h, acct) if present else (False, "")
        return {
            "id": h.id,
            "account_id": h.account_id,
            "account": acct.label if acct else "?",
            "title": h.overview_title,
            "notebook": h.notebook_title,
            "notebook_id": h.notebook_id,
            "artifact_id": h.artifact_id,
            "created_at": h.created_at,
            "saved_at": h.saved_at,
            "path": h.saved_path,
            "file_name": Path(h.saved_path).name,
            "folder": str(Path(h.saved_path).parent),
            "duration_s": h.duration_s,
            "size_bytes": h.size_bytes,
            "origin": h.origin,
            "present": present,
            "reachable": present or harvest.folder_reachable(h.saved_path, acct.output_dir if acct else None),
            "playable": ok,
            "play_reason": why,
            "sha256": h.sha256,
        }

    @app.get("/api/history/page")
    def history_page():
        """One page of history, filtered and sorted by the server (pagination ruling 2)."""
        accts = {a.id: a for a in led.accounts()}
        sort = request.args.get("sort", "saved_desc")
        page = request.args.get("page", 1, type=int)
        size = request.args.get("size", 50, type=int)
        if size not in app_settings.PAGE_SIZES:
            size = 50
        rows, total = led.history_page(history_filters(), sort, page, size)
        return jsonify(total=total, page=page, size=size, rows=[history_row(h, accts) for h in rows])

    @app.get("/api/history/notebooks")
    def history_notebooks():
        """Notebook names in the ledger with counts (for the history filter picker)."""
        counts: dict[str, int] = {}
        for h in led.harvests():
            counts[h.notebook_title] = counts.get(h.notebook_title, 0) + 1
        return jsonify(
            sorted(
                ({"name": k, "count": v} for k, v in counts.items()), key=lambda x: (-x["count"], x["name"])
            )
        )

    EXPORT_COLUMNS = [
        "id",
        "account_id",
        "notebook_id",
        "artifact_id",
        "overview_title",
        "notebook_title",
        "created_at",
        "saved_path",
        "size_bytes",
        "duration_s",
        "sha256",
        "saved_at",
        "origin",
    ]

    @app.get("/api/export.<fmt>")
    def export(fmt):
        """The whole filtered ledger as CSV or JSON: ledger columns only, never session data."""
        rows = led.history_all(history_filters(), request.args.get("sort", "saved_desc"))
        records = []
        for h in rows:
            full = dataclasses.asdict(h)
            records.append({c: full[c] for c in EXPORT_COLUMNS})
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "json":
            body_text = json.dumps(records, ensure_ascii=False, indent=2)
            mime = "application/json"
        elif fmt == "csv":
            import csv
            import io

            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
            w.writeheader()
            w.writerows(records)
            body_text = buf.getvalue()
            mime = "text/csv"
        else:
            abort(404)
        return Response(
            body_text,
            mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="notebooklm-harvest-{stamp}.{fmt}"'},
        )

    @app.get("/api/stats")
    def stats():
        """Totals and breakdowns over the whole filtered ledger, plus attempt statistics."""
        f = history_filters()
        rows = led.history_all(f)
        accts = {a.id: a.label for a in led.accounts()}
        by_account: dict[str, dict] = {}
        by_notebook: dict[str, dict] = {}
        by_month: dict[str, dict] = {}
        for h in rows:
            for bucket, key in (
                (by_account, accts.get(h.account_id, "?")),
                (by_notebook, h.notebook_title),
                (by_month, (h.saved_at or "")[:7]),
            ):
                b = bucket.setdefault(key, {"files": 0, "hours": 0.0, "bytes": 0})
                b["files"] += 1
                b["hours"] += (h.duration_s or 0) / 3600
                b["bytes"] += h.size_bytes or 0
        att = led.attempt_stats(f.get("accounts") or None)
        done = att["saved"] + att["failed"]
        return jsonify(
            totals={
                "files": len(rows),
                "hours": round(sum((h.duration_s or 0) for h in rows) / 3600, 2),
                "bytes": sum(h.size_bytes or 0 for h in rows),
            },
            by_account=sorted(({"name": k, **v} for k, v in by_account.items()), key=lambda x: -x["files"]),
            by_notebook=sorted(({"name": k, **v} for k, v in by_notebook.items()), key=lambda x: -x["files"])[
                :10
            ],
            by_month=sorted(({"name": k, **v} for k, v in by_month.items()), key=lambda x: x["name"]),
            success_rate=round(att["saved"] / done, 4) if done else None,
            attempts={"saved": att["saved"], "failed": att["failed"]},
            avg_download_s=round(att["avg_download_s"], 1) if att["avg_download_s"] else None,
        )

    @app.get("/api/play/<int:harvest_id>")
    def play(harvest_id):
        """Stream a saved MP3 (HTTP Range supported). No path ever comes from the page."""
        h = led.harvest_by_id(harvest_id)
        if not h:
            abort(404)
        ok, why = playable(h, led.account(h.account_id))
        if not ok:
            return jsonify(error=why or "That file cannot be played."), 403
        resp = send_file(Path(h.saved_path).resolve(), mimetype="audio/mpeg", conditional=True, max_age=0)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/history/<int:harvest_id>/redownload")
    def redownload(harvest_id):
        """Download a missing file again: only when its folder is reachable (FINDING-1)."""
        h = led.harvest_by_id(harvest_id)
        if not h:
            abort(404)
        acct = led.account(h.account_id)
        if h.file_present:
            return jsonify(error="The file is already there."), 400
        if not harvest.folder_reachable(h.saved_path, acct.output_dir if acct else None):
            return jsonify(
                error="The folder that holds this file is not reachable. Plug in the drive first."
            ), 400
        job, err = start_harvest([(h.account_id, h.artifact_id)], {h.artifact_id})
        if err:
            return jsonify(error=err), 400
        return jsonify(job.view())

    def allowed_folders() -> set[str]:
        """Folders the page may open: account output folders and folders of saved files."""
        out = {a.output_dir for a in led.accounts() if a.output_dir}
        out |= {str(Path(h.saved_path).parent) for h in led.harvests()}
        return out

    @app.post("/api/open")
    def open_folder():
        path = str(body().get("path", ""))
        if path not in allowed_folders() or not Path(path).is_dir():
            return jsonify(error="That folder cannot be opened."), 400
        open_in_file_manager(Path(path))
        return jsonify(ok=True)

    @app.get("/api/browse")
    def browse():
        raw = request.args.get("path") or ""
        if not raw:
            if sys.platform == "win32":
                drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
                return jsonify(path="", parent=None, dirs=drives, home=str(Path.home()))
            raw = str(Path.home())
        p = Path(raw).expanduser()
        if not p.is_dir():
            return jsonify(error="That folder does not exist."), 400
        try:
            dirs = sorted(
                (str(c) for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")),
                key=str.lower,
            )
        except PermissionError:
            dirs = []
        parent = str(p.parent) if p.parent != p else ("" if sys.platform == "win32" else None)
        return jsonify(path=str(p), parent=parent, dirs=dirs, home=str(Path.home()))

    @app.post("/api/mkdir")
    def make_folder():
        """Create a new folder inside the folder being browsed (folder picker)."""
        data = body()
        parent = Path(str(data.get("parent", ""))).expanduser()
        name = str(data.get("name", "")).strip()
        if not parent.is_dir():
            return jsonify(error="Open a folder first, then make the new folder inside it."), 400
        if not name or name in (".", "..") or naming.safe_stem(name) != name:
            return jsonify(error='Use a folder name without / \\ : * ? " < > | characters.'), 400
        target = parent / name
        if target.exists():
            return jsonify(error="A folder with that name already exists here."), 400
        try:
            target.mkdir()
        except OSError:
            return jsonify(error="Could not make the folder here."), 400
        log.info("made folder %s", target)
        return jsonify(ok=True, path=str(target))

    app.config["worker"] = worker
    app.config["library"] = library
    return app


_CLASH = re.compile(r" \(\d+\)$")


def _row_status(ov, h, acct, attempt: dict | None) -> str:
    """Status of one overview for the library view (U1): saved file, gaps, failures."""
    if not ov.completed:
        return harvest.IN_PROGRESS
    if h is None:
        return "failed" if attempt and attempt.get("outcome") == "failed" else harvest.NEW
    if Path(h.saved_path).exists():
        return harvest.SAVED
    return harvest.MISSING if harvest.folder_reachable(h.saved_path, acct.output_dir) else harvest.UNREACHABLE


def _dump_listing(lib) -> str:
    """Serialize a listing (titles and ids only, no secrets) for the cache."""
    return json.dumps(
        [{"nb": dataclasses.asdict(nb), "ovs": [dataclasses.asdict(o) for o in ovs]} for nb, ovs in lib]
    )


def _load_listing(text: str):
    """Rebuild a cached listing into adapter types."""
    return [
        (adapter.Notebook(**row["nb"]), [adapter.AudioOverview(**o) for o in row["ovs"]])
        for row in json.loads(text)
    ]


def _clear_stale_temp() -> None:
    """Remove leftover per-item temp folders from an interrupted run (app home only)."""
    import shutil

    for item in paths.temp_dir().glob("item-*"):
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
            log.info("cleared leftover temp folder %s", item.name)


def open_in_file_manager(path: Path) -> None:
    """Show a folder in Finder or Explorer."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _free_port() -> int:
    """Ask the OS for a free local port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_free(port: int) -> bool:
    """True if nothing is listening on this local port."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _server_settings() -> tuple[int, str]:
    """Port and page key, kept in the app home (owner-only) so open tabs survive a restart."""
    settings = paths.app_home() / "server.json"
    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    token = data.get("token") or secrets.token_urlsafe(24)
    port = int(os.environ.get("NLH_PORT") or data.get("port") or _free_port())
    if not _port_free(port):
        port = _free_port()
    settings.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(settings, 0o600)
    return port, token


def _say(message: str) -> None:
    """Print to the launcher window if there is one (a double-clicked app has none)."""
    if sys.stdout is not None:
        try:
            print(message, flush=True)
        except OSError:
            pass


def _running_url() -> str | None:
    """Address of an app instance that is already running for this app home, if any."""
    import urllib.request

    settings = paths.app_home() / "server.json"
    if not settings.exists():
        return None
    try:
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        req = urllib.request.Request(
            f"http://127.0.0.1:{cfg['port']}/api/state", headers={"X-NLH-Token": cfg["token"]}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return f"http://127.0.0.1:{cfg['port']}/?t={cfg['token']}"
    except (OSError, ValueError, KeyError):
        return None
    return None


def quiet_request_log() -> None:
    """Stop the web server library writing one line per request to stderr.

    Those lines carry the page address with its key (?t=...), so they must not be
    printed; and when stderr is a pipe nobody reads (a Windows pipe holds about 4 KB)
    the server blocks once it fills. Warnings and errors still go through.
    """
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def serve(open_browser: bool = True) -> int:
    """Start the local server and open the page in the default browser."""
    from werkzeug.serving import make_server

    quiet_request_log()
    if demo.enabled():
        demo.prepare()  # synthetic data in a temporary app home; never the real one
    lock = paths.AppLock()
    if not lock.acquire():
        # Another process owns this app home (FINDING-2): show its page instead.
        running = _running_url()
        _say(f"{APP_NAME} is already running.")
        if running and open_browser:
            webbrowser.open(running)
        elif not running:
            _say("Another copy is starting or stopping. Try again in a few seconds.")
        return 0
    # Only the lock holder may clear temp folders: a second process must never remove
    # the first one's download in progress.
    _clear_stale_temp()
    port, token = _server_settings()
    app = create_app(token)
    server = make_server("127.0.0.1", port, app, threaded=True)
    app.config["stop"] = server.shutdown
    url = f"http://127.0.0.1:{port}/?t={token}"
    log.info("server started on 127.0.0.1:%d", port)
    _say(f"{APP_NAME} is running. If the page did not open, use the address in your browser:")
    _say(url)
    _say("Close this window (or press Ctrl+C) to stop, or use Quit on the page.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
    return 0
