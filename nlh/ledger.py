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

SCHEMA_VERSION = 4

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

# Schema v4 (Studio, D-025). Plans keep only what Jordan approved (never the raw chat text);
# the queue keeps one row per overview to generate. Rows are never deleted.
STUDIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_plans (
  id             INTEGER PRIMARY KEY,
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  notebook_id    TEXT NOT NULL,
  notebook_title TEXT NOT NULL,
  items_json     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  archived       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio_queue (
  id             INTEGER PRIMARY KEY,
  plan_id        INTEGER REFERENCES studio_plans(id),
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  notebook_id    TEXT NOT NULL,
  notebook_title TEXT NOT NULL,
  title          TEXT NOT NULL,
  focus          TEXT NOT NULL,
  audio_format   TEXT NOT NULL,
  audio_length   TEXT NOT NULL,
  language       TEXT NOT NULL,
  state          TEXT NOT NULL,
  task_id        TEXT,
  queued_at      TEXT NOT NULL,
  submitted_at   TEXT,
  submit_day     TEXT,
  finished_at    TEXT,
  harvest_id     INTEGER REFERENCES harvests(id),
  message        TEXT
);
CREATE TABLE IF NOT EXISTS studio_limits (
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  day            TEXT NOT NULL,
  noted_at       TEXT NOT NULL,
  PRIMARY KEY (account_id, day)
);
CREATE INDEX IF NOT EXISTS studio_queue_state ON studio_queue(state);
CREATE INDEX IF NOT EXISTS studio_queue_day ON studio_queue(account_id, submit_day);
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
    origin: str = "harvest"  # harvest | scan | relinked (schema v3) | studio (v4)

    @property
    def file_present(self) -> bool:
        """True if the saved MP3 still exists on disk."""
        return Path(self.saved_path).exists()


class Ledger:
    """Thin wrapper over the SQLite file."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.ledger_path()
        # One SQLite connection per thread: the page's request threads and the worker never
        # share a connection (sharing one could hang under many simultaneous requests).
        # Writes still go through one lock, and SQLite's own file lock (with a busy timeout)
        # keeps connections from stepping on each other.
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self.lock = threading.RLock()
        c = self.conn
        c.execute("PRAGMA journal_mode = WAL")  # readers do not wait for a writer
        c.executescript(SCHEMA)
        self._migrate()
        c.executescript(STUDIO_SCHEMA)  # v3 -> v4: new tables only, existing rows untouched
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        c.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA busy_timeout = 30000")
            # fold(): lower case without accents, for search that ignores both.
            c.create_function("fold", 1, fold, deterministic=True)
            self._local.conn = c
            with self._all_lock:
                self._all.append(c)
        return c

    def _migrate(self) -> None:
        """Additive upgrades only: never rewrites or deletes rows."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(harvests)")}
        if "origin" not in cols:  # v2 -> v3
            self.conn.execute("ALTER TABLE harvests ADD COLUMN origin TEXT NOT NULL DEFAULT 'harvest'")

    def close(self) -> None:
        """Close every connection this ledger opened."""
        with self._all_lock:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

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

    # ------------------------------------------------------------ Studio (D-025)

    def add_plan(self, account_id: int, notebook_id: str, notebook_title: str, items_json: str) -> int:
        """Store a plan Jordan approved (items only, never the raw chat answer)."""
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO studio_plans (account_id, notebook_id, notebook_title, items_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, notebook_id, notebook_title, items_json, now()),
            )
            return int(cur.lastrowid)

    def plans(self, account_id: int | None = None, include_archived: bool = False) -> list[dict]:
        """Approved plans, newest first."""
        sql = "SELECT * FROM studio_plans WHERE 1 = 1"
        args: list = []
        if account_id is not None:
            sql += " AND account_id = ?"
            args.append(account_id)
        if not include_archived:
            sql += " AND archived = 0"
        rows = self.conn.execute(sql + " ORDER BY id DESC", args).fetchall()
        return [dict(r) for r in rows]

    def plan(self, plan_id: int) -> dict | None:
        """One plan by id."""
        r = self.conn.execute("SELECT * FROM studio_plans WHERE id = ?", (plan_id,)).fetchone()
        return dict(r) if r else None

    def archive_plan(self, plan_id: int) -> None:
        """Hide a plan from the list (the row stays)."""
        with self.transaction() as c:
            c.execute("UPDATE studio_plans SET archived = 1 WHERE id = ?", (plan_id,))

    def submitted_on(self, account_id: int, day: str) -> int:
        """Generations submitted for an account on a local day (the cap counts on submit)."""
        r = self.conn.execute(
            "SELECT COUNT(*) FROM studio_queue WHERE account_id = ? AND submit_day = ?", (account_id, day)
        ).fetchone()
        return int(r[0])

    # queue rows ---------------------------------------------------------------

    QUEUE_FIELDS = ("state", "task_id", "submitted_at", "submit_day", "finished_at", "harvest_id", "message")

    def add_queue_items(self, rows: list[dict]) -> list[int]:
        """Queue overviews to generate (state "queued"; nothing is sent yet)."""
        ids = []
        with self.transaction() as c:
            for r in rows:
                cur = c.execute(
                    "INSERT INTO studio_queue (plan_id, account_id, notebook_id, notebook_title, title,"
                    " focus, audio_format, audio_length, language, state, queued_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r.get("plan_id"),
                        r["account_id"],
                        r["notebook_id"],
                        r["notebook_title"],
                        r["title"],
                        r["focus"],
                        r["audio_format"],
                        r["audio_length"],
                        r["language"],
                        "queued",
                        now(),
                    ),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def queue_items(self, states: tuple[str, ...] | None = None, limit: int = 200) -> list[dict]:
        """Queue rows, oldest first, optionally only some states."""
        sql = "SELECT * FROM studio_queue"
        args: list = []
        if states:
            sql += f" WHERE state IN ({','.join('?' * len(states))})"
            args += list(states)
        rows = self.conn.execute(sql + " ORDER BY id LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_queue(self, limit: int = 100) -> list[dict]:
        """Unfinished rows plus the most recent finished ones, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM (SELECT * FROM studio_queue ORDER BY"
            " (state IN ('done', 'failed', 'cancelled')), id DESC LIMIT ?) ORDER BY id",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def queue_item(self, item_id: int) -> dict | None:
        """One queue row."""
        r = self.conn.execute("SELECT * FROM studio_queue WHERE id = ?", (item_id,)).fetchone()
        return dict(r) if r else None

    def update_queue(self, item_id: int, **fields) -> None:
        """Change a queue row's state and its facts (never deletes)."""
        bad = set(fields) - set(self.QUEUE_FIELDS)
        if bad:
            raise ValueError(f"cannot update {bad}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction() as c:
            c.execute(f"UPDATE studio_queue SET {cols} WHERE id = ?", (*fields.values(), item_id))

    def mark_submitting(self, item_id: int, day: str) -> bool:
        """Count the item against the cap BEFORE the request goes out (a crash cannot undercount).
        Returns False if the row was no longer queued."""
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE studio_queue SET state = 'submitting', submitted_at = ?, submit_day = ?"
                " WHERE id = ? AND state = 'queued'",
                (now(), day, item_id),
            )
            return cur.rowcount == 1

    def note_limit(self, account_id: int, day: str) -> None:
        """Remember that Google refused for a usage limit: no more requests that day."""
        with self.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO studio_limits (account_id, day, noted_at) VALUES (?, ?, ?)",
                (account_id, day, now()),
            )

    def limit_hit(self, account_id: int, day: str) -> bool:
        """True if Google refused this account for a limit on that day."""
        r = self.conn.execute(
            "SELECT 1 FROM studio_limits WHERE account_id = ? AND day = ?", (account_id, day)
        ).fetchone()
        return r is not None

    def recover_after_restart(self) -> int:
        """At start-up: a row left "submitting" may or may not have reached Google. It becomes
        "needs_review" so Jordan checks it; nothing is sent again on its own."""
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE studio_queue SET state = 'needs_review', message = ? WHERE state = 'submitting'",
                ("The app stopped while sending this request. Check NotebookLM before sending it again.",),
            )
            return cur.rowcount
