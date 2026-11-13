"""SQLite ledger: accounts and saved overviews, keyed by artifact id (ARCH-RULES 5, R5).

The ledger never deletes rows. A re-download of a "saved but file missing" overview
updates that row in place with the new file's details.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nlh import paths

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id             INTEGER PRIMARY KEY,
  profile_name   TEXT NOT NULL UNIQUE,
  email          TEXT,
  label          TEXT NOT NULL,
  chrome_profile TEXT,
  output_dir     TEXT,
  last_status    TEXT,
  last_checked   TEXT,
  created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harvests (
  id             INTEGER PRIMARY KEY,
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  notebook_id    TEXT NOT NULL,
  artifact_id    TEXT NOT NULL UNIQUE,
  overview_title TEXT NOT NULL,
  notebook_title TEXT NOT NULL,
  created_at     TEXT,
  saved_path     TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL,
  duration_s     REAL NOT NULL,
  sha256         TEXT NOT NULL,
  saved_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS harvests_account ON harvests(account_id);
CREATE TABLE IF NOT EXISTS library_cache (
  account_id     INTEGER PRIMARY KEY REFERENCES accounts(id),
  data_json      TEXT NOT NULL,
  listed_at      TEXT NOT NULL
);
"""


def now() -> str:
    """Current UTC time as ISO text."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Account:
    """An account row."""

    id: int
    profile_name: str
    email: str | None
    label: str
    chrome_profile: str | None
    output_dir: str | None
    last_status: str | None
    last_checked: str | None
    created_at: str


@dataclass(frozen=True)
class Harvest:
    """A saved-overview row."""

    id: int
    account_id: int
    notebook_id: str
    artifact_id: str
    overview_title: str
    notebook_title: str
    created_at: str | None
    saved_path: str
    size_bytes: int
    duration_s: float
    sha256: str
    saved_at: str

    @property
    def file_present(self) -> bool:
        """True if the saved MP3 still exists on disk."""
        return Path(self.saved_path).exists()


class Ledger:
    """Thin wrapper over the SQLite file."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.ledger_path()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        # The connection is shared by the page's request threads and the worker; one lock
        # keeps another thread's commit from landing inside a harvest's transaction.
        self.lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements in one transaction (one thread at a time); roll back on any error."""
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ------------------------------------------------------------ accounts

    def add_account(
        self, profile_name: str, label: str, email: str | None = None, chrome_profile: str | None = None
    ) -> Account:
        """Register an account (its session lives in the library profile folder)."""
        with self.transaction() as c:
            c.execute(
                "INSERT INTO accounts (profile_name, email, label, chrome_profile, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_name, email, label, chrome_profile, now()),
            )
        return self.account_by_profile(profile_name)

    def accounts(self) -> list[Account]:
        """All accounts, oldest first."""
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [Account(**dict(r)) for r in rows]

    def account(self, account_id: int) -> Account | None:
        """One account by id."""
        r = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return Account(**dict(r)) if r else None

    def account_by_profile(self, profile_name: str) -> Account | None:
        """One account by library profile name."""
        r = self.conn.execute("SELECT * FROM accounts WHERE profile_name = ?", (profile_name,)).fetchone()
        return Account(**dict(r)) if r else None

    def update_account(self, account_id: int, **fields) -> None:
        """Update label, email, output_dir, last_status or last_checked."""
        allowed = {"label", "email", "output_dir", "last_status", "last_checked", "chrome_profile"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as c:
            c.execute(f"UPDATE accounts SET {cols} WHERE id = ?", (*fields.values(), account_id))

    # ------------------------------------------------------------ harvests

    def harvest(self, artifact_id: str) -> Harvest | None:
        """The ledger row for an artifact id, if any."""
        r = self.conn.execute("SELECT * FROM harvests WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return Harvest(**dict(r)) if r else None

    def harvests(self, account_id: int | None = None) -> list[Harvest]:
        """Saved rows, newest first, optionally for one account."""
        if account_id is None:
            rows = self.conn.execute("SELECT * FROM harvests ORDER BY saved_at DESC, id DESC")
        else:
            rows = self.conn.execute(
                "SELECT * FROM harvests WHERE account_id = ? ORDER BY saved_at DESC, id DESC",
                (account_id,),
            )
        return [Harvest(**dict(r)) for r in rows.fetchall()]

    def record(
        self,
        c: sqlite3.Connection,
        *,
        account_id: int,
        notebook_id: str,
        artifact_id: str,
        overview_title: str,
        notebook_title: str,
        created_at: str | None,
        saved_path: str,
        size_bytes: int,
        duration_s: float,
        sha256: str,
    ) -> None:
        """Insert a row, or update the existing row for a re-download. Call inside transaction()."""
        c.execute(
            "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title, "
            "notebook_title, created_at, saved_path, size_bytes, duration_s, sha256, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(artifact_id) DO UPDATE SET saved_path = excluded.saved_path, "
            "size_bytes = excluded.size_bytes, duration_s = excluded.duration_s, "
            "sha256 = excluded.sha256, saved_at = excluded.saved_at, "
            "overview_title = excluded.overview_title, notebook_title = excluded.notebook_title",
            (
                account_id,
                notebook_id,
                artifact_id,
                overview_title,
                notebook_title,
                created_at,
                saved_path,
                size_bytes,
                duration_s,
                sha256,
                now(),
            ),
        )

    # ------------------------------------------------------------ listing cache

    def save_listing(self, account_id: int, data_json: str) -> None:
        """Keep the last notebook listing (titles and ids only) for instant display."""
        with self.transaction() as c:
            c.execute(
                "INSERT INTO library_cache (account_id, data_json, listed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET data_json = excluded.data_json, "
                "listed_at = excluded.listed_at",
                (account_id, data_json, now()),
            )

    def load_listing(self, account_id: int) -> tuple[str, str] | None:
        """The cached listing JSON and when it was read, if any."""
        r = self.conn.execute(
            "SELECT data_json, listed_at FROM library_cache WHERE account_id = ?", (account_id,)
        ).fetchone()
        return (r["data_json"], r["listed_at"]) if r else None
