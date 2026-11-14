"""Studio page routes (D-025): plan (S1), generate (S2), auto-harvest (S3).

Every write toward NotebookLM starts from a request the page sends after Jordan's click;
nothing here runs on a timer. The adapter's StudioClient is the only write path.
"""

from __future__ import annotations

import json

from flask import abort, jsonify, request

from nlh import adapter, studio
from nlh import settings as app_settings
from nlh.ledger import Ledger
from nlh.log import get_logger

log = get_logger()

MAX_QUESTION = 2000


def register(app, led: Ledger, worker, get_account, body, account_listing) -> None:
    """Add the /api/studio routes to the app."""

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
