"""Local web server: token, host check, endpoints, a harvest through the API (fake download)."""

import shutil
import time

import pytest
from conftest import needs_ffmpeg

from nlh import adapter, harvest, server
from nlh.adapter import AudioOverview, Notebook

TOKEN = "test-token"
H = {"X-NLH-Token": TOKEN}


@pytest.fixture
def client(ledger):
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    return app, app.test_client()


def _wait(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}", headers=H).get_json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_token_required(client):
    _, c = client
    assert c.get("/").status_code == 403
    assert c.get("/api/state").status_code == 403
    assert c.get("/api/state", headers={"X-NLH-Token": "wrong"}).status_code == 403
    assert c.get(f"/?t={TOKEN}").status_code == 200
    assert c.get("/api/state", headers=H).status_code == 200


def test_foreign_host_refused(client):
    _, c = client
    assert c.get("/api/state", headers={**H, "Host": "evil.example:80"}).status_code == 403


def test_page_has_app_name_and_csp(client):
    _, c = client
    r = c.get(f"/?t={TOKEN}")
    assert b"NotebookLM Harvester" in r.data and b"{{APP_NAME}}" not in r.data
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_state_lists_accounts(client, ledger):
    ledger.add_account("p1", "One", "one@example.com")
    _, c = client
    data = c.get("/api/state", headers=H).get_json()
    assert [a["label"] for a in data["accounts"]] == ["One"]


def test_label_and_output(client, ledger, tmp_path):
    a = ledger.add_account("p1", "One")
    _, c = client
    assert c.post(f"/api/accounts/{a.id}/label", json={"label": "Two"}, headers=H).status_code == 200
    assert (
        c.post(f"/api/accounts/{a.id}/output", json={"path": str(tmp_path / "x")}, headers=H).status_code
        == 400
    )
    assert c.post(f"/api/accounts/{a.id}/output", json={"path": str(tmp_path)}, headers=H).status_code == 200
    b = ledger.account(a.id)
    assert b.label == "Two" and b.output_dir == str(tmp_path.resolve())


def test_harvest_needs_folder_and_listing(client, ledger, tmp_path):
    a = ledger.add_account("p1", "One")
    _, c = client
    r = c.post(f"/api/accounts/{a.id}/harvest", json={"all_new": True}, headers=H)
    assert r.status_code == 400 and "folder" in r.get_json()["error"]
    ledger.update_account(a.id, output_dir=str(tmp_path))
    r = c.post(f"/api/accounts/{a.id}/harvest", json={"all_new": True}, headers=H)
    assert r.status_code == 400 and "Refresh" in r.get_json()["error"]


def test_browse_and_open_restricted(client, ledger, tmp_path):
    (tmp_path / "sub").mkdir()
    _, c = client
    data = c.get(f"/api/browse?path={tmp_path}", headers=H).get_json()
    assert str(tmp_path / "sub") in data["dirs"]
    r = c.post("/api/open", json={"path": str(tmp_path)}, headers=H)
    assert r.status_code == 400  # not an account folder, so it is refused


@needs_ffmpeg
def test_refresh_then_harvest_all_new_via_api(client, ledger, tmp_path, sample_m4a, monkeypatch):
    a = ledger.add_account("p1", "One")
    out = tmp_path / "out"
    out.mkdir()
    ledger.update_account(a.id, output_dir=str(out))
    nb = Notebook("nb-1", "Notebook", None)
    ovs = [
        AudioOverview("a1", "First Overview", "nb-1", "Notebook", None, "completed"),
        AudioOverview("a2", "Still Working", "nb-1", "Notebook", None, "in_progress"),
    ]
    monkeypatch.setattr(server.accounts, "check", lambda led, acct, **k: adapter.SessionStatus("ok", "ok"))
    monkeypatch.setattr(adapter, "list_library", lambda profile, progress=None: [(nb, ovs)])

    def fake_download(profile, ov, dest):
        shutil.copyfile(sample_m4a, dest)
        return dest

    monkeypatch.setattr(adapter, "download_overview", fake_download)
    _, c = client
    j = _wait(c, c.post(f"/api/accounts/{a.id}/refresh", headers=H).get_json()["id"])
    assert j["state"] == "done"
    lib = c.get(f"/api/accounts/{a.id}/library", headers=H).get_json()
    assert [o["status"] for o in lib["notebooks"][0]["overviews"]] == ["new", "in_progress"]

    j = _wait(c, c.post(f"/api/accounts/{a.id}/harvest", json={"all_new": True}, headers=H).get_json()["id"])
    assert j["state"] == "done" and j["message"].startswith("Saved 1")
    assert (out / "First Overview.mp3").exists()
    assert ledger.harvest("a1").saved_path == str(out / "First Overview.mp3")
    lib = c.get(f"/api/accounts/{a.id}/library", headers=H).get_json()
    assert lib["notebooks"][0]["overviews"][0]["status"] == harvest.SAVED
    # Second "all new" finds nothing to do.
    r = c.post(f"/api/accounts/{a.id}/harvest", json={"all_new": True}, headers=H)
    assert r.status_code == 400
    hist = c.get("/api/history", headers=H).get_json()
    assert len(hist) == 1 and hist[0]["present"]
    assert c.post("/api/open", json={"path": "/definitely/not/allowed"}, headers=H).status_code == 400


def test_make_folder(client, tmp_path):
    _, c = client
    r = c.post("/api/mkdir", json={"parent": str(tmp_path), "name": "Audio Overviews"}, headers=H)
    assert r.status_code == 200 and (tmp_path / "Audio Overviews").is_dir()
    assert (
        c.post("/api/mkdir", json={"parent": str(tmp_path), "name": "Audio Overviews"}, headers=H).status_code
        == 400
    )
    assert c.post("/api/mkdir", json={"parent": str(tmp_path), "name": "a/b"}, headers=H).status_code == 400
    assert c.post("/api/mkdir", json={"parent": str(tmp_path), "name": ".."}, headers=H).status_code == 400
    assert (
        c.post("/api/mkdir", json={"parent": str(tmp_path / "nope"), "name": "x"}, headers=H).status_code
        == 400
    )
    assert not (tmp_path / "a").exists()


def test_stale_key_gets_explanation_page(client):
    _, c = client
    r = c.get("/?t=old-key")
    assert r.status_code == 403 and b"old one" in r.data
    assert c.get("/favicon.ico").status_code == 204
    assert c.get("/api/state").get_json()["error"].startswith("This page's key")


def test_server_settings_persist(home, monkeypatch):
    monkeypatch.delenv("NLH_PORT", raising=False)
    p1, t1 = server._server_settings()
    p2, t2 = server._server_settings()
    assert (p1, t1) == (p2, t2)
    assert oct((home / "server.json").stat().st_mode & 0o777) in ("0o600",) or server.sys.platform == "win32"
