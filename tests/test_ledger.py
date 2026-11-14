"""Ledger write paths assert the stored record (R5)."""

import sqlite3

import pytest


def test_account_roundtrip(ledger):
    a = ledger.add_account("p1", "Label", "x@example.com", "Profile 9")
    ledger.update_account(a.id, output_dir="/tmp/out", last_status="ok")
    b = ledger.account(a.id)
    assert (b.profile_name, b.label, b.email, b.chrome_profile) == (
        "p1",
        "Label",
        "x@example.com",
        "Profile 9",
    )
    assert b.output_dir == "/tmp/out" and b.last_status == "ok"


def test_update_rejects_unknown_field(ledger):
    a = ledger.add_account("p1", "L")
    with pytest.raises(ValueError):
        ledger.update_account(a.id, profile_name="evil")


def _rec(ledger, acct, path="/x/a.mp3", sha="h1"):
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=acct.id,
            notebook_id="nb",
            artifact_id="art",
            overview_title="T",
            notebook_title="N",
            created_at=None,
            saved_path=path,
            size_bytes=10,
            duration_s=1.5,
            sha256=sha,
        )


def test_record_and_update_in_place(ledger):
    acct = ledger.add_account("p1", "L")
    _rec(ledger, acct)
    _rec(ledger, acct, path="/x/a (2).mp3", sha="h2")
    rows = ledger.harvests()
    assert len(rows) == 1
    assert rows[0].saved_path == "/x/a (2).mp3" and rows[0].sha256 == "h2"


def test_transaction_rolls_back(ledger):
    acct = ledger.add_account("p1", "L")
    with pytest.raises(RuntimeError):
        with ledger.transaction() as c:
            ledger.record(
                c,
                account_id=acct.id,
                notebook_id="nb",
                artifact_id="z",
                overview_title="T",
                notebook_title="N",
                created_at=None,
                saved_path="/p",
                size_bytes=1,
                duration_s=1,
                sha256="h",
            )
            raise RuntimeError
    assert ledger.harvest("z") is None


def test_schema_version(ledger):
    assert ledger.conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_artifact_id_unique(ledger):
    acct = ledger.add_account("p1", "L")
    ledger.conn.execute(
        "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title, notebook_title,"
        " saved_path, size_bytes, duration_s, sha256, saved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (acct.id, "nb", "dup", "T", "N", "/p", 1, 1.0, "h", "t"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        ledger.conn.execute(
            "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title,"
            " notebook_title, saved_path, size_bytes, duration_s, sha256, saved_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (acct.id, "nb", "dup", "T", "N", "/p", 1, 1.0, "h", "t"),
        )


def test_transactions_from_two_threads_do_not_interleave(ledger):
    """A write from another thread waits until a running transaction has finished."""
    import threading
    import time

    acct = ledger.add_account("p1", "L")
    inside, order = threading.Event(), []

    def long_transaction():
        with ledger.transaction() as c:
            inside.set()
            time.sleep(0.3)
            c.execute("UPDATE accounts SET label = ? WHERE id = ?", ("from worker", acct.id))
            order.append("worker done")

    t = threading.Thread(target=long_transaction)
    t.start()
    inside.wait(2)
    ledger.update_account(acct.id, output_dir="/x")  # page thread
    order.append("page done")
    t.join()
    assert order == ["worker done", "page done"]
    a = ledger.account(acct.id)
    assert a.label == "from worker" and a.output_dir == "/x"


def test_many_threads_read_and_write_at_once(ledger):
    """Request threads and the worker each get their own connection; nothing hangs or errors."""
    import threading

    acct = ledger.add_account("p1", "L")
    errors = []

    def writer(k):
        try:
            for i in range(20):
                with ledger.transaction() as c:
                    ledger.record(
                        c,
                        account_id=acct.id,
                        notebook_id="nb",
                        artifact_id=f"w{k}-{i}",
                        overview_title="T",
                        notebook_title="N",
                        created_at=None,
                        saved_path=f"/x/{k}-{i}.mp3",
                        size_bytes=1,
                        duration_s=1.0,
                        sha256="h",
                    )
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    def reader():
        try:
            for _ in range(40):
                ledger.harvests()
                ledger.accounts()
                ledger.history_page({"q": "t"}, "saved_desc", 1, 50)
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "a thread hung"
    assert errors == []
    assert len(ledger.harvests()) == 80
