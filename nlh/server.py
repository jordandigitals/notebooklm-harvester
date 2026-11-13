"""Local web page (D-015, D-016): Flask on 127.0.0.1 with a per-launch token (R8).

All slow work (listing, harvesting, sign-ins) runs on one background worker, one
job at a time, so there is never more than one download or sign-in in flight.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import os
import queue
import secrets
import socket
import string
import subprocess
import sys
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from nlh import APP_NAME, __version__, accounts, adapter, harvest, media, naming, paths
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
            job.state = "running"
            try:
                fn(job)
                if job.state == "running":
                    job.state = "done"
            except (accounts.AccountError, adapter.AdapterError) as exc:
                job.state, job.message = "failed", getattr(exc, "message", str(exc))
            except Exception as exc:  # last resort: plain message, detail in the log
                log.exception("job %s failed: %s", job.kind, type(exc).__name__)
                job.state, job.message = "failed", "Something went wrong. Details are in the log."


# ---------------------------------------------------------------- app


def create_app(token: str, ledger: Ledger | None = None) -> Flask:
    """Build the Flask app. Every request needs the launch token and a local Host."""
    app = Flask(__name__, static_folder=None)
    _clear_stale_temp()
    led = ledger or Ledger()
    worker = Worker()
    library: dict[int, list] = {}  # account id -> [(Notebook, [AudioOverview])]
    cached_at: dict[int, str] = {}  # account id -> when a cached listing was read

    @app.before_request
    def guard():
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.path.startswith("/static/"):
            return None
        if request.path == "/favicon.ico":
            return "", 204
        given = request.args.get("t") or request.headers.get("X-NLH-Token") or ""
        if not hmac.compare_digest(given, token):
            if request.path.startswith("/api/"):
                return jsonify(error="This page's key is out of date. Reopen the app's page."), 403
            return STALE_PAGE.replace("{{APP_NAME}}", APP_NAME), 403
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
            "status": a.last_status or "unknown",
            "last_checked": a.last_checked,
            "saved_count": len(saved),
        }

    # ------------------------------------------------------------ page

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return html.replace("{{APP_NAME}}", APP_NAME).replace("{{VERSION}}", __version__)

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    # ------------------------------------------------------------ state

    @app.get("/api/state")
    def state():
        current = [worker.jobs[j].view() for j in worker.order[-20:]]
        portable = paths.portable_home() is not None and not os.environ.get("NLH_HOME")
        return jsonify(
            interactive=adapter.interactive_available(),
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
            if not acct.chrome_profile:
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

        def run(job):
            for ov in chosen:
                job.items[ov.artifact_id] = {
                    "title": ov.title,
                    "step": "Waiting",
                    "outcome": None,
                    "pct": 0.0,
                }
            job.total = len(chosen)

            def on_item(i, n, ov, msg, pct=None):
                item = job.items[ov.artifact_id]
                item["step"], item["pct"] = msg, pct
                done = sum((it["pct"] or 0) for it in job.items.values())
                job.pct = min(1.0, done / max(1, n))
                job.current = ov.artifact_id
                if not msg.startswith(("Downloading ", "Converting to MP3 ")):
                    job.lines.append(f"[{i + 1}/{n}] {ov.title}: {msg}")

            def download(profile, ov, dest):
                return accounts.with_session(led, acct, lambda: adapter.download_overview(profile, ov, dest))

            results = harvest.harvest_many(
                led,
                led.account(acct.id),
                chosen,
                Path(acct.output_dir),
                redownload_ids=redo,
                downloader=download,
                on_item=on_item,
            )
            for r in results:
                job.items[r.artifact_id].update(outcome=r.outcome, step=r.message, pct=1.0)
            job.pct, job.current = 1.0, None
            saved = sum(r.outcome == "saved" for r in results)
            failed = sum(r.outcome == "failed" for r in results)
            job.message = f"Saved {saved}, skipped {len(results) - saved - failed}, failed {failed}."
            if failed:
                job.state = "failed"

        title = f"Harvesting {len(chosen)} for {acct.label}"
        return jsonify(worker.submit("harvest", title, run, acct.id).view())

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

    # ------------------------------------------------------------ history and folders

    @app.get("/api/history")
    def history():
        account_id = request.args.get("account", type=int)
        labels = {a.id: a.label for a in led.accounts()}
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
                }
                for h in led.harvests(account_id)
            ]
        )

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


def serve(open_browser: bool = True) -> int:
    """Start the local server and open the page in the default browser."""
    from werkzeug.serving import make_server

    running = _running_url()
    if running:
        # Already running: just show it.
        _say(f"{APP_NAME} is already running.")
        if open_browser:
            webbrowser.open(running)
        return 0
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
    return 0
