"""Studio S2 and S3 (D-025): the queue state machine, the daily cap counted on submit, the
limit stop, restart review, and the auto-harvest. Fakes stand in for every Google call."""

import shutil
from datetime import datetime, timedelta

import pytest
from conftest import needs_ffmpeg

from nlh import adapter, server
from nlh import settings as app_settings
from nlh.adapter import AudioOverview
from nlh.studio_runner import StudioRunner

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}


class FakeGoogle:
    """generate_audio, list_audio_overviews and download_overview, recorded."""

    def __init__(self, sample=None):
        self.sent = []
        self.made = {}  # task -> dict(notebook, title, status)
        self.refuse = set()  # focus words that trigger a limit refusal
        self.fail_network = False
        self.complete_after = 0  # list calls before a task shows completed
        self.vanish = set()  # tasks that never appear
        self.list_calls = 0
        self.sample = sample

    def generate(self, profile, notebook_id, focus, fmt, length, lang):
        self.sent.append((profile, notebook_id, focus, fmt, length, lang))
        if any(w in focus for w in self.refuse):
            raise adapter.AdapterError("limit", "limit")
        if self.fail_network:
            raise adapter.AdapterError("Could not reach NotebookLM.")
        task = f"task-{len(self.sent)}"
        self.made[task] = {"nb": notebook_id, "title": focus.split(".")[0], "polls": 0}
        return adapter.Submitted(task, "pending")

    def list(self, profile, nb):
        self.list_calls += 1
        out = []
        for task, m in self.made.items():
            if m["nb"] != nb.id or task in self.vanish:
                continue
            m["polls"] += 1
            done = m["polls"] > self.complete_after
            out.append(
                AudioOverview(
                    task,
                    m["title"] if done else nb.title,
                    nb.id,
                    nb.title,
                    datetime.now().astimezone().isoformat(),
                    "completed" if done else "in_progress",
                )
            )
        return out

    def download(self, profile, ov, dest):
        shutil.copyfile(self.sample, dest)
        return dest


@pytest.fixture
def g(monkeypatch, sample_m4a):
    fake = FakeGoogle(sample_m4a)
    monkeypatch.setattr(adapter, "generate_audio", fake.generate)
    monkeypatch.setattr(adapter, "list_audio_overviews", fake.list)
    monkeypatch.setattr(adapter, "download_overview", fake.download)
    return fake


@pytest.fixture
def setup(ledger, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    a = ledger.add_account("p1", "Work")
    b = ledger.add_account("p2", "Home")
    ledger.update_account(a.id, output_dir=str(out))
    ledger.update_account(b.id, output_dir=str(out))
    return a, b, out


def queue(ledger, account, titles, notebook="nb1"):
    return ledger.add_queue_items(
        [
            {
                "account_id": account.id,
                "notebook_id": notebook,
                "notebook_title": "Garden",
                "title": t,
                "focus": f"About {t}",
                "audio_format": "deep_dive",
                "audio_length": "long",
                "language": "en",
            }
            for t in titles
        ]
    )


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def runner(ledger, day="2026-11-14", clock=None):
    clock = clock or Clock()
    return StudioRunner(ledger, clock=clock, sleep=clock.sleep, today=lambda: day, poll_s=1, pause_s=1)


def run(r):
    assert r.start()
    r.join(30)
    assert not r.state.running


def states(ledger):
    return [x["state"] for x in ledger.queue_items()]


# ---------------------------------------------------------------- full path (S2 + S3)


@needs_ffmpeg
def test_plan_to_mp3_full_path_then_nothing_new(ledger, setup, g):
    a, _b, out = setup
    queue(ledger, a, ["Soil", "Compost"])
    assert g.sent == []  # queuing sends nothing
    run(runner(ledger))
    assert states(ledger) == ["done", "done"]
    assert [s[2] for s in g.sent] == ["Soil. About Soil", "Compost. About Compost"]
    assert g.sent[0][3:] == ("deep_dive", "long", "en")
    rows = ledger.queue_items()
    for row in rows:
        h = ledger.harvest_by_id(row["harvest_id"])
        assert h is not None and h.origin == "studio" and h.artifact_id == row["task_id"]
        assert h.file_present and h.saved_path.endswith(".mp3") and h.saved_path.startswith(str(out))
    assert ledger.submitted_on(a.id, "2026-11-14") == 2
    # A second run produces nothing new.
    sent = len(g.sent)
    run(runner(ledger))
    assert len(g.sent) == sent and len(ledger.harvests()) == 2


@needs_ffmpeg
def test_queue_waits_the_saved_pause_in_seconds(ledger, setup, g, home):
    """Two hours chosen in Settings is stored as 7200 and the queue waits 7200 s (fake clock)."""
    a, _b, _out = setup
    app_settings.save({"studio_pause_s": 7200})
    queue(ledger, a, ["One", "Two"])
    clock = Clock()
    waits = []

    def sleep(s):
        waits.append(s)
        clock.t += s

    r = StudioRunner(ledger, clock=clock, sleep=sleep, today=lambda: "2026-11-14", poll_s=1)
    run(r)
    assert states(ledger) == ["done", "done"]
    assert 7200 in waits and waits.count(7200) == 1  # one pause, between the two requests


# ---------------------------------------------------------------- cap


@needs_ffmpeg
def test_cap_counts_on_submit_and_resets_next_day(ledger, setup, g, home):
    a, _b, _out = setup
    app_settings.save({"studio_daily_cap": 1})
    queue(ledger, a, ["One", "Two"])
    r = runner(ledger, "2026-11-14")
    run(r)
    assert states(ledger) == ["done", "queued"]
    assert "Daily limit reached for Work" in r.state.message
    run(runner(ledger, "2026-11-15"))  # after local midnight
    assert states(ledger) == ["done", "done"]
    assert ledger.submitted_on(a.id, "2026-11-15") == 1


def test_failed_request_still_counts(ledger, setup, g, home):
    a, _b, _out = setup
    g.fail_network = True
    queue(ledger, a, ["One"])
    run(runner(ledger))
    assert states(ledger) == ["needs_review"]  # it may have reached Google: check, never resend blind
    assert ledger.submitted_on(a.id, "2026-11-14") == 1  # counted before the call: no undercount
    assert len(g.sent) == 1  # never retried


def test_cap_survives_restart(ledger, setup, g, home):
    a, _b, _out = setup
    app_settings.save({"studio_daily_cap": 1})
    ids = queue(ledger, a, ["One", "Two"])
    ledger.mark_submitting(ids[0], "2026-11-14")  # the app stopped right after counting
    ledger.recover_after_restart()
    run(runner(ledger))
    assert len(g.sent) == 0  # the cap already holds one for today
    assert states(ledger) == ["needs_review", "queued"]


# ---------------------------------------------------------------- limit


@needs_ffmpeg
def test_limit_stops_that_account_for_the_day(ledger, setup, g):
    a, b, _out = setup
    g.refuse = {"Two"}
    queue(ledger, a, ["One", "Two", "Three"])
    queue(ledger, b, ["Other"])
    run(runner(ledger))
    assert states(ledger) == ["done", "paused_limit", "paused_limit", "done"]
    assert [s[2].split(".")[0] for s in g.sent] == ["One", "Two", "Other"]  # no retry, Three never sent
    assert ledger.limit_hit(a.id, "2026-11-14") and not ledger.limit_hit(b.id, "2026-11-14")
    queue(ledger, a, ["Four"])
    run(runner(ledger))
    assert len(g.sent) == 3 and states(ledger)[-1] == "paused_limit"  # same day: nothing sent


def test_request_removed_by_google_counts_as_limit(ledger, setup, g):
    a, _b, _out = setup
    g.complete_after = 10**6
    queue(ledger, a, ["One", "Two"])
    original = g.generate

    def gen(*args):
        sub = original(*args)
        g.vanish.add(sub.task_id)
        return sub

    g.generate = gen
    adapter.generate_audio = gen
    run(runner(ledger))
    assert states(ledger) == ["paused_limit", "paused_limit"]
    assert ledger.limit_hit(a.id, "2026-11-14")
    assert "removed this request" in ledger.queue_items()[0]["message"]


# ---------------------------------------------------------------- cancel, drop, requeue, restart


@needs_ffmpeg
def test_cancel_during_generation_keeps_it_for_resume(ledger, setup, g):
    a, _b, _out = setup
    g.complete_after = 3
    queue(ledger, a, ["One", "Two"])
    clock = Clock()
    r = runner(ledger, clock=clock)

    def sleep(s):
        clock.t += s
        if g.list_calls >= 1:
            r.cancel()

    r._sleep = sleep
    run(r)
    assert states(ledger) == ["generating", "cancelled"]
    run(runner(ledger))  # Resume collects it; nothing new is sent
    assert states(ledger) == ["done", "cancelled"] and len(g.sent) == 1


@pytest.fixture
def client(ledger, setup, g):
    listing = [(adapter.Notebook("nb1", "Garden", None, "owner"), [])]
    for acct in setup[:2]:
        ledger.save_listing(acct.id, server._dump_listing(listing))
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    return app, app.test_client()


def test_queue_route_validates_and_sends_nothing(client, ledger, setup, g):
    app, c = client
    a = setup[0]
    pid = ledger.add_plan(a.id, "nb1", "Garden", '[{"title": "x", "focus": ""}]')
    bad = c.post(
        "/api/studio/queue",
        json={"plan_id": pid, "items": [{"title": "x", "audio_format": "loud"}]},
        headers=H,
    )
    assert bad.status_code == 400
    r = c.post(
        "/api/studio/queue",
        json={
            "plan_id": pid,
            "items": [{"title": "Soil", "focus": "f", "audio_format": "brief", "audio_length": "short"}],
        },
        headers=H,
    )
    assert r.status_code == 200 and g.sent == []
    row = r.get_json()["rows"][0]
    assert (row["state"], row["audio_format"], row["audio_length"], row["language"]) == (
        "queued",
        "brief",
        "short",
        "en",
    )
    assert r.get_json()["runner"]["running"] is False  # never starts on its own


def test_drop_and_requeue_rules(client, ledger, setup):
    _app, c = client
    a = setup[0]
    ids = queue(ledger, a, ["One", "Two"])
    assert c.post(f"/api/studio/queue/{ids[0]}/requeue", headers=H).status_code == 400  # still queued
    assert c.post(f"/api/studio/queue/{ids[0]}/drop", headers=H).status_code == 200
    assert ledger.queue_item(ids[0])["state"] == "cancelled"
    assert c.post(f"/api/studio/queue/{ids[0]}/drop", headers=H).status_code == 400  # already finished
    assert c.post(f"/api/studio/queue/{ids[0]}/requeue", headers=H).status_code == 200
    assert states(ledger) == ["cancelled", "queued", "queued"]  # a new row; the old one stays


def test_restart_review_finds_or_fails_never_resends(client, ledger, setup, g):
    app, c = client
    a = setup[0]
    ids = queue(ledger, a, ["One", "Two"])
    ledger.mark_submitting(ids[0], "2026-11-14")
    ledger.mark_submitting(ids[1], "2026-11-14")
    assert ledger.recover_after_restart() == 2
    assert states(ledger) == ["needs_review", "needs_review"]
    # Google did accept the first one before the stop.
    g.made["task-x"] = {"nb": "nb1", "title": "One", "polls": 0}
    r = c.post(f"/api/studio/queue/{ids[0]}/check", headers=H).get_json()
    assert r["found"] is True and ledger.queue_item(ids[0])["task_id"] == "task-x"
    r = c.post(f"/api/studio/queue/{ids[1]}/check", headers=H).get_json()
    assert r["found"] is False and ledger.queue_item(ids[1])["state"] == "failed"
    assert g.sent == []  # checking never sends anything


def test_run_route_refuses_empty_queue_and_double_start(client, ledger, setup, g):
    app, c = client
    assert c.post("/api/studio/run", headers=H).status_code == 400
    g.complete_after = 10**6
    queue(ledger, setup[0], ["One"])
    runner_obj = app.config["STUDIO_RUNNER"]
    runner_obj.poll_s = 0.05
    assert c.post("/api/studio/run", headers=H).status_code == 200
    assert c.post("/api/studio/run", headers=H).status_code == 409
    c.post("/api/studio/cancel", headers=H)
    runner_obj.join(10)
    assert not runner_obj.state.running


def test_no_output_folder_marks_done_without_saving(ledger, g):
    acct = ledger.add_account("p3", "No folder")
    queue(ledger, acct, ["One"])
    run(runner(ledger))
    row = ledger.queue_items()[0]
    assert (
        row["state"] == "done"
        and row["harvest_id"] is None
        and "choose the account's folder" in row["message"]
    )


def test_generation_timeout_fails_that_row_and_carries_on(ledger, setup, g):
    a, _b, _out = setup
    g.complete_after = 10**6
    ids = queue(ledger, a, ["Slow"])
    old = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
    ledger.mark_submitting(ids[0], "2026-11-14")
    ledger.update_queue(ids[0], state="generating", task_id="task-old", submitted_at=old)
    g.made["task-old"] = {"nb": "nb1", "title": "Slow", "polls": -(10**6)}  # never finishes
    queue(ledger, a, ["Next"])
    g.complete_after = 0
    r = runner(ledger)
    run(r)
    assert states(ledger)[0] == "failed" and "40 minutes" in ledger.queue_items()[0]["message"]
    assert len(g.sent) == 1  # the stuck one does not block the rest


@needs_ffmpeg
def test_final_message_keeps_the_limit_note(ledger, setup, g):
    a, b, _out = setup
    g.refuse = {"Two"}
    queue(ledger, a, ["One", "Two"])
    queue(ledger, b, ["Other"])
    r = runner(ledger)
    run(r)
    assert "usage limit was reached for Work" in r.state.message


def test_sign_in_needed_while_following_stops_without_cancelling(ledger, setup, g):
    a, _b, _out = setup
    g.complete_after = 10**6
    queue(ledger, a, ["One", "Two"])
    calls = {"n": 0}

    def flaky(profile, nb):
        calls["n"] += 1
        raise adapter.AdapterError(
            "This account's Google sign-in has expired. Use Sign in again.", "needs_sign_in"
        )

    adapter.list_audio_overviews = flaky
    r = runner(ledger)
    run(r)
    assert states(ledger) == ["generating", "queued"]  # nothing cancelled, nothing re-sent
    assert "Then click Resume" in r.state.message and len(g.sent) == 1


# ---------------------------------------------------------------- code review fixes


@needs_ffmpeg
def test_studio_save_holds_the_harvest_lock(ledger, setup, g, monkeypatch):
    from nlh import harvest

    seen = []
    real = harvest.harvest_one

    def spy(*a, **k):
        seen.append(harvest.WORK_LOCK.locked())
        return real(*a, **k)

    monkeypatch.setattr(harvest, "harvest_one", spy)
    queue(ledger, setup[0], ["One"])
    run(runner(ledger))
    assert seen == [True]


def test_sign_in_refusal_on_send_halts_and_keeps_the_rest_queued(ledger, setup, g):
    a, _b, _out = setup

    def refuse(*args):
        g.sent.append(args)
        raise adapter.AdapterError(
            "This account's Google sign-in has expired. Use Sign in again.", "needs_sign_in"
        )

    adapter.generate_audio = refuse
    queue(ledger, a, ["One", "Two", "Three"])
    r = runner(ledger)
    run(r)
    assert states(ledger) == ["failed", "queued", "queued"] and len(g.sent) == 1
    assert "click Start after signing in" in r.state.message


def test_requeue_only_once(client, ledger, setup):
    _app, c = client
    ids = queue(ledger, setup[0], ["One"])
    ledger.update_queue(ids[0], state="failed", message="boom")
    assert c.post(f"/api/studio/queue/{ids[0]}/requeue", headers=H).status_code == 200
    assert c.post(f"/api/studio/queue/{ids[0]}/requeue", headers=H).status_code == 409
    assert states(ledger) == ["failed", "queued"]


def test_linked_task_ids_cover_every_row(ledger, setup):
    ids = queue(ledger, setup[0], [f"T{i}" for i in range(230)])
    ledger.update_queue(ids[-1], task_id="late-task")
    assert "late-task" in ledger.linked_task_ids()


def test_removed_from_an_earlier_day_does_not_pause_today(ledger, setup, g):
    a, _b, _out = setup
    ids = queue(ledger, a, ["Old"])
    ledger.mark_submitting(ids[0], "2026-11-13")
    ledger.update_queue(ids[0], state="generating", task_id="gone")
    queue(ledger, a, ["Today"])
    run(runner(ledger, "2026-11-14"))
    rows = ledger.queue_items()
    assert rows[0]["state"] == "failed" and "removed" in rows[0]["message"]
    assert not ledger.limit_hit(a.id, "2026-11-14") and rows[1]["state"] == "done"


def test_windows_too_deep_folder_refused_before_sending(ledger, setup, g, monkeypatch, tmp_path):
    """D-023 for Studio: a folder too deep for the Windows cap fails the row before any request,
    so no daily generation is spent and nothing is written."""
    from nlh import naming, paths

    a, _b, _out = setup
    deep = tmp_path / "deep"
    deep.mkdir()
    ledger.update_account(a.id, output_dir=str(deep))
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    # Treat the folder as about 200 characters long, whatever the real temp path is.
    monkeypatch.setattr(naming, "WIN_MAX_PATH", 240 - 200 + len(str(deep.resolve())))
    queue(ledger, a, ["Soil"])
    run(runner(ledger))
    row = ledger.queue_items()[0]
    assert row["state"] == "failed" and "too long for Windows" in row["message"]
    assert "Nothing was sent" in row["message"]
    assert g.sent == [] and ledger.submitted_on(a.id, "2026-11-14") == 0
    assert list(deep.iterdir()) == []


@needs_ffmpeg
def test_studio_save_under_the_windows_cap_is_shortened(ledger, setup, g, monkeypatch, tmp_path):
    """A deep but workable folder: the Studio save goes through and its path stays under 240."""
    import os

    from nlh import naming, paths

    a, _b, _out = setup
    deep = tmp_path / "deep"
    deep.mkdir()
    ledger.update_account(a.id, output_dir=str(deep))
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    monkeypatch.setattr(naming, "WIN_MAX_PATH", 240 - 100 + len(str(deep.resolve())))
    long_title = "Lighthouse " + "lenses " * 40
    queue(ledger, a, [long_title.strip()])
    run(runner(ledger))
    row = ledger.queue_items()[0]
    assert row["state"] == "done" and row["harvest_id"], row["message"]
    h = ledger.harvest_by_id(row["harvest_id"])
    assert h.file_present
    assert len(os.path.abspath(h.saved_path)) + naming.SUFFIX_ROOM < naming.WIN_MAX_PATH
