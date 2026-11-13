"""R12 machine independence, R13 portable mode, R14 Scan folder."""

import re
import shutil
from pathlib import Path

from conftest import needs_ffmpeg

from nlh import harvest, media, paths
from nlh.adapter import AudioOverview

SRC = Path(__file__).resolve().parent.parent / "nlh"


def test_no_hardcoded_user_paths_or_drive_letters():
    bad = re.compile(r"/Users/|/home/[a-z]|[A-Z]:\\\\|danet|merce\b", re.I)
    hits = [
        f"{p.name}:{n}"
        for p in SRC.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if bad.search(line) and "%LOCALAPPDATA%" not in line
    ]
    assert hits == []


def test_portable_mode_needs_flag_and_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("NLH_HOME", raising=False)
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)
    assert paths.portable_home() is None
    (tmp_path / "data").mkdir()
    assert paths.portable_home() is None  # folder alone is not enough
    (tmp_path / "portable").write_text("")
    assert paths.portable_home() == tmp_path / "data"
    assert paths.app_home() == tmp_path / "data"
    (tmp_path / "data").rmdir()
    assert paths.portable_home() is None  # flag alone is not enough


def test_nlh_home_wins_over_portable(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "portable").write_text("")
    monkeypatch.setenv("NLH_HOME", str(tmp_path / "elsewhere"))
    assert paths.app_home() == tmp_path / "elsewhere"


def _tagged(sample, dest, nb, art, title):
    tags = media.build_tags(title, "Notebook", None, nb, art)
    media.convert_to_mp3(sample, dest, tags)


@needs_ffmpeg
def test_scan_folder_tagged_untagged_foreign(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Mine")
    folder = tmp_path / "synced"
    folder.mkdir()
    _tagged(sample_m4a, folder / "Mine One.mp3", "nb-1", "art-mine-1", "Mine One")
    _tagged(sample_m4a, folder / "Mine Two.mp3", "nb-1", "art-mine-2", "Mine Two")
    _tagged(sample_m4a, folder / "Someone Else.mp3", "nb-x", "art-foreign", "Someone Else")
    media.convert_to_mp3(sample_m4a, folder / "Untagged.mp3", {"title": "no comment tag"})
    (folder / "notes.txt").write_text("not audio")
    before = {p.name: (p.stat().st_size, p.stat().st_mtime) for p in folder.iterdir()}
    known = {
        a: AudioOverview(a, t, "nb-1", "Notebook", None, "completed")
        for a, t in (("art-mine-1", "Mine One"), ("art-mine-2", "Mine Two"))
    }
    r = harvest.scan_folder(ledger, acct, folder, known)
    assert (r.added, r.relinked, r.already, r.ignored) == (2, 0, 0, 2)
    row = ledger.harvest("art-mine-1")
    assert row.saved_path == str(folder / "Mine One.mp3")
    assert row.sha256 == media.sha256(folder / "Mine One.mp3")
    assert ledger.harvest("art-foreign") is None
    # Files untouched: same names, sizes and times.
    assert {p.name: (p.stat().st_size, p.stat().st_mtime) for p in folder.iterdir()} == before
    # A second scan adds nothing; harvest then treats them as saved.
    r2 = harvest.scan_folder(ledger, acct, folder, known)
    assert (r2.added, r2.already) == (0, 2)
    assert harvest.overview_status(ledger, known["art-mine-1"]) == harvest.SAVED


@needs_ffmpeg
def test_scan_relinks_moved_file(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Mine")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _tagged(sample_m4a, old / "A.mp3", "nb-1", "art-a", "A")
    known = {"art-a": AudioOverview("art-a", "A", "nb-1", "Notebook", None, "completed")}
    harvest.scan_folder(ledger, acct, old, known)
    shutil.move(old / "A.mp3", new / "A.mp3")
    r = harvest.scan_folder(ledger, acct, new, known)
    assert r.relinked == 1 and ledger.harvest("art-a").saved_path == str(new / "A.mp3")
    assert len(ledger.harvests()) == 1


@needs_ffmpeg
def test_scan_finds_files_in_notebook_subfolders(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Mine")
    sub = tmp_path / "synced" / "Notebook"
    sub.mkdir(parents=True)
    _tagged(sample_m4a, sub / "B.mp3", "nb-1", "art-b", "B")
    known = {"art-b": AudioOverview("art-b", "B", "nb-1", "Notebook", None, "completed")}
    r = harvest.scan_folder(ledger, acct, tmp_path / "synced", known)
    assert r.added == 1 and ledger.harvest("art-b").saved_path == str(sub / "B.mp3")
