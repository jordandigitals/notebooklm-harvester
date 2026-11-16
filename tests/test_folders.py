"""D-033: remembered folders, and one folder shared by several accounts."""

import shutil

import pytest
from conftest import needs_ffmpeg

from nlh import harvest, media, server
from nlh import settings as app_settings
from nlh.adapter import AudioOverview

TOKEN = "test-token"
H = {"X-NLH-Token": TOKEN}


@pytest.fixture
def client(ledger):
    app = server.create_app(TOKEN, ledger)
    app.testing = True
    return app, app.test_client()


# ------------------------------------------------------------ the remembered list


def test_remembered_most_recent_first_folded_and_capped(home, tmp_path):
    folders = []
    for i in range(12):
        f = tmp_path / f"f{i}"
        f.mkdir()
        folders.append(f)
        app_settings.remember_folder(str(f))
    got = app_settings.load()["recent_folders"]
    assert got == [str(f) for f in reversed(folders)][: app_settings.RECENT_MAX]
    app_settings.remember_folder(str(folders[5]) + "/")  # the same folder again moves to the top
    got = app_settings.load()["recent_folders"]
    assert got[0].rstrip("/") == str(folders[5]) and len(got) == app_settings.RECENT_MAX
    assert sum(1 for p in got if app_settings.folder_key(p) == app_settings.folder_key(str(folders[5]))) == 1


def test_remembered_list_survives_restart_and_forget_touches_nothing(home, tmp_path):
    keep, drop = tmp_path / "keep", tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    (drop / "a.mp3").write_bytes(b"x")
    app_settings.remember_folder(str(drop))
    app_settings.remember_folder(str(keep))
    assert app_settings.load()["recent_folders"] == [str(keep), str(drop)]  # read back from the file
    app_settings.forget_folder(str(drop))
    assert app_settings.load()["recent_folders"] == [str(keep)]
    assert (drop / "a.mp3").read_bytes() == b"x"  # nothing on disk changed


def test_page_cannot_write_the_list(client):
    _, c = client
    r = c.post("/api/settings", headers=H, json={"recent_folders": ["/etc"]})
    assert r.status_code == 400
    assert app_settings.load()["recent_folders"] == []


def test_folders_route_marks_missing_and_users(client, ledger, tmp_path):
    here, gone = tmp_path / "here", tmp_path / "gone"
    here.mkdir()
    a = ledger.add_account("p1", "Work")
    ledger.update_account(a.id, output_dir=str(here))
    app_settings.remember_folder(str(gone))  # remembered, then the drive went away
    app_settings.remember_folder(str(here))
    _, c = client
    data = c.get("/api/folders", headers=H).get_json()
    assert data == [
        {"path": str(here), "available": True, "used_by": ["Work"]},
        {"path": str(gone), "available": False, "used_by": []},
    ]
    assert not gone.exists()  # never created


def test_choosing_a_folder_remembers_it_and_names_other_users(client, ledger, tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    work = ledger.add_account("p1", "Work")
    home_ = ledger.add_account("p2", "Home")
    _, c = client
    r = c.post(f"/api/accounts/{work.id}/output", headers=H, json={"path": str(shared)})
    assert r.get_json()["also_used_by"] == []  # nobody else yet: no notice
    r = c.post(f"/api/accounts/{home_.id}/output", headers=H, json={"path": str(shared)})
    assert r.status_code == 200 and r.get_json()["also_used_by"] == ["Work"]
    assert app_settings.load()["recent_folders"] == [str(shared.resolve())]


def test_suggested_folder_is_remembered(client, tmp_path):
    f = tmp_path / "suggested"
    f.mkdir()
    _, c = client
    r = c.post("/api/settings", headers=H, json={"default_output": str(f)})
    assert r.status_code == 200 and r.get_json()["recent_folders"] == [str(f)]


def test_forget_route(client, tmp_path):
    f = tmp_path / "f"
    f.mkdir()
    app_settings.remember_folder(str(f))
    _, c = client
    assert c.post("/api/folders/forget", headers=H, json={"path": str(f)}).get_json()["folders"] == []
    assert c.post("/api/folders/forget", headers=H, json={}).status_code == 400
    assert f.is_dir()


def test_new_account_gets_no_folder(ledger, tmp_path):
    f = tmp_path / "f"
    f.mkdir()
    app_settings.remember_folder(str(f))
    acct = ledger.add_account("p9", "New")
    assert ledger.account(acct.id).output_dir is None


# ------------------------------------------------------------ one folder, several accounts


class _Copy:
    def __init__(self, src):
        self.src = src

    def __call__(self, profile, ov, dest):
        shutil.copyfile(self.src, dest)
        return dest


@needs_ffmpeg
def test_two_accounts_one_folder_same_titles_two_files_two_rows(ledger, tmp_path, sample_m4a):
    out = tmp_path / "shared"
    out.mkdir()
    a = ledger.add_account("pa", "Work")
    b = ledger.add_account("pb", "Home")
    ov_a = AudioOverview("art-a", "Weekly Review", "nb-a", "Garden", "2026-11-12T09:00:00+00:00", "completed")
    ov_b = AudioOverview("art-b", "Weekly Review", "nb-b", "Garden", "2026-11-12T09:00:00+00:00", "completed")
    assert harvest.harvest_one(ledger, a, ov_a, out, downloader=_Copy(sample_m4a)).outcome == "saved"
    first = (out / "Garden" / "Weekly Review.mp3").read_bytes()
    assert harvest.harvest_one(ledger, b, ov_b, out, downloader=_Copy(sample_m4a)).outcome == "saved"
    files = sorted(p.name for p in (out / "Garden").iterdir())
    assert files == ["Weekly Review (2).mp3", "Weekly Review.mp3"]
    assert (out / "Garden" / "Weekly Review.mp3").read_bytes() == first  # never overwritten
    ra, rb = ledger.harvest("art-a"), ledger.harvest("art-b")
    assert (ra.account_id, rb.account_id) == (a.id, b.id)
    assert ra.saved_path != rb.saved_path
    assert media.probe(ra.saved_path).tags["comment"].endswith("artifact:art-a")
    assert media.probe(rb.saved_path).tags["comment"].endswith("artifact:art-b")


@needs_ffmpeg
def test_scan_in_a_shared_folder_never_claims_the_other_accounts_files(ledger, tmp_path, sample_m4a):
    out = tmp_path / "shared"
    out.mkdir()
    a = ledger.add_account("pa", "Work")
    b = ledger.add_account("pb", "Home")
    ov_b = AudioOverview("art-b", "Weekly Review", "nb-b", "Garden", None, "completed")
    harvest.harvest_one(ledger, b, ov_b, out, downloader=_Copy(sample_m4a))
    # Account A has an overview with the very same title, not saved yet.
    ov_a = AudioOverview("art-a", "Weekly Review", "nb-a", "Garden", None, "completed")
    r = harvest.scan_folder(ledger, a, out, {"art-a": ov_a})
    assert (r.added, r.relinked, r.already) == (0, 0, 0) and r.ignored == 1
    assert ledger.harvest("art-a") is None
    assert harvest.overview_status(ledger, ov_a) == harvest.NEW
    assert ledger.harvest("art-b").account_id == b.id


# ------------------------------------------------------------ phone


def test_phone_refused_for_folder_routes():
    for endpoint in ("remembered_folders", "forget_folder", "output", "browse", "make_folder"):
        assert endpoint not in server.PHONE_ENDPOINTS


def test_browse_names_other_accounts_and_follows_links(client, ledger, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this system does not allow folder links")
    work = ledger.add_account("p1", "Work")
    other = ledger.add_account("p2", "Home")
    ledger.update_account(work.id, output_dir=str(link))  # stored through a link
    _, c = client
    d = c.get(f"/api/browse?path={real}&account={other.id}", headers=H).get_json()
    assert d["used_by"] == ["Work"]
    d = c.get(f"/api/browse?path={real}&account={work.id}", headers=H).get_json()
    assert d["used_by"] == []  # the account itself does not count
    assert c.get(f"/api/browse?path={real}", headers=H).get_json()["used_by"] == []
