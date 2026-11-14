"""Studio runner (D-025, S2 and S3): sends queued overviews to NotebookLM one at a time, waits
for each to finish, harvests it, pauses, then moves on.

Rules kept here (D-025 conditions 1, 3, 4):
- The runner only runs after Jordan clicks Start or Resume. Nothing starts it on its own,
  and it stops when the queue is empty, on Cancel, or on a limit.
- One request at a time with a pause between requests. No request is ever retried.
- The daily cap is counted when a row turns "submitting", before the request goes out.
- The first usage-limit refusal from Google stops that account for the rest of the day and
  marks its remaining rows "paused_limit".
- Completion is checked by reading the notebook's audio list (the read-only path), matching
  the task id, which is the artifact id.

Queue states: queued, submitting, generating, done, failed, cancelled, paused_limit, and
needs_review (a row left "submitting" when the app stopped: Jordan checks it).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nlh import adapter, harvest, studio
from nlh import settings as app_settings
from nlh.ledger import Ledger, now
from nlh.log import get_logger

log = get_logger()

FINISHED = ("done", "failed", "cancelled")
POLL_S = 20.0  # how often a running generation is checked
MISSING_POLLS = 5  # absent this many checks in a row (and for at least MISSING_WINDOW_S) = removed
MISSING_WINDOW_S = 90.0
GENERATION_TIMEOUT_S = 40 * 60


@dataclass
class RunState:
    """What the page shows about the runner."""

    running: bool = False
    current: int | None = None  # queue row being worked on
    step: str = ""  # plain words: "Sending", "Making the overview", "Saving", "Pausing"
    pause_until: float | None = None
    message: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)


class StudioRunner:
    """One background thread at most; it works through the queue in order."""

    def __init__(
        self,
        led: Ledger,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
        today: Callable[[], str] = studio.local_day,
        poll_s: float = POLL_S,
        on_saved: Callable[[], None] | None = None,
        pause_s: float | None = None,
    ):
        self.led = led
        self.clock = clock
        self.today = today
        self.poll_s = poll_s
        self.state = RunState()
        self._sleep = sleep
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.on_saved = on_saved or (lambda: None)
        self._halt: str | None = None
        self._limit_note = ""  # kept to the end of a run so the final message still says it
        self.pause_override = pause_s  # demo mode and tests only; real runs use the setting

    # ------------------------------------------------------------ control (page clicks)

    def start(self) -> bool:
        """Start working through the queue (Jordan's click). False if already running."""
        with self._lock:
            if self.state.running:
                return False
            self.state = RunState(running=True, message="Starting.")
            self._limit_note = ""
            self._thread = threading.Thread(target=self._loop, daemon=True, name="nlh-studio")
            self._thread.start()
            return True

    def cancel(self) -> None:
        """Stop after the current safe step. Queued rows become cancelled; a generation that
        NotebookLM already accepted keeps going there and stays "generating" here, so Resume
        can still collect it."""
        self.state.cancel.set()
        self.state.message = "Stopping after the current step."

    def view(self) -> dict:
        """JSON view for the page."""
        s = self.state
        left = max(0.0, s.pause_until - self.clock()) if s.pause_until else None
        return {
            "running": s.running,
            "current": s.current,
            "step": s.step,
            "pause_left_s": round(left) if left is not None else None,
            "message": s.message,
            "cancelling": s.cancel.is_set() and s.running,
        }

    def join(self, timeout: float | None = None) -> None:
        """Wait for the thread to end (tests)."""
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------ the loop

    def _wait(self, seconds: float) -> bool:
        """Wait, waking early on Cancel. True if cancelled."""
        if self._sleep is not None:
            self._sleep(seconds)
            return self.state.cancel.is_set()
        return self.state.cancel.wait(seconds)

    def _loop(self) -> None:
        try:
            first = True
            while True:
                if self.state.cancel.is_set():
                    self._cancel_queued()
                    self.state.message = (
                        "Stopped. Queued overviews were cancelled. Anything already being made in"
                        " NotebookLM stays in the list; Resume collects it."
                    )
                    return
                # 1. finish anything already accepted by NotebookLM (after a restart or Resume)
                running = self.led.queue_items(("generating",))
                if running:
                    halt = self._follow(running[0])
                    if halt:  # stop without cancelling anything; Resume continues
                        self.state.message = halt
                        return
                    continue
                item, why = self._next_queued()
                if item is None:
                    notes = " ".join(n for n in (why, self._limit_note) if n)
                    self.state.message = notes or "Queue finished."
                    return
                if not first:
                    pause = (
                        self.pause_override
                        if self.pause_override is not None
                        else app_settings.load()["studio_pause_s"]
                    )
                    self.state.step, self.state.current = "Pausing between requests", None
                    self.state.pause_until = self.clock() + pause
                    cancelled = self._wait(pause)
                    self.state.pause_until = None
                    if cancelled:
                        continue
                first = False
                self._halt = None
                self._submit(item)
                if self._halt:
                    self.state.message = self._halt
                    return
        except Exception as exc:  # last resort: plain message, detail (type only) in the log
            log.exception("studio runner stopped: %s", type(exc).__name__)
            self.state.message = "Studio stopped because of an unexpected error. Details are in the log."
        finally:
            self.state.running = False
            self.state.current = None
            self.state.step = ""
            self.state.pause_until = None

    def _next_queued(self) -> tuple[dict | None, str]:
        """The oldest queued row whose account may still send today, and why there is none.

        Rows of an account that reached the app's own cap stay queued (they can go tomorrow);
        rows of an account Google refused today become paused_limit.
        """
        day = self.today()
        cap = app_settings.load()["studio_daily_cap"]
        capped = []
        for item in self.led.queue_items(("queued",)):
            acct = item["account_id"]
            if self.led.limit_hit(acct, day):
                self.led.update_queue(item["id"], state="paused_limit", message=LIMIT_TEXT)
                continue
            if self.led.submitted_on(acct, day) >= cap:
                if acct not in capped:
                    capped.append(acct)
                continue
            return item, ""
        if capped:
            names = ", ".join(self._label(a) for a in capped)
            return (
                None,
                f"Daily limit reached for {names} ({cap} a day). The rest stays queued for tomorrow.",
            )
        return None, ""

    def _label(self, account_id: int) -> str:
        a = self.led.account(account_id)
        return a.label if a else "this account"

    def _cancel_queued(self) -> None:
        for item in self.led.queue_items(("queued",)):
            self.led.update_queue(item["id"], state="cancelled", message="Cancelled before it was sent.")

    # ------------------------------------------------------------ one item

    def _submit(self, item: dict) -> None:
        """Count, send, then follow the generation to the end."""
        day = self.today()
        acct = self.led.account(item["account_id"])
        if acct is None:
            self.led.update_queue(item["id"], state="failed", message="The account was removed.")
            return
        if not self.led.mark_submitting(item["id"], day):
            return  # changed meanwhile (cancelled or dropped)
        self.state.current, self.state.step = item["id"], "Sending the request"
        self.state.message = f"Sending: {item['title']}"
        try:
            sub = adapter.generate_audio(
                acct.profile_name,
                item["notebook_id"],
                _instructions(item),
                item["audio_format"],
                item["audio_length"],
                item["language"],
            )
        except adapter.AdapterError as exc:
            if exc.state == "limit":
                self._limit(item, day)
            else:
                self.led.update_queue(item["id"], state="failed", finished_at=now(), message=exc.message)
                if exc.state == "needs_sign_in":
                    self._pause_account(item["account_id"], exc.message)
            return
        self.led.update_queue(item["id"], state="generating", task_id=sub.task_id, message="Being made.")
        log.info("studio item %d accepted as task %s", item["id"], sub.task_id)
        self._halt = self._follow(self.led.queue_item(item["id"]))

    def _limit(self, item: dict, day: str) -> None:
        """Google refused for a limit: no more requests for this account today."""
        self.led.note_limit(item["account_id"], day)
        self.led.update_queue(item["id"], state="paused_limit", finished_at=now(), message=LIMIT_TEXT)
        for other in self.led.queue_items(("queued",)):
            if other["account_id"] == item["account_id"]:
                self.led.update_queue(other["id"], state="paused_limit", message=LIMIT_TEXT)
        self._limit_note = (
            f"NotebookLM's usage limit was reached for {self._label(item['account_id'])}; paused for today."
        )
        self.state.message = self._limit_note
        log.info("studio limit for account %d on %s", item["account_id"], day)

    def _pause_account(self, account_id: int, message: str) -> None:
        for other in self.led.queue_items(("queued",)):
            if other["account_id"] == account_id:
                self.led.update_queue(other["id"], state="failed", message=message)

    def _follow(self, item: dict) -> str | None:
        """Poll the notebook's audio list until this task is finished, removed or too slow.

        Returns a message when the runner must stop without cancelling anything (sign-in
        needed, much too slow), else None. A Cancel click returns None; the loop handles it.
        """
        acct = self.led.account(item["account_id"])
        nb = adapter.Notebook(item["notebook_id"], item["notebook_title"], None)
        self.state.current, self.state.step = item["id"], "Making the overview"
        self.state.message = f"Being made in NotebookLM: {item['title']} (usually about 3 minutes)"
        submitted = _parse(item["submitted_at"])
        missing, first_missing = 0, None
        while True:
            if self.state.cancel.is_set():
                return None
            try:
                ovs = adapter.list_audio_overviews(acct.profile_name, nb)
            except adapter.AdapterError as exc:
                self.led.update_queue(item["id"], message=f"Could not check yet: {exc.message}")
                if exc.state == "needs_sign_in":
                    return f"{exc.message} Then click Resume."
                ovs = None
            if ovs is not None:
                found = next((o for o in ovs if o.artifact_id == item["task_id"]), None)
                if found is None:
                    missing += 1
                    first_missing = first_missing or self.clock()
                    if missing >= MISSING_POLLS and self.clock() - first_missing >= MISSING_WINDOW_S:
                        # NotebookLM drops a request it will not make (often a daily limit).
                        self._limit(item, item["submit_day"] or self.today())
                        self.led.update_queue(
                            item["id"],
                            message="NotebookLM removed this request, usually because of its daily limit.",
                        )
                        return None
                else:
                    missing, first_missing = 0, None
                    if found.status == "completed":
                        self._harvest(item, found)
                        return None
                    if found.status == "failed":
                        self.led.update_queue(
                            item["id"],
                            state="failed",
                            finished_at=now(),
                            message="NotebookLM could not make it.",
                        )
                        return None
            if (
                submitted
                and (datetime.now(submitted.tzinfo) - submitted).total_seconds() > GENERATION_TIMEOUT_S
            ):
                self.led.update_queue(
                    item["id"],
                    message="Taking much longer than usual. It stays here; Resume checks it again.",
                )
                return "An overview is taking much longer than usual. Stopped; Resume checks it again."
            if self._wait(self.poll_s):
                continue  # loop top reports the stop

    # ------------------------------------------------------------ S3: harvest

    def _harvest(self, item: dict, ov: adapter.AudioOverview) -> None:
        """Save the finished overview through the normal pipeline and link the rows."""
        acct = self.led.account(item["account_id"])
        self.state.step = "Saving the MP3"
        out = Path(acct.output_dir) if acct and acct.output_dir else None
        if out is None or not out.is_dir():
            self.led.update_queue(
                item["id"],
                state="done",
                finished_at=now(),
                message="Made in NotebookLM, not saved: choose this account's folder, then harvest it.",
            )
            return
        result = harvest.harvest_one(
            self.led,
            acct,
            ov,
            out,
            template=app_settings.load()["naming_template"],
            origin="studio",
        )
        row = self.led.harvest(ov.artifact_id)
        if result.outcome in ("saved", "skipped") and row is not None:
            self.led.update_queue(
                item["id"], state="done", finished_at=now(), harvest_id=row.id, message=f"Saved: {ov.title}"
            )
            self.on_saved()
        else:
            self.led.update_queue(
                item["id"],
                state="done",
                finished_at=now(),
                message=f"Made, but saving failed: {result.message} Harvest it from the Library.",
            )
        self.led.record_attempt(
            acct.id,
            ov.artifact_id,
            result.outcome if result.outcome != "skipped" else "saved",
            result.message,
        )


LIMIT_TEXT = "Paused: NotebookLM's usage limit for this account today."


def _instructions(item: dict) -> str:
    """The focus text sent with the request (the title helps NotebookLM keep to it)."""
    focus = (item.get("focus") or "").strip()
    title = (item.get("title") or "").strip()
    if focus and title:
        return f"{title}. {focus}"
    return focus or title


def _parse(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
