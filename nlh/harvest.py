"""Harvest: download, convert, tag, verify, save and record one overview at a time (R4).

A failure leaves no partial file in the output folder and no ledger row.
Saved MP3s are never deleted or overwritten (ARCH-RULES 2).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nlh import adapter, media, naming, paths
from nlh.adapter import AdapterError, AudioOverview
from nlh.ledger import Account, Ledger
from nlh.log import get_logger

log = get_logger()

# One download at a time across every account (ARCH-RULES 7).
WORK_LOCK = threading.Lock()

NEW = "new"
SAVED = "saved"
MISSING = "saved_but_missing"
IN_PROGRESS = "in_progress"


@dataclass
class ItemResult:
    """Outcome of one overview in a harvest."""

    artifact_id: str
    title: str
    outcome: str  # saved | skipped | failed
    message: str
    saved_path: str | None = None


def overview_status(ledger: Ledger, ov: AudioOverview) -> str:
    """new, saved, saved_but_missing or in_progress (R3)."""
    if not ov.completed:
        return IN_PROGRESS
    row = ledger.harvest(ov.artifact_id)
    if row is None:
        return NEW
    return SAVED if row.file_present else MISSING


def _place(part: Path, final: Path) -> None:
    """Move a finished .part file to its final name without ever overwriting a file."""
    if final.exists():
        raise FileExistsError(final)
    try:
        os.link(part, final)  # fails if final exists, on every platform
        part.unlink()
    except (OSError, NotImplementedError) as exc:
        if isinstance(exc, FileExistsError):
            raise
        # Some synced folders do not support hard links; fall back to a checked rename.
        if final.exists():
            raise FileExistsError(final) from None
        os.replace(part, final)


def harvest_one(
    ledger: Ledger,
    account: Account,
    ov: AudioOverview,
    out_dir: Path,
    *,
    redownload: bool = False,
    downloader: Callable[[str, AudioOverview, Path], Path] | None = None,
    on_step: Callable[..., None] | None = None,
) -> ItemResult:
    """Harvest a single overview into out_dir. Skips anything already saved."""
    step = on_step or (lambda _msg, _pct=None: None)
    download = downloader or adapter.download_overview

    status = overview_status(ledger, ov)
    if status == IN_PROGRESS:
        return ItemResult(ov.artifact_id, ov.title, "skipped", "Not finished in NotebookLM yet.")
    if status == SAVED:
        return ItemResult(ov.artifact_id, ov.title, "skipped", "Already saved.")
    if status == MISSING and not redownload:
        return ItemResult(ov.artifact_id, ov.title, "skipped", "Saved before, but the file is missing.")

    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return ItemResult(ov.artifact_id, ov.title, "failed", "The output folder does not exist.")

    work = Path(tempfile.mkdtemp(prefix="item-", dir=paths.temp_dir()))
    part: Path | None = None
    final: Path | None = None
    placed = False
    try:
        step("Downloading", None)
        stop = threading.Event()

        def watch():
            # The library streams into a temp file next to the target; report its size.
            while not stop.wait(0.7):
                size = sum(f.stat().st_size for f in work.glob("source.bin*") if f.is_file())
                if size:
                    step(f"Downloading {size / 1e6:.1f} MB", None)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            src = download(account.profile_name, ov, work / "source.bin")
        finally:
            stop.set()
            watcher.join(timeout=2)
        step("Checking the audio", 0.3)
        source = media.probe(src)
        log.info(
            "artifact %s source %s/%s %.2fs %dch",
            ov.artifact_id,
            source.format_name,
            source.codec,
            source.duration,
            source.channels,
        )
        tags = media.build_tags(ov.title, ov.notebook_title, ov.created_at, ov.notebook_id, ov.artifact_id)
        step("Converting to MP3 0%", 0.3)
        mp3 = work / "out.mp3"
        media.convert_to_mp3(
            src,
            mp3,
            tags,
            duration=source.duration,
            on_progress=lambda f: step(f"Converting to MP3 {int(f * 100)}%", 0.3 + 0.65 * f),
        )
        checked = media.verify_mp3(mp3, source.duration, tags)
        digest = media.sha256(mp3)

        step("Saving", 0.97)
        final = naming.unique_path(out_dir, ov.title)
        part = final.with_name(final.name + ".part")
        shutil.copyfile(mp3, part)
        if media.sha256(part) != digest:
            raise media.MediaError("The saved copy does not match the converted file.")
        with ledger.transaction() as c:
            ledger.record(
                c,
                account_id=account.id,
                notebook_id=ov.notebook_id,
                artifact_id=ov.artifact_id,
                overview_title=ov.title,
                notebook_title=ov.notebook_title,
                created_at=ov.created_at,
                saved_path=str(final),
                size_bytes=part.stat().st_size,
                duration_s=checked.duration,
                sha256=digest,
            )
            _place(part, final)
            placed = True
        part = None
        log.info("saved artifact %s to %s", ov.artifact_id, final)
        return ItemResult(ov.artifact_id, ov.title, "saved", "Saved.", str(final))
    except AdapterError as exc:
        return ItemResult(ov.artifact_id, ov.title, "failed", exc.message)
    except media.MediaError as exc:
        log.warning("artifact %s media error: %s", ov.artifact_id, exc)
        return ItemResult(ov.artifact_id, ov.title, "failed", str(exc))
    except sqlite3.Error as exc:
        log.warning("artifact %s ledger error: %s", ov.artifact_id, exc)
        if placed and final is not None and final.exists():
            final.unlink()  # created this run and not recorded; never a prior saved file
        return ItemResult(ov.artifact_id, ov.title, "failed", "Could not record the save.")
    except FileExistsError:
        return ItemResult(
            ov.artifact_id, ov.title, "failed", "A file with that name appeared; nothing was changed."
        )
    except OSError as exc:
        log.warning("artifact %s file error: %s", ov.artifact_id, exc)
        return ItemResult(ov.artifact_id, ov.title, "failed", "Could not write to the output folder.")
    finally:
        if part is not None and part.exists():
            part.unlink()  # our own unfinished .part, never a saved MP3
        shutil.rmtree(work, ignore_errors=True)


def harvest_many(
    ledger: Ledger,
    account: Account,
    overviews: list[AudioOverview],
    out_dir: Path,
    *,
    redownload_ids: set[str] | None = None,
    downloader=None,
    on_item: Callable[..., None] | None = None,
) -> list[ItemResult]:
    """Harvest several overviews one at a time, holding the global work lock."""
    results = []
    with WORK_LOCK:
        for i, ov in enumerate(overviews):

            def step(msg, pct=None, i=i, ov=ov):
                if on_item:
                    on_item(i, len(overviews), ov, msg, pct)

            res = harvest_one(
                ledger,
                account,
                ov,
                out_dir,
                redownload=ov.artifact_id in (redownload_ids or set()),
                downloader=downloader,
                on_step=step,
            )
            step(res.message, 1.0)
            results.append(res)
    return results


_TAG = re.compile(r"notebooklm:(\S+)\s+artifact:(\S+)")


@dataclass
class ScanResult:
    """What a folder scan found."""

    added: int = 0
    relinked: int = 0
    already: int = 0
    ignored: int = 0


def scan_folder(
    ledger: Ledger,
    account: Account,
    folder: Path,
    known: dict[str, AudioOverview],
    on_step: Callable[[str], None] | None = None,
) -> ScanResult:
    """Rebuild ledger rows from MP3 tags in an output folder (R14).

    Only reads files: it never renames, retags or deletes them. A file counts when its
    comment tag names an artifact id that belongs to this account (known holds the
    account's overviews by artifact id). New ids get a ledger row; a row whose file
    went missing is pointed at the found file. Everything else is ignored.
    """
    result = ScanResult()
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() == ".mp3")
    for i, path in enumerate(files):
        if on_step:
            on_step(f"Checking file {i + 1} of {len(files)}")
        try:
            info = media.probe(path)
        except media.MediaError:
            result.ignored += 1
            continue
        match = _TAG.search(info.tags.get("comment", ""))
        if not match or match.group(2) not in known:
            result.ignored += 1
            continue
        ov = known[match.group(2)]
        row = ledger.harvest(ov.artifact_id)
        if row and row.file_present:
            result.already += 1
            continue
        with ledger.transaction() as c:
            ledger.record(
                c,
                account_id=account.id,
                notebook_id=ov.notebook_id,
                artifact_id=ov.artifact_id,
                overview_title=ov.title,
                notebook_title=ov.notebook_title,
                created_at=ov.created_at,
                saved_path=str(path),
                size_bytes=path.stat().st_size,
                duration_s=info.duration,
                sha256=media.sha256(path),
            )
        if row:
            result.relinked += 1
        else:
            result.added += 1
        log.info("scan: recorded artifact %s from %s", ov.artifact_id, path.name)
    return result
