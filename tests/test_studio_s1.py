"""Studio S1 (D-025): plan parsing, settings, schema v4 migration and the plan routes.

No test here reaches Google: the adapter's Studio calls are replaced with fakes.
"""

import json
import sqlite3
import time

import pytest

from nlh import adapter, server, studio
from nlh import settings as app_settings
from nlh.adapter import AudioOverview, Notebook
from nlh.demo import PLAN_SHAPES

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}

# ---------------------------------------------------------------- parser

SPIKE_SHAPE = """Based on the sources, I recommend **3 long audio overviews** [1].

1. **The Towers and Their Builders**
   * **Focus:** How towers were sited and built [1, 2].
   * **Key topics:** stone, iron.
2. **Optics and Light Characters**
   * **Focus:** Lenses and reflectors [3].
3. **Keepers and Night Navigation**
   * **Focus:** Routines and how ships use the lights [4].
"""


def test_parse_bold_count_numbered_focus():
    r = studio.parse_plan(SPIKE_SHAPE)
    assert r["count"] == 3 and r["method"] == "numbered_focus"
    assert [i["title"] for i in r["items"]] == [
        "The Towers and Their Builders",
        "Optics and Light Characters",
        "Keepers and Night Navigation",
    ]
    assert r["items"][0]["focus"] == "How towers were sited and built."  # citations and bold removed


def test_parse_headings_with_overview_prefix():
    r = studio.parse_plan(PLAN_SHAPES[1])
    assert r["count"] is None and r["method"] == "numbered_focus"
    assert r["items"][1] == {
        "title": "Bridges and Tunnels",
        "focus": "the engineering that crossed rivers and hills.",
    }


def test_parse_plain_numbered_list_with_word_count_and_nested_bullets():
    r = studio.parse_plan(PLAN_SHAPES[2])
    assert r["count"] == 4 and r["method"] == "numbered"
    assert len(r["items"]) == 4
    assert r["items"][0]["focus"] == "why there are two tides a day; spring and neap tides"
    assert r["items"][3] == {"title": "Reading a tide table", "focus": ""}


def test_parse_no_list_gives_no_rows():
    r = studio.parse_plan(PLAN_SHAPES[3])
    assert r == {"count": 1, "items": [], "method": "none"}
    assert studio.parse_plan("")["items"] == []


def test_parse_ignores_nested_numbered_lists_and_other_numbers():
    text = (
        "**Two overviews**\n"
        "1. Origins: a long look at where the river trade began and which towns grew from it.\n"
        "   1. sub point\n   2. sub point\n"
        "2. Decline: how railways replaced the boats and what remains along the banks today.\n"
        "Note 5. this is not an item\n"
    )
    r = studio.parse_plan(text)
    assert [i["title"] for i in r["items"]] == ["Origins", "Decline"]
    assert r["items"][0]["focus"].startswith("a long look")


def test_parse_caps_rows_and_lengths():
    text = "\n".join(f"{n}. Title {n}\nFocus: {'x' * 3000}" for n in range(1, 40))
    r = studio.parse_plan(text)
    assert len(r["items"]) == studio.MAX_ITEMS
    assert all(len(i["focus"]) <= studio.MAX_FOCUS for i in r["items"])


def test_clean_items_rules():
    assert studio.clean_items([{"title": " A ", "focus": " f "}]) == [{"title": "A", "focus": "f"}]
    for bad in ([], [{"title": ""}], [{"title": "x" * 201}], "nope", [1], [{"title": "t"}] * 21):
        with pytest.raises(ValueError):
            studio.clean_items(bad)


def test_local_day_format():
    assert len(studio.local_day()) == 10 and studio.local_day()[4] == "-"


# ---------------------------------------------------------------- settings


def test_studio_settings_defaults_and_limits(home):
    s = app_settings.load()
    assert s["studio_daily_cap"] == 4 and s["studio_pause_s"] == 45 and s["studio_shared_accounts"] == []
    app_settings.save(
        {"studio_daily_cap": 6, "studio_shared_accounts": [3, 1, 3], "studio_language": "pt-BR"}
    )
    s = app_settings.load()
    assert (
        s["studio_daily_cap"] == 6
        and s["studio_shared_accounts"] == [1, 3]
        and s["studio_language"] == "pt-BR"
    )
    for bad in (
        {"studio_daily_cap": 0},
        {"studio_daily_cap": 21},
        {"studio_daily_cap": True},
        {"studio_pause_s": 10},
        {"studio_shared_accounts": ["1"]},
        {"studio_language": "english please"},
        {"studio_ask_warned": "yes"},
    ):
        with pytest.raises(app_settings.SettingsError):
            app_settings.save(bad)


# ---------------------------------------------------------------- schema v4


def test_migration_from_v3_keeps_rows(home):
    from nlh import paths
    from nlh.ledger import SCHEMA, Ledger

    path = paths.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("ALTER TABLE harvests ADD COLUMN origin TEXT NOT NULL DEFAULT 'harvest'")
    c.execute("INSERT INTO accounts (profile_name, label, created_at) VALUES ('p', 'L', 't')")
    c.execute(
        "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title, notebook_title,"
        " saved_path, size_bytes, duration_s, sha256, saved_at)"
        " VALUES (1,'nb','a','T','N','/p',1,1.0,'h','t')"
    )
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()
    led = Ledger(path)
    try:
        assert led.conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert led.harvest("a").overview_title == "T"
        tables = {r[0] for r in led.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"studio_plans", "studio_queue"} <= tables
        pid = led.add_plan(1, "nb", "N", json.dumps([{"title": "x", "focus": ""}]))
        assert led.plan(pid)["notebook_title"] == "N"
        led.archive_plan(pid)
        assert led.plans() == [] and len(led.plans(include_archived=True)) == 1
    finally:
        led.close()


# ---------------------------------------------------------------- routes


@pytest.fixture
def app_client(ledger, monkeypatch):
    acct = ledger.add_account("p1", "Work", "w@example.com")
    ledger.update_account(acct.id, last_status="ok")
    listing = [
        (Notebook("nb1", "Garden", None), [AudioOverview("a1", "T", "nb1", "Garden", None, "completed")])
    ]
    ledger.save_listing(acct.id, server._dump_listing(listing))
    calls = []

    def fake_ask(profile, notebook_id, question):
        calls.append((profile, notebook_id, question))
        return adapter.PlanAnswer(SPIKE_SHAPE)

    monkeypatch.setattr(adapter, "ask_for_plan", fake_ask)
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    return app.test_client(), acct, calls


def _wait(c, job_id):
    for _ in range(300):
        j = c.get(f"/api/jobs/{job_id}", headers=H).get_json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_state_has_no_default_account(app_client):
    c, acct, _ = app_client
    st = c.get("/api/studio/state", headers=H).get_json()
    assert st["cap"] == 4 and st["used_today"] == {str(acct.id): 0}
    assert "account" not in st and "default_account" not in st  # never preselected
    assert st["accounts"][0]["shared"] is False


def test_ask_needs_one_time_acknowledgement(app_client):
    c, acct, calls = app_client
    body = {"account_id": acct.id, "notebook_id": "nb1"}
    r = c.post("/api/studio/ask", json=body, headers=H)
    assert r.status_code == 409 and r.get_json()["needs_ack"] is True and calls == []
    r = c.post("/api/studio/ask", json={**body, "acknowledged": True}, headers=H)
    job = _wait(c, r.get_json()["id"])
    assert job["state"] == "done" and len(job["result"]["parsed"]["items"]) == 3
    assert calls == [("p1", "nb1", studio.DEFAULT_QUESTION)]
    # Second ask: no acknowledgement needed any more.
    r = c.post("/api/studio/ask", json={**body, "question": "How many?"}, headers=H)
    _wait(c, r.get_json()["id"])
    assert calls[-1][2] == "How many?"


def test_ask_refuses_unknown_notebook_and_missing_account(app_client):
    c, acct, calls = app_client
    app_settings.save({"studio_ask_warned": True})
    assert c.post("/api/studio/ask", json={"notebook_id": "nb1"}, headers=H).status_code == 400
    r = c.post("/api/studio/ask", json={"account_id": acct.id, "notebook_id": "someone-elses"}, headers=H)
    assert r.status_code == 400 and calls == []
    r = c.post(
        "/api/studio/ask",
        json={"account_id": acct.id, "notebook_id": "nb1", "question": "q" * 2001},
        headers=H,
    )
    assert r.status_code == 400


def test_ask_answer_is_not_stored(app_client, ledger, caplog):
    c, acct, _ = app_client
    app_settings.save({"studio_ask_warned": True})
    r = c.post("/api/studio/ask", json={"account_id": acct.id, "notebook_id": "nb1"}, headers=H)
    _wait(c, r.get_json()["id"])
    dump = "\n".join(ledger.conn.iterdump())
    assert "Towers and Their Builders" not in dump  # raw answer and parsed rows not kept until approved
    assert "studio plan for notebook nb1: 3 items" in caplog.text
    assert "Towers" not in caplog.text  # only counts and ids reach the log


def test_keep_plan_and_hide(app_client):
    c, acct, _ = app_client
    items = [{"title": "Soil", "focus": "what it is"}, {"title": "Compost", "focus": ""}]
    r = c.post(
        "/api/studio/plans", json={"account_id": acct.id, "notebook_id": "nb1", "items": items}, headers=H
    )
    assert r.status_code == 200
    plans = c.get("/api/studio/state", headers=H).get_json()["plans"]
    assert plans[0]["items"] == items and plans[0]["notebook_title"] == "Garden"
    assert c.post(f"/api/studio/plans/{plans[0]['id']}/archive", headers={**H}).status_code == 200
    assert c.get("/api/studio/state", headers=H).get_json()["plans"] == []
    bad = c.post(
        "/api/studio/plans", json={"account_id": acct.id, "notebook_id": "nb1", "items": []}, headers=H
    )
    assert bad.status_code == 400


def test_notebooks_route(app_client):
    c, acct, _ = app_client
    r = c.get(f"/api/studio/notebooks?account_id={acct.id}", headers=H).get_json()
    assert r == {"loaded": True, "notebooks": [{"id": "nb1", "title": "Garden", "overviews": 1}]}


def test_limit_refusal_maps_to_limit_state():
    class FakeRate(Exception):
        pass

    err = adapter._studio_error(Exception("RPC failed: USER_DISPLAYABLE_ERROR quota"))
    assert err.state == "limit"
    assert adapter._studio_error(Exception("429 Too Many Requests")).state == "limit"


def test_quiet_library_silences_answer_text(capsys):
    import logging

    with adapter._QuietLibrary():
        logging.getLogger("notebooklm._chat").error("ANSWER TEXT SHOULD NOT APPEAR")
    assert "ANSWER TEXT" not in capsys.readouterr().err


def test_demo_mode_never_imports_the_library(tmp_path):
    """Demo mode's Studio calls are local fakes: the library is never even imported."""
    import os
    import subprocess
    import sys

    code = (
        "import sys; from nlh import demo, adapter; demo.install_fake_adapter();"
        "a = adapter.ask_for_plan('demo-work', 'demo-nb-00001', 'q');"
        "print(len(a.text) > 0, 'notebooklm' in sys.modules)"
    )
    env = {**os.environ, "NLH_HOME": str(tmp_path), "NLH_DEMO": "1", "NLH_DEMO_ASK_S": "0"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["True", "False"], out.stderr[-500:]
