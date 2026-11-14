"""U1 backend: whole-library rows, harvest exactly the selection, cancel, settings."""

import shutil
import threading
import time

import pytest
from conftest import needs_ffmpeg

from nlh import adapter, harvest, server
from nlh import settings as app_settings
from nlh.adapter import AudioOverview, Notebook

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}


def _wait(c, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}", headers=H).get_json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


@pytest.fixture
def lib(ledger, tmp_path, monkeypatch):
    """Two accounts with cached listings; account 2 has no listing yet."""
    a1 = ledger.add_account("p1", "One", "one@example.com")
    a2 = ledger.add_account("p2", "Two", "two@example.com")
    out = tmp_path / "out"
    out.mkdir()
    ledger.update_account(a1.id, output_dir=str(out))
    nb = Notebook("nb-1", "Garden", None)
    ovs = [
        AudioOverview(
            f"a{i}",
            f"Episode {i}",
            "nb-1",
            "Garden",
            f"2026-11-0{i}T10:00:00+00:00",
            "in_progress" if i == 5 else "completed",
        )
        for i in range(1, 6)
    ]
    ledger.save_listing(a1.id, server._dump_listing([(nb, ovs)]))
    ledger.record_attempt(a1.id, "a4", "failed", "Could not reach NotebookLM.")
    app = server.create_app(TOKEN, ledger)
    client = app.test_client()
    client.ledger = ledger
    return client, a1, a2, out, ovs


def test_library_rows_and_statuses(lib):
    c, a1, a2, out, ovs = lib
    data = c.get("/api/library", headers=H).get_json()
    assert data["never_refreshed"] == [a2.id]
    by_id = {r["artifact_id"]: r for r in data["rows"]}
    assert set(by_id) == {"a1", "a2", "a3", "a4", "a5"}
    assert by_id["a1"]["status"] == "new" and by_id["a4"]["status"] == "failed"
    assert by_id["a4"]["last_error"].startswith("Could not")
    assert by_id["a5"]["status"] == "in_progress"
    assert all(r["multi"] for r in data["rows"]) and by_id["a1"]["notebook"] == "Garden"
    assert "cookie" not in str(data).lower()


@needs_ffmpeg
def test_harvest_exactly_the_selection(lib, sample_m4a, monkeypatch):
    c, a1, _a2, out, _ovs = lib

    def fake(profile, ov, dest):
        shutil.copyfile(sample_m4a, dest)
        return dest

    monkeypatch.setattr(adapter, "download_overview", fake)
    picked = [{"account_id": a1.id, "artifact_id": a} for a in ("a2", "a3")]
    job = c.post("/api/harvest", json={"items": picked}, headers=H).get_json()
    j = _wait(c, job["id"])
    assert j["state"] == "done" and j["message"].startswith("Saved 2")
    saved = sorted(p.name for p in out.rglob("*.mp3"))
    assert saved == ["Episode 2.mp3", "Episode 3.mp3"]

    rows = {r["artifact_id"]: r for r in c.get("/api/library", headers=H).get_json()["rows"]}
    assert rows["a2"]["status"] == rows["a3"]["status"] == "saved"
    assert rows["a1"]["status"] == "new"  # not selected, untouched
    assert sorted(h.artifact_id for h in c.ledger.harvests()) == ["a2", "a3"]
    for h in c.ledger.harvests():
        assert h.saved_path in {str(p) for p in out.rglob("*.mp3")}


@needs_ffmpeg
def test_cancel_leaves_no_part_and_no_row(lib, sample_m4a, monkeypatch):
    c, a1, _a2, out, _ovs = lib
    release = threading.Event()

    def slow(profile, ov, dest):
        release.wait(10)
        shutil.copyfile(sample_m4a, dest)
        return dest

    monkeypatch.setattr(adapter, "download_overview", slow)
    picked = [{"account_id": a1.id, "artifact_id": a} for a in ("a1", "a2", "a3")]
    job = c.post("/api/harvest", json={"items": picked}, headers=H).get_json()
    time.sleep(0.5)
    c.post(f"/api/jobs/{job['id']}/cancel", headers=H)
    release.set()
    j = _wait(c, job["id"])
    assert j["state"] == "cancelled"
    assert list(out.rglob("*.mp3")) == [] and list(out.rglob("*.part")) == []
    rows = {r["artifact_id"]: r for r in c.get("/api/library", headers=H).get_json()["rows"]}
    assert all(rows[a]["status"] == "new" for a in ("a1", "a2", "a3"))
    assert sum(it["outcome"] == "cancelled" for it in j["items"].values()) == 3


def test_harvest_refuses_bad_items(lib):
    c, _a1, a2, _out, _ovs = lib
    assert c.post("/api/harvest", json={"items": [{"account_id": "x"}]}, headers=H).status_code == 400
    r = c.post("/api/harvest", json={"items": [{"account_id": a2.id, "artifact_id": "a1"}]}, headers=H)
    assert r.status_code == 400


def test_settings_roundtrip_and_validation(home):
    s = app_settings.load()
    assert s["theme"] == "auto" and [v["name"] for v in s["views"]][:1] == ["New to harvest"]
    s = app_settings.save({"theme": "dark", "history_page_size": 100})
    assert app_settings.load()["theme"] == "dark"
    for bad in (
        {"theme": "neon"},
        {"unknown": 1},
        {"naming_template": "{notebook}/{x}"},
        {"naming_template": "a/b/{title}"},
        {"naming_template": "{notebook}"},
        {"views": [{"name": "A"}, {"name": "a"}]},
        {"history_page_size": 7},
    ):
        with pytest.raises(app_settings.SettingsError):
            app_settings.save(bad)
    text = (home / "settings.json").read_text()
    assert "token" not in text and "cookie" not in text.lower()


def test_unreachable_status_in_library(ledger, tmp_path):
    a = ledger.add_account("p1", "One")
    ledger.update_account(a.id, output_dir=str(tmp_path / "gone"))
    nb = Notebook("nb", "N", None)
    ov = AudioOverview("x1", "T", "nb", "N", None, "completed")
    ledger.save_listing(a.id, server._dump_listing([(nb, [ov])]))
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=a.id,
            notebook_id="nb",
            artifact_id="x1",
            overview_title="T",
            notebook_title="N",
            created_at=None,
            saved_path=str(tmp_path / "gone" / "N" / "T.mp3"),
            size_bytes=1,
            duration_s=1,
            sha256="h",
        )
    c = server.create_app(TOKEN, ledger).test_client()
    row = c.get("/api/library", headers=H).get_json()["rows"][0]
    assert row["status"] == harvest.UNREACHABLE
