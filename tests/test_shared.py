"""P2-B "Shared with me": roles from the list row, old caches, folder prefix and path cap,
"all new" skipping shared, Studio refusing view-only and unknown notebooks, history filter."""

import shutil
from types import SimpleNamespace

import pytest
from conftest import needs_ffmpeg

from nlh import adapter, harvest, naming, paths, server
from nlh.adapter import AudioOverview, Notebook

TOKEN = "k"
H = {"X-NLH-Token": TOKEN, "X-NLH-Req": "1"}


@pytest.mark.parametrize(
    "raw,role", [(1, "owner"), (2, "editor"), (3, "viewer"), (None, "unknown"), (9, "unknown")]
)
def test_role_from_the_list_row(raw, role):
    assert adapter._role(SimpleNamespace(role=raw)) == role
    assert adapter._role(SimpleNamespace()) == "unknown"


def test_shared_means_editor_or_viewer():
    assert not Notebook("a", "A", None, "owner").shared
    assert Notebook("a", "A", None, "editor").shared and Notebook("a", "A", None, "viewer").shared
    assert not Notebook("a", "A", None).shared  # unknown is not shown as shared


def test_old_cache_reads_as_unknown():
    old = (
        '[{"nb": {"id": "n", "title": "T", "created_at": null}, "ovs": [{"artifact_id": "a", "title": "x",'
        ' "notebook_id": "n", "notebook_title": "T", "created_at": null, "status": "completed"}]}]'
    )
    ((nb, ovs),) = server._load_listing(old)
    assert nb.role == "unknown" and ovs[0].notebook_role == "unknown"
    again = server._load_listing(server._dump_listing([(Notebook("n", "T", None, "viewer"), ovs)]))
    assert again[0][0].role == "viewer"


def test_folder_prefix_only_for_shared():
    assert naming.folder_title("Garden", True) == "Shared - Garden"
    assert naming.folder_title("Garden", False) == "Garden"
    long = "x" * 200
    assert naming.folder_name(naming.folder_title(long, True)).startswith("Shared - ")
    assert len(naming.folder_name(naming.folder_title(long, True))) <= naming.MAX_FOLDER


def test_windows_cap_counts_the_prefix(tmp_path, monkeypatch):
    """Somewhere the plain folder still fits under 240 characters and the "Shared - " one does not."""
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    title = "n" * 30
    found = False
    for depth in range(1, 240):
        base = tmp_path / ("d" * depth)
        try:
            naming.check_fits(base, title)
        except naming.PathTooLong:
            break
        try:
            naming.check_fits(base, naming.folder_title(title, True))
        except naming.PathTooLong:
            found = True
            break
    assert found


@needs_ffmpeg
def test_shared_overview_saved_under_shared_folder_with_real_tags(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Work")
    out = tmp_path / "out"
    out.mkdir()
    ov = AudioOverview("s1", "Episode", "nb", "Their notebook", None, "completed", "viewer")

    def dl(profile, o, dest):
        shutil.copyfile(sample_m4a, dest)
        return dest

    r = harvest.harvest_one(ledger, acct, ov, out, downloader=dl)
    assert r.outcome == "saved"
    row = ledger.harvest("s1")
    assert row.saved_path == str(out / "Shared - Their notebook" / "Episode.mp3")
    assert row.notebook_title == "Their notebook"  # ledger and tags keep the real title
    from nlh import media

    assert media.probe(out / "Shared - Their notebook" / "Episode.mp3").tags.get("album") == "Their notebook"


# ---------------------------------------------------------------- server


@pytest.fixture
def app(ledger, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    acct = ledger.add_account("p1", "Work")
    ledger.update_account(acct.id, output_dir=str(out), last_status="ok")
    listing = [
        (
            Notebook("own", "Mine", None, "owner"),
            [AudioOverview("a1", "A", "own", "Mine", None, "completed", "owner")],
        ),
        (
            Notebook("ed", "Edit", None, "editor"),
            [AudioOverview("a2", "B", "ed", "Edit", None, "completed", "editor")],
        ),
        (
            Notebook("vw", "View", None, "viewer"),
            [AudioOverview("a3", "C", "vw", "View", None, "completed", "viewer")],
        ),
        (Notebook("un", "Unknown", None), [AudioOverview("a4", "D", "un", "Unknown", None, "completed")]),
    ]
    ledger.save_listing(acct.id, server._dump_listing(listing))
    a = server.create_app(TOKEN, ledger)
    a.testing = True
    return a, acct


def test_library_rows_carry_role(app):
    a, _ = app
    rows = {r["artifact_id"]: r for r in a.test_client().get("/api/library", headers=H).get_json()["rows"]}
    assert (rows["a1"]["role"], rows["a1"]["shared"]) == ("owner", False)
    assert rows["a2"]["shared"] and rows["a3"]["shared"] and not rows["a4"]["shared"]


def test_all_new_skips_shared_unless_asked(app, monkeypatch):
    a, acct = app
    monkeypatch.setattr(harvest, "harvest_many", lambda *x, **k: [])  # no downloads in this test
    c = a.test_client()
    c.get(f"/api/accounts/{acct.id}/library", headers=H)  # load the listing
    job = c.post(f"/api/accounts/{acct.id}/harvest", json={"all_new": True}, headers=H).get_json()
    assert job["title"].startswith("Harvesting 2 ")  # own and unknown; editor and viewer skipped
    _finish(c, job["id"])
    job = c.post(
        f"/api/accounts/{acct.id}/harvest", json={"all_new": True, "include_shared": True}, headers=H
    ).get_json()
    assert job["title"].startswith("Harvesting 4 ")
    _finish(c, job["id"])  # never leave a job running past the test (the ledger closes after it)


def _finish(c, job_id):
    import time

    for _ in range(200):
        if c.get(f"/api/jobs/{job_id}", headers=H).get_json()["state"] not in ("queued", "running"):
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_studio_refuses_view_only_and_unknown(app, monkeypatch):
    a, acct = app
    from nlh import settings as app_settings

    app_settings.save({"studio_ask_warned": True})
    monkeypatch.setattr(adapter, "ask_for_plan", lambda *x: pytest.fail("Studio must not ask here"))
    c = a.test_client()
    nbs = {
        n["id"]: n
        for n in c.get(f"/api/studio/notebooks?account_id={acct.id}", headers=H).get_json()["notebooks"]
    }
    assert nbs["own"]["studio_ok"] and nbs["ed"]["studio_ok"]
    assert not nbs["vw"]["studio_ok"] and "view only" in nbs["vw"]["studio_note"]
    assert not nbs["un"]["studio_ok"] and "Refresh" in nbs["un"]["studio_note"]
    for nb in ("vw", "un"):
        assert (
            c.post("/api/studio/ask", json={"account_id": acct.id, "notebook_id": nb}, headers=H).status_code
            == 403
        )
        r = c.post(
            "/api/studio/plans",
            json={"account_id": acct.id, "notebook_id": nb, "items": [{"title": "t"}]},
            headers=H,
        )
        assert r.status_code == 403


def test_history_shared_filter_and_counts(app, ledger, tmp_path):
    a, acct = app
    for art, nb, title in (("a1", "own", "Mine"), ("a3", "vw", "View")):
        with ledger.transaction() as cx:
            ledger.record(
                cx,
                account_id=acct.id,
                notebook_id=nb,
                artifact_id=art,
                overview_title=art,
                notebook_title=title,
                created_at=None,
                saved_path=str(tmp_path / f"{art}.mp3"),
                size_bytes=1,
                duration_s=1.0,
                sha256="x",
            )
    c = a.test_client()
    d = c.get("/api/history/page", headers=H).get_json()
    assert d["shared_counts"] == {"owner": 1, "shared": 1} and d["total"] == 2
    rows = {r["artifact_id"]: r for r in d["rows"]}
    assert rows["a3"]["shared"] and rows["a3"]["role"] == "viewer" and not rows["a1"]["shared"]
    assert [
        r["artifact_id"] for r in c.get("/api/history/page?shared=yes", headers=H).get_json()["rows"]
    ] == ["a3"]
    assert [r["artifact_id"] for r in c.get("/api/history/page?shared=no", headers=H).get_json()["rows"]] == [
        "a1"
    ]
