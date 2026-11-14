"""Studio page routes (D-025): plan (S1), generate (S2), auto-harvest (S3).

Every write toward NotebookLM starts from a request the page sends after Jordan's click;
nothing here runs on a timer. The adapter's StudioClient is the only write path.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from flask import abort, jsonify, request

from nlh import adapter, demo, studio
from nlh import settings as app_settings
from nlh.ledger import Ledger
from nlh.log import get_logger
from nlh.studio_runner import StudioRunner

log = get_logger()

MAX_QUESTION = 2000


def register(app, led: Ledger, worker, get_account, body, account_listing) -> None:
    """Add the /api/studio routes to the app."""

    led.recover_after_restart()  # rows left "submitting" become needs_review; nothing re-sent
    if demo.enabled():  # quick demo runs; real runs use the 20 s check and the pause setting
        runner = StudioRunner(
            led,
            poll_s=float(os.environ.get("NLH_DEMO_POLL_S", "1")),
            pause_s=float(os.environ.get("NLH_DEMO_PAUSE_S", "2")),
        )
    else:
        runner = StudioRunner(led)
    app.config["STUDIO_RUNNER"] = runner  # tests reach it here

    def notebook_in(account_id: int, notebook_id: str):
        """The notebook from the account's listing, or None (the page cannot name others)."""
        for nb, _ovs in account_listing(account_id) or []:
            if nb.id == notebook_id:
                return nb
        return None

    def account_rows() -> list[dict]:
        shared = set(app_settings.load()["studio_shared_accounts"])
        return [
            {
                "id": a.id,
                "label": a.label,
                "email": a.email,
                "status": a.last_status or "unknown",
                "shared": a.id in shared,
            }
            for a in led.accounts()
        ]

    @app.get("/api/studio/state")
    def studio_state():
        """Accounts (none preselected), the daily cap and today's use, approved plans."""
        s = app_settings.load()
        day = studio.local_day()
        accounts = account_rows()
        used = {a["id"]: led.submitted_on(a["id"], day) for a in accounts}
        plans = []
        for p in led.plans():
            plans.append(
                {
                    "id": p["id"],
                    "account_id": p["account_id"],
                    "notebook_id": p["notebook_id"],
                    "notebook_title": p["notebook_title"],
                    "items": json.loads(p["items_json"]),
                    "created_at": p["created_at"],
                }
            )
        return jsonify(
            accounts=accounts,
            cap=s["studio_daily_cap"],
            pause_s=s["studio_pause_s"],
            language=s["studio_language"],
            ask_warned=s["studio_ask_warned"],
            used_today=used,
            day=day,
            plans=plans,
            default_question=studio.DEFAULT_QUESTION,
            formats=list(adapter.AUDIO_FORMATS),
            lengths=list(adapter.AUDIO_LENGTHS),
        )

    @app.get("/api/studio/notebooks")
    def studio_notebooks():
        """The chosen account's notebooks from its last listing."""
        try:
            account_id = int(request.args.get("account_id", ""))
        except ValueError:
            abort(400)
        get_account(account_id)
        listing = account_listing(account_id)
        if listing is None:
            return jsonify(loaded=False, notebooks=[])
        rows = [{"id": nb.id, "title": nb.title, "overviews": len(ovs)} for nb, ovs in listing]
        rows.sort(key=lambda r: r["title"].casefold())
        return jsonify(loaded=True, notebooks=rows)

    @app.post("/api/studio/ask")
    def studio_ask():
        """Ask one notebook for an overview plan: one chat message, current conversation."""
        data = body()
        try:
            account_id = int(data.get("account_id"))
        except (TypeError, ValueError):
            return jsonify(error="Choose an account first."), 400
        acct = get_account(account_id)
        nb = notebook_in(account_id, str(data.get("notebook_id") or ""))
        if nb is None:
            return jsonify(error="Choose a notebook from this account's list."), 400
        question = str(data.get("question") or "").strip() or studio.DEFAULT_QUESTION
        if len(question) > MAX_QUESTION:
            return jsonify(error=f"The question can be at most {MAX_QUESTION} characters."), 400
        s = app_settings.load()
        if not s["studio_ask_warned"]:
            if data.get("acknowledged") is not True:
                return jsonify(
                    error="Asking adds a message to that notebook's chat. Confirm to continue.",
                    needs_ack=True,
                ), 409
            app_settings.save({"studio_ask_warned": True})
        if any(j.kind == "studio-ask" and j.state in ("queued", "running") for j in worker.jobs.values()):
            return jsonify(error="A plan request is already running. Wait for it to finish."), 409

        def run(job):
            job.current = nb.title
            answer = adapter.ask_for_plan(acct.profile_name, nb.id, question)
            parsed = studio.parse_plan(answer.text)
            job.result = {
                "account_id": account_id,
                "notebook_id": nb.id,
                "notebook_title": nb.title,
                "raw": answer.text,
                "parsed": parsed,
            }
            n = len(parsed["items"])
            job.message = (
                f"Plan received: {n} overview{'s' if n != 1 else ''} found."
                if n
                else ("Plan received. No list was found; add the overviews yourself.")
            )
            log.info("studio plan for notebook %s: %d items (%s)", nb.id, n, parsed["method"])

        job = worker.submit("studio-ask", f"Asking for a plan: {nb.title}", run, account_id)
        return jsonify(job.view())

    @app.post("/api/studio/plans")
    def studio_save_plan():
        """Keep a plan Jordan edited and approved (only its rows are stored)."""
        data = body()
        try:
            account_id = int(data.get("account_id"))
        except (TypeError, ValueError):
            return jsonify(error="Choose an account first."), 400
        get_account(account_id)
        nb = notebook_in(account_id, str(data.get("notebook_id") or ""))
        if nb is None:
            return jsonify(error="Choose a notebook from this account's list."), 400
        try:
            items = studio.clean_items(data.get("items"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        plan_id = led.add_plan(account_id, nb.id, nb.title, json.dumps(items, ensure_ascii=False))
        log.info("studio plan %d kept: %d items", plan_id, len(items))
        return jsonify(id=plan_id, items=items)

    @app.post("/api/studio/plans/<int:plan_id>/archive")
    def studio_archive_plan(plan_id):
        """Hide a kept plan from the list (the row stays in the ledger)."""
        if not led.plan(plan_id):
            abort(404)
        led.archive_plan(plan_id)
        return jsonify(ok=True)

    # ------------------------------------------------------------ S2: queue and runner

    def queue_view() -> dict:
        s = app_settings.load()
        day = studio.local_day()
        return {
            "rows": led.recent_queue(),
            "runner": runner.view(),
            "cap": s["studio_daily_cap"],
            "used_today": {a.id: led.submitted_on(a.id, day) for a in led.accounts()},
            "limited_today": [a.id for a in led.accounts() if led.limit_hit(a.id, day)],
        }

    @app.get("/api/studio/queue")
    def studio_queue():
        """The queue (unfinished rows and recent finished ones) and the runner's state."""
        return jsonify(queue_view())

    @app.post("/api/studio/queue")
    def studio_enqueue():
        """Queue overviews from a kept plan with Jordan's per-item settings. Sends nothing."""
        data = body()
        try:
            plan = led.plan(int(data.get("plan_id")))
        except (TypeError, ValueError):
            plan = None
        if not plan:
            return jsonify(error="Choose a kept plan first."), 400
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= studio.MAX_ITEMS:
            return jsonify(error="Choose at least one overview to make."), 400
        default_lang = app_settings.load()["studio_language"]
        rows = []
        for it in items:
            if not isinstance(it, dict):
                return jsonify(error="An overview in the request is not valid."), 400
            try:
                clean = studio.clean_items([it])[0]
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            fmt = it.get("audio_format", "deep_dive")
            length = it.get("audio_length", "default")
            lang = str(it.get("language") or default_lang).strip()
            if fmt not in adapter.AUDIO_FORMATS or length not in adapter.AUDIO_LENGTHS:
                return jsonify(error="Unknown format or length."), 400
            try:
                lang = app_settings.validate({"studio_language": lang})["studio_language"]
            except app_settings.SettingsError as exc:
                return jsonify(error=str(exc)), 400
            rows.append(
                {
                    "plan_id": plan["id"],
                    "account_id": plan["account_id"],
                    "notebook_id": plan["notebook_id"],
                    "notebook_title": plan["notebook_title"],
                    "title": clean["title"],
                    "focus": clean["focus"],
                    "audio_format": fmt,
                    "audio_length": length,
                    "language": lang,
                }
            )
        ids = led.add_queue_items(rows)
        log.info("studio queued %d items from plan %d", len(ids), plan["id"])
        return jsonify(ids=ids, **queue_view())

    @app.post("/api/studio/run")
    def studio_run():
        """Start or resume the queue (Jordan's click). Nothing else ever starts it."""
        if not led.queue_items(("queued", "generating")):
            return jsonify(error="Nothing is waiting in the queue."), 400
        if not runner.start():
            return jsonify(error="The queue is already running."), 409
        return jsonify(queue_view())

    @app.post("/api/studio/cancel")
    def studio_cancel():
        """Stop after the current safe step."""
        runner.cancel()
        return jsonify(queue_view())

    def queue_row(item_id: int) -> dict:
        row = led.queue_item(item_id)
        if not row:
            abort(404)
        return row

    @app.post("/api/studio/queue/<int:item_id>/drop")
    def studio_drop(item_id):
        """Drop a row that is not being worked on right now. Nothing is sent or deleted in
        NotebookLM; if it was already made there, a normal harvest finds it."""
        row = queue_row(item_id)
        if row["state"] in ("done", "failed", "cancelled"):
            return jsonify(error="This overview is already finished."), 400
        if runner.state.running and runner.state.current == item_id:
            return jsonify(error="This overview is being worked on. Use Cancel first."), 409
        if row["state"] == "submitting":
            return jsonify(error="This request is being sent right now. Try again in a moment."), 409
        note = (
            "Dropped. If NotebookLM already made it, a normal harvest will find it."
            if row["state"] in ("generating", "needs_review")
            else "Dropped before it was sent."
        )
        led.update_queue(item_id, state="cancelled", message=note)
        return jsonify(queue_view())

    @app.post("/api/studio/queue/<int:item_id>/requeue")
    def studio_requeue(item_id):
        """Queue a finished, failed, paused or dropped row again as a new row (counts again
        when it is sent). Only from Jordan's click."""
        row = queue_row(item_id)
        if row["state"] not in ("failed", "cancelled", "paused_limit"):
            return jsonify(error="Only a failed, paused or dropped overview can be queued again."), 400
        fields = (
            "plan_id",
            "account_id",
            "notebook_id",
            "notebook_title",
            "title",
            "focus",
            "audio_format",
            "audio_length",
            "language",
        )
        led.add_queue_items([{k: row[k] for k in fields}])
        if row["state"] == "paused_limit":
            led.update_queue(item_id, state="cancelled", message="Queued again as a new row.")
        return jsonify(queue_view())

    @app.post("/api/studio/queue/<int:item_id>/check")
    def studio_check(item_id):
        """For a row left "submitting" when the app stopped: look in the notebook (read-only)
        for an overview made after it was sent. Found: follow it. Not found: it was not sent."""
        row = queue_row(item_id)
        if row["state"] != "needs_review":
            return jsonify(error="Only an overview that needs checking can be checked."), 400
        acct = get_account(row["account_id"])
        try:
            ovs = adapter.list_audio_overviews(
                acct.profile_name, adapter.Notebook(row["notebook_id"], row["notebook_title"], None)
            )
        except adapter.AdapterError as exc:
            return jsonify(error=exc.message), 502
        linked = {r["task_id"] for r in led.queue_items() if r["task_id"]}
        since = datetime.fromisoformat(row["submitted_at"]) - timedelta(minutes=2)
        candidates = [
            o
            for o in ovs
            if o.artifact_id not in linked
            and led.harvest(o.artifact_id) is None
            and o.created_at
            and datetime.fromisoformat(o.created_at) >= since
        ]
        candidates.sort(key=lambda o: o.created_at)
        if candidates:
            led.update_queue(
                item_id,
                state="generating",
                task_id=candidates[0].artifact_id,
                message="Found in NotebookLM. Resume collects it.",
            )
            found = True
        else:
            led.update_queue(
                item_id,
                state="failed",
                message="Not found in NotebookLM, so it was not made. Use Queue again to send it.",
            )
            found = False
        return jsonify(found=found, **queue_view())
