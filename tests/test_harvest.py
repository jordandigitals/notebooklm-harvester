"""Harvest write paths: file on disk, ledger row, idempotency, failure cleanup (R4, R5)."""

import os
import shutil

from conftest import needs_ffmpeg

from nlh import harvest, media, naming, paths
from nlh.adapter import AdapterError, AudioOverview


def _ov(aid="art-1", title="How AI Works", status="completed"):
    return AudioOverview(aid, title, "nb-1", "My Notebook", "2026-11-12T09:00:00+00:00", status)


class FakeDownloader:
    """Stands in for the adapter: copies a synthetic M4A, counts calls."""

    def __init__(self, src):
        self.src, self.calls = src, 0

    def __call__(self, profile, ov, dest):
        self.calls += 1
        shutil.copyfile(self.src, dest)
        return dest


def _setup(ledger, tmp_path):
    acct = ledger.add_account("p1", "L")
    out = tmp_path / "out"
    out.mkdir()
    return acct, out


@needs_ffmpeg
def test_harvest_saves_file_and_row_then_skips(ledger, tmp_path, sample_m4a):
    acct, out = _setup(ledger, tmp_path)
    dl = FakeDownloader(sample_m4a)
    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=dl)
    assert r.outcome == "saved"
    f = out / "My Notebook" / "How AI Works.mp3"
    assert f.exists() and not list(out.rglob("*.part"))
    row = ledger.harvest("art-1")
    assert row.saved_path == str(f)
    assert row.size_bytes == f.stat().st_size
    assert row.sha256 == media.sha256(f)
    assert abs(row.duration_s - media.probe(sample_m4a).duration) <= 0.5
    assert media.probe(f).tags["comment"] == "notebooklm:nb-1 artifact:art-1"
    # Second harvest downloads nothing.
    r2 = harvest.harvest_one(ledger, acct, _ov(), out, downloader=dl)
    assert r2.outcome == "skipped" and dl.calls == 1
    assert harvest.overview_status(ledger, _ov()) == harvest.SAVED


@needs_ffmpeg
def test_download_failure_leaves_nothing(ledger, tmp_path):
    acct, out = _setup(ledger, tmp_path)

    def broken(profile, ov, dest):
        dest.write_bytes(b"partial")
        raise AdapterError("NotebookLM is limiting requests right now.")

    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=broken)
    assert r.outcome == "failed" and "limiting" in r.message
    assert list(out.iterdir()) == []
    assert ledger.harvest("art-1") is None


@needs_ffmpeg
def test_bad_audio_leaves_nothing(ledger, tmp_path):
    acct, out = _setup(ledger, tmp_path)

    def garbage(profile, ov, dest):
        dest.write_bytes(b"not audio" * 500)
        return dest

    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=garbage)
    assert r.outcome == "failed"
    assert list(out.iterdir()) == []
    assert ledger.harvest("art-1") is None


@needs_ffmpeg
def test_existing_file_never_overwritten(ledger, tmp_path, sample_m4a):
    acct, out = _setup(ledger, tmp_path)
    (out / "My Notebook").mkdir()
    keep = out / "My Notebook" / "How AI Works.mp3"
    keep.write_bytes(b"jordan's own file")
    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=FakeDownloader(sample_m4a))
    assert r.outcome == "saved"
    assert keep.read_bytes() == b"jordan's own file"
    assert (out / "My Notebook" / "How AI Works (2).mp3").exists()


@needs_ffmpeg
def test_two_overviews_same_title_both_kept(ledger, tmp_path, sample_m4a):
    acct, out = _setup(ledger, tmp_path)
    dl = FakeDownloader(sample_m4a)
    harvest.harvest_many(ledger, acct, [_ov("a1"), _ov("a2")], out, downloader=dl)
    assert sorted(p.name for p in (out / "My Notebook").iterdir()) == [
        "How AI Works (2).mp3",
        "How AI Works.mp3",
    ]
    assert len(ledger.harvests()) == 2


@needs_ffmpeg
def test_missing_file_redownload_updates_row(ledger, tmp_path, sample_m4a):
    acct, out = _setup(ledger, tmp_path)
    dl = FakeDownloader(sample_m4a)
    harvest.harvest_one(ledger, acct, _ov(), out, downloader=dl)
    first = ledger.harvest("art-1")
    # Simulate the user moving the file away (the app itself never deletes it).
    moved = tmp_path / "moved.mp3"
    shutil.move(first.saved_path, moved)
    assert harvest.overview_status(ledger, _ov()) == harvest.MISSING
    skipped = harvest.harvest_one(ledger, acct, _ov(), out, downloader=dl)
    assert skipped.outcome == "skipped" and dl.calls == 1
    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=dl, redownload=True)
    assert r.outcome == "saved" and dl.calls == 2
    row = ledger.harvest("art-1")
    assert row.file_present and len(ledger.harvests()) == 1


def test_in_progress_skipped(ledger, tmp_path):
    acct, out = _setup(ledger, tmp_path)
    r = harvest.harvest_one(
        ledger,
        acct,
        _ov(status="in_progress"),
        out,
        downloader=lambda *a: (_ for _ in ()).throw(AssertionError),
    )
    assert r.outcome == "skipped"


def test_missing_output_folder(ledger, tmp_path):
    acct = ledger.add_account("p1", "L")
    r = harvest.harvest_one(
        ledger, acct, _ov(), tmp_path / "nope", downloader=lambda *a: (_ for _ in ()).throw(AssertionError)
    )
    assert r.outcome == "failed"


@needs_ffmpeg
def test_temp_folder_cleaned(ledger, tmp_path, sample_m4a, home):
    acct, out = _setup(ledger, tmp_path)
    harvest.harvest_one(ledger, acct, _ov(), out, downloader=FakeDownloader(sample_m4a))
    assert list((home / "tmp").iterdir()) == []


@needs_ffmpeg
def test_saved_into_notebook_subfolder(ledger, tmp_path, sample_m4a):
    acct, out = _setup(ledger, tmp_path)
    ov = AudioOverview("a9", "Episode: One?", "nb-9", 'Notes/2026 "Plans"', None, "completed")
    r = harvest.harvest_one(ledger, acct, ov, out, downloader=FakeDownloader(sample_m4a))
    assert r.outcome == "saved"
    assert (out / "Notes2026 Plans" / "Episode One.mp3").exists()
    assert [p.name for p in out.iterdir()] == ["Notes2026 Plans"]


def _deep_out(tmp_path, monkeypatch, total):
    """Output folder treated as total characters long (cap shifted by the real temp path)."""
    out = tmp_path.resolve() / "out"
    out.mkdir()
    monkeypatch.setattr(naming, "WIN_MAX_PATH", 240 - total + len(str(out)))
    return out


@needs_ffmpeg
def test_windows_deep_folder_long_titles_save_under_240(ledger, tmp_path, sample_m4a, monkeypatch):
    """D-023: the file saves, its path is under 240, and the ledger row matches the file."""
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    acct = ledger.add_account("p1", "L")
    out = _deep_out(tmp_path, monkeypatch, 100)
    ov = AudioOverview("art-long", "O" * 185, "nb-1", "N" * 126, "2026-11-12T09:00:00+00:00", "completed")
    r = harvest.harvest_one(ledger, acct, ov, out, downloader=FakeDownloader(sample_m4a))
    assert r.outcome == "saved", r.message
    f = os.path.abspath(r.saved_path)
    assert len(f) < naming.WIN_MAX_PATH and os.path.exists(f)
    row = ledger.harvest("art-long")
    assert row.saved_path == r.saved_path and row.size_bytes == os.path.getsize(f)
    assert row.sha256 == media.sha256(r.saved_path)
    assert media.probe(r.saved_path).tags["title"] == "O" * 185  # tags keep the full title
    assert not list(out.rglob("*.part"))


def test_windows_too_deep_folder_refused_cleanly(ledger, tmp_path, monkeypatch):
    """D-023: plain message, no download, no folder, no partial file, no ledger row."""
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    acct = ledger.add_account("p1", "L")
    out = _deep_out(tmp_path, monkeypatch, 200)
    calls = []
    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=lambda *a: calls.append(a))
    assert r.outcome == "failed" and r.message == naming.TOO_LONG_MESSAGE
    assert calls == [] and list(out.iterdir()) == []
    assert ledger.harvest("art-1") is None


def test_place_without_hard_links_never_overwrites(tmp_path, monkeypatch):
    """Folders that cannot hard-link get an exclusive-create copy; an existing file is untouched."""

    def no_links(src, dst):
        raise OSError("hard links not supported")

    monkeypatch.setattr(harvest.os, "link", no_links)
    part, final = tmp_path / "a.mp3.part", tmp_path / "a.mp3"
    part.write_bytes(b"new audio")
    harvest._place(part, final)
    assert final.read_bytes() == b"new audio" and not part.exists()

    part.write_bytes(b"newer audio")
    final.write_bytes(b"saved before")
    original_exists = final.exists

    # The exists() check passes (a file appears right after it): the copy must still refuse.
    monkeypatch.setattr(type(final), "exists", lambda self: False if self == final else original_exists())
    try:
        harvest._place(part, final)
        raise AssertionError("an existing file was overwritten")
    except FileExistsError:
        pass
    monkeypatch.undo()
    assert final.read_bytes() == b"saved before"


@needs_ffmpeg
def test_locked_part_file_does_not_fail_a_finished_save(ledger, tmp_path, sample_m4a, monkeypatch):
    """Antivirus or a sync tool holding the .part must not lose a save that already landed."""
    acct, out = _setup(ledger, tmp_path)
    real_unlink = type(out).unlink

    def unlink(self, missing_ok=False):
        if self.name.endswith(".part"):
            raise PermissionError("in use")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(out), "unlink", unlink)
    r = harvest.harvest_one(ledger, acct, _ov(), out, downloader=FakeDownloader(sample_m4a))
    assert r.outcome == "saved", r.message
    row = ledger.harvest("art-1")
    f = out / "My Notebook" / "How AI Works.mp3"
    assert row.saved_path == str(f) and row.size_bytes == f.stat().st_size
    assert row.sha256 == media.sha256(f)
