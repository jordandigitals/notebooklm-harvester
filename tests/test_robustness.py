"""U0 robustness: app lock, unreachable folders, trash for failed sign-ins, suffix room."""

import subprocess
import sys

import pytest

from nlh import accounts, adapter, harvest, naming, paths
from nlh.adapter import AudioOverview, SessionStatus


def test_app_lock_is_exclusive_across_processes(home):
    lock = paths.AppLock()
    assert lock.acquire()
    code = "import sys; from nlh import paths; sys.exit(0 if paths.AppLock().acquire() else 3)"
    other = subprocess.run([sys.executable, "-c", code], env={**__import__("os").environ}, cwd=".")
    assert other.returncode == 3  # a second process cannot take it
    lock.release()
    other = subprocess.run([sys.executable, "-c", code], env={**__import__("os").environ}, cwd=".")
    assert other.returncode == 0


def _ov(aid="a1"):
    return AudioOverview(aid, "Title", "nb", "Notebook", None, "completed")


def _record(ledger, acct, path):
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=acct.id,
            notebook_id="nb",
            artifact_id="a1",
            overview_title="Title",
            notebook_title="Notebook",
            created_at=None,
            saved_path=str(path),
            size_bytes=1,
            duration_s=1.0,
            sha256="h",
        )


def test_missing_file_with_folder_present_is_missing(ledger, tmp_path):
    out = tmp_path / "out"
    (out / "Notebook").mkdir(parents=True)
    acct = ledger.add_account("p", "L")
    ledger.update_account(acct.id, output_dir=str(out))
    _record(ledger, acct, out / "Notebook" / "Title.mp3")
    assert harvest.overview_status(ledger, _ov()) == harvest.MISSING


def test_subfolder_removed_but_output_present_is_missing(ledger, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    acct = ledger.add_account("p", "L")
    ledger.update_account(acct.id, output_dir=str(out))
    _record(ledger, acct, out / "Notebook" / "Title.mp3")
    assert harvest.overview_status(ledger, _ov()) == harvest.MISSING


def test_unplugged_output_folder_is_unreachable_and_never_redownloaded(ledger, tmp_path):
    gone = tmp_path / "unplugged-drive" / "Audio"
    acct = ledger.add_account("p", "L")
    ledger.update_account(acct.id, output_dir=str(gone))
    _record(ledger, acct, gone / "Notebook" / "Title.mp3")
    assert harvest.overview_status(ledger, _ov()) == harvest.UNREACHABLE
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    calls = []
    r = harvest.harvest_one(
        ledger,
        ledger.account(acct.id),
        _ov(),
        elsewhere,
        redownload=True,
        downloader=lambda *a: calls.append(1),
    )
    assert r.outcome == "skipped" and "not reachable" in r.message and calls == []
    assert ledger.harvest("a1").saved_path == str(gone / "Notebook" / "Title.mp3")  # not repointed
    assert list(elsewhere.iterdir()) == []


def test_file_on_missing_volume_is_unreachable(ledger, monkeypatch, tmp_path):
    acct = ledger.add_account("p", "L")
    root = tmp_path / "Volumes"
    root.mkdir()
    monkeypatch.setattr(harvest.os.path, "ismount", lambda p: str(p) == str(root))
    _record(ledger, acct, root / "Offline" / "Old" / "Title.mp3")
    assert harvest.overview_status(ledger, _ov()) == harvest.UNREACHABLE


@pytest.fixture
def fake_login(monkeypatch, home):
    def write(name):
        d = paths.profile_dir(name)
        (d / "browser_profile").mkdir(parents=True, exist_ok=True)
        (d / "browser_profile" / "Cookies").write_text("fixture")

    state = {"ok": False, "email": "dup@example.com"}

    def login_interactive(name, timeout_s=300):
        write(name)
        return SessionStatus("ok" if state["ok"] else "needs_sign_in", "x", state["email"])

    monkeypatch.setattr(adapter, "login_interactive", login_interactive)
    return state


def test_failed_signin_profile_goes_to_trash(ledger, fake_login):
    with pytest.raises(accounts.AccountError):
        accounts.add_interactive(ledger)
    assert not any(paths.profile_dir("acct-account").parent.glob("acct-account*"))
    entries = accounts.trash_entries()
    assert len(entries) == 1 and entries[0]["name"].startswith("acct-account-")
    assert (paths.trash_dir() / entries[0]["name"] / "browser_profile" / "Cookies").exists()


def test_refused_duplicate_goes_to_trash_and_clear_trash(ledger, fake_login):
    ledger.add_account("acct-x", "Existing", "dup@example.com")
    fake_login["ok"] = True
    with pytest.raises(accounts.AccountError):
        accounts.add_interactive(ledger)
    assert len(accounts.trash_entries()) == 1
    assert accounts.clear_trash() == 1
    assert accounts.trash_entries() == []


def test_trash_api_needs_confirm(ledger, fake_login):
    from nlh import server

    app = server.create_app("k", ledger)
    c = app.test_client()
    h = {"X-NLH-Token": "k"}
    with pytest.raises(accounts.AccountError):
        accounts.add_interactive(ledger)
    assert len(c.get("/api/trash", headers=h).get_json()) == 1
    assert c.post("/api/trash/clear", json={}, headers=h).status_code == 400
    assert c.post("/api/trash/clear", json={"confirm": True}, headers=h).get_json()["removed"] == 1


def test_suffix_room_fits_999(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    folder = tmp_path / ("d" * 60)
    folder.mkdir()
    stem_room = naming.stem_room(folder)
    path_len = len(str(folder.resolve())) + 1 + stem_room + len(" (999)") + len(".mp3")
    assert naming.SUFFIX_ROOM == len(" (999)")
    assert path_len <= naming.WIN_MAX_PATH - 1 or stem_room < naming.WIN_MIN_STEM


def test_second_start_opens_running_page_and_exits(home, monkeypatch):
    from nlh import server

    opened = []
    monkeypatch.setattr(server.paths.AppLock, "acquire", lambda self: False)
    monkeypatch.setattr(server, "_running_url", lambda: "http://127.0.0.1:1/?t=x")
    monkeypatch.setattr(server.webbrowser, "open", opened.append)
    monkeypatch.setattr(server, "create_app", lambda *a, **k: pytest.fail("must not start a server"))
    assert server.serve() == 0
    assert opened == ["http://127.0.0.1:1/?t=x"]
