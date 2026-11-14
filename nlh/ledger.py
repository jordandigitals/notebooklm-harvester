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

SCHEMA_VERSION = 3

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
CREATE INDEX IF NOT EXISTS harvests_saved_at ON harvests(saved_at);
CREATE TABLE IF NOT EXISTS attempts (
  id             INTEGER PRIMARY KEY,
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  artifact_id    TEXT NOT NULL,
  started_at     TEXT NOT NULL,
  outcome        TEXT NOT NULL,
  message        TEXT,
  download_s     REAL,
  total_s        REAL
);
CREATE INDEX IF NOT EXISTS attempts_artifact ON attempts(artifact_id, started_at);
CREATE TABLE IF NOT EXISTS library_cache (
  account_id     INTEGER PRIMARY KEY REFERENCES accounts(id),
  data_json      TEXT NOT NULL,
  listed_at      TEXT NOT NULL
);
"""


def fold(text: str | None) -> str:
    """Lower case with accents removed, for case- and accent-insensitive search."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


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
    origin: str = "harvest"  # harvest | scan | relinked (schema v3)

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
        self._migrate()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()
        # fold(): lower case without accents, for search that ignores both.
        self.conn.create_function("fold", 1, fold, deterministic=True)

    def _migrate(self) -> None:
        """Additive upgrades only: never rewrites or deletes rows."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(harvests)")}
        if "origin" not in cols:  # v2 -> v3
            self.conn.execute("ALTER TABLE harvests ADD COLUMN origin TEXT NOT NULL DEFAULT 'harvest'")

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
        origin: str = "harvest",
    ) -> None:
        """Insert a row, or update the existing row for a re-download. Call inside transaction()."""
        c.execute(
            "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title, "
            "notebook_title, created_at, saved_path, size_bytes, duration_s, sha256, saved_at, origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(artifact_id) DO UPDATE SET saved_path = excluded.saved_path, "
            "size_bytes = excluded.size_bytes, duration_s = excluded.duration_s, "
            "sha256 = excluded.sha256, saved_at = excluded.saved_at, origin = excluded.origin, "
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
                origin,
            ),
        )

    def harvest_by_id(self, harvest_id: int) -> Harvest | None:
        """One ledger row by its own id (used by the player route; no paths from the page)."""
        r = self.conn.execute("SELECT * FROM harvests WHERE id = ?", (harvest_id,)).fetchone()
        return Harvest(**dict(r)) if r else None

    # ------------------------------------------------------------ attempts

    def record_attempt(
        self,
        account_id: int,
        artifact_id: str,
        outcome: str,
        message: str = "",
        download_s: float | None = None,
        total_s: float | None = None,
    ) -> None:
        """Log one harvest attempt (saved, failed, cancelled) for status and statistics."""
        with self.transaction() as c:
            c.execute(
                "INSERT INTO attempts (account_id, artifact_id, started_at, outcome, message, "
                "download_s, total_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (account_id, artifact_id, now(), outcome, message[:300], download_s, total_s),
            )

    def last_attempts(self) -> dict[str, dict]:
        """Latest attempt per artifact id: outcome, message, when."""
        rows = self.conn.execute(
            "SELECT a.artifact_id, a.outcome, a.message, a.started_at FROM attempts a "
            "JOIN (SELECT artifact_id, MAX(id) AS mid FROM attempts GROUP BY artifact_id) m "
            "ON a.id = m.mid"
        ).fetchall()
        return {r["artifact_id"]: dict(r) for r in rows}

    # ------------------------------------------------------------ history query (paged)

    HISTORY_SORTS = {
        "saved_desc": "h.saved_at DESC, h.id DESC",
        "saved_asc": "h.saved_at ASC, h.id ASC",
        "title_asc": "fold(h.overview_title) ASC, h.id ASC",
        "title_desc": "fold(h.overview_title) DESC, h.id DESC",
        "notebook_asc": "fold(h.notebook_title) ASC, h.saved_at DESC",
        "size_desc": "h.size_bytes DESC, h.id DESC",
        "size_asc": "h.size_bytes ASC, h.id ASC",
        "length_desc": "h.duration_s DESC, h.id DESC",
        "length_asc": "h.duration_s ASC, h.id ASC",
    }

    def _history_where(self, f: dict) -> tuple[str, list]:
        """WHERE clause for history filters; every value is a bound parameter."""
        where, args = ["1=1"], []
        if f.get("accounts"):
            ids = [int(a) for a in f["accounts"]]
            where.append(f"h.account_id IN ({','.join('?' * len(ids))})")
            args += ids
        if f.get("notebooks"):
            names = [str(n) for n in f["notebooks"]]
            where.append(f"h.notebook_title IN ({','.join('?' * len(names))})")
            args += names
        for key, col, op in (
            ("saved_from", "substr(h.saved_at, 1, 10)", ">="),
            ("saved_to", "substr(h.saved_at, 1, 10)", "<="),
            ("size_min", "h.size_bytes", ">="),
            ("size_max", "h.size_bytes", "<="),
            ("length_min", "h.duration_s", ">="),
            ("length_max", "h.duration_s", "<="),
        ):
            if f.get(key) not in (None, ""):
                where.append(f"{col} {op} ?")
                args.append(f[key])
        for word in fold(str(f.get("q") or "")).split():
            where.append(
                "fold(h.overview_title || ' ' || h.notebook_title || ' ' || h.saved_path || ' ' "
                "|| COALESCE(a.label, '') || ' ' || COALESCE(a.email, '')) LIKE ? ESCAPE '\\'"
            )
            args.append("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return " AND ".join(where), args

    def history_page(self, f: dict, sort: str = "saved_desc", page: int = 1, size: int = 50):
        """One page of history rows plus the total count (LIMIT and OFFSET over indexed columns)."""
        order = self.HISTORY_SORTS.get(sort, self.HISTORY_SORTS["saved_desc"])
        where, args = self._history_where(f)
        base = f"FROM harvests h LEFT JOIN accounts a ON a.id = h.account_id WHERE {where}"
        total = self.conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        size = max(1, min(int(size), 250))
        page = max(1, int(page))
        rows = self.conn.execute(
            f"SELECT h.* {base} ORDER BY {order} LIMIT ? OFFSET ?", [*args, size, (page - 1) * size]
        ).fetchall()
        return [Harvest(**dict(r)) for r in rows], total

    def history_all(self, f: dict, sort: str = "saved_desc") -> list[Harvest]:
        """Every row matching the filters (export and statistics use the whole set)."""
        order = self.HISTORY_SORTS.get(sort, self.HISTORY_SORTS["saved_desc"])
        where, args = self._history_where(f)
        rows = self.conn.execute(
            f"SELECT h.* FROM harvests h LEFT JOIN accounts a ON a.id = h.account_id WHERE {where} "
            f"ORDER BY {order}",
            args,
        ).fetchall()
        return [Harvest(**dict(r)) for r in rows]

    def attempt_stats(self, account_ids: list[int] | None = None) -> dict:
        """Success rate and average download time from the attempts log."""
        where, args = "1=1", []
        if account_ids:
            where = f"account_id IN ({','.join('?' * len(account_ids))})"
            args = list(account_ids)
        r = self.conn.execute(
            f"SELECT SUM(outcome = 'saved') AS saved, SUM(outcome = 'failed') AS failed, "
            f"AVG(CASE WHEN outcome = 'saved' THEN download_s END) AS avg_dl FROM attempts WHERE {where}",
            args,
        ).fetchone()
        return {"saved": r["saved"] or 0, "failed": r["failed"] or 0, "avg_download_s": r["avg_dl"]}

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
