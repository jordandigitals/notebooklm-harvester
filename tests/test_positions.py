"""Resume playback (P2-J): ledger v5 migration, stored positions, staleness, the API and the phone.

Every check reads the stored row, not only an answer. The migration runs on a real-shaped v4
ledger built in the test home, never on a real app home.
"""

import hashlib
import json
import sqlite3
import time

import pytest

from nlh import ledger as ledger_mod
from nlh import paths, phone, server

TOKEN = "t" * 32
LAN = ("192.168.1.20", 18765)
HOST = f"{LAN[0]}:{LAN[1]}"
DATA_TABLES = (
    "accounts",
    "harvests",
    "attempts",
    "library_cache",
    "studio_plans",
    "studio_queue",
    "studio_limits",
)


# ---------------------------------------------------------------- a real-shaped v4 ledger


def v4_statements(c: sqlite3.Connection) -> None:
    """Exactly what the v4 app (0.4.10 and earlier) runs when it opens a ledger."""
    c.executescript(ledger_mod.SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(harvests)")}
    if "origin" not in cols:
        c.execute("ALTER TABLE harvests ADD COLUMN origin TEXT NOT NULL DEFAULT 'harvest'")
    c.executescript(ledger_mod.STUDIO_SCHEMA)
    c.execute("PRAGMA user_version = 4")
    c.commit()


def make_v4(path, out_dir):
    """Two accounts, a saved file of every origin, attempts, a cached listing and Studio rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode = WAL")
    v4_statements(c)
    c.execute(
        "INSERT INTO accounts (profile_name, email, label, chrome_profile, output_dir, last_status,"
        " created_at)"
        " VALUES ('p1', 'a@example.com', 'Work', 'Profile 1', ?, 'ok', '2026-11-01T10:00:00+00:00')",
        (str(out_dir),),
    )
    c.execute(
        "INSERT INTO accounts (profile_name, label, created_at)"
        " VALUES ('p2', 'Home', '2026-11-02T10:00:00+00:00')"
    )
    for i, origin in enumerate(("harvest", "scan", "relinked", "studio"), start=1):
        mp3 = out_dir / f"file{i}.mp3"
        mp3.write_bytes(bytes([i]) * (1000 + i))
        c.execute(
            "INSERT INTO harvests (account_id, notebook_id, artifact_id, overview_title, notebook_title,"
            " created_at, saved_path, size_bytes, duration_s, sha256, saved_at, origin)"
            " VALUES (1, 'nb', ?, ?, 'Garden', '2026-11-03', ?, ?, 2400.0, ?,"
            " '2026-11-04T10:00:00+00:00', ?)",
            (f"art{i}", f"Title {i}", str(mp3), mp3.stat().st_size, f"sha{i}", origin),
        )
        c.execute(
            "INSERT INTO attempts (account_id, artifact_id, started_at, outcome, message,"
            " download_s, total_s)"
            " VALUES (1, ?, '2026-11-04T09:59:00+00:00', 'saved', 'Saved.', 12.5, 40.0)",
            (f"art{i}",),
        )
    c.execute(
        "INSERT INTO attempts (account_id, artifact_id, started_at, outcome, message)"
        " VALUES (2, 'art9', '2026-11-05T10:00:00+00:00', 'failed', 'Could not reach NotebookLM.')"
    )
    c.execute("INSERT INTO library_cache VALUES (1, '[]', '2026-11-05T10:00:00+00:00')")
    c.execute(
        "INSERT INTO studio_plans (account_id, notebook_id, notebook_title, items_json, created_at)"
        " VALUES (1, 'nb', 'Garden', '[]', '2026-11-06T10:00:00+00:00')"
    )
    c.execute(
        "INSERT INTO studio_queue (plan_id, account_id, notebook_id, notebook_title, title, focus,"
        " audio_format,"
        " audio_length, language, state, queued_at, harvest_id)"
        " VALUES (1, 1, 'nb', 'Garden', 'Soil', '', 'deep_dive', 'long', 'en', 'done', '2026-11-06', 4)"
    )
    c.execute("INSERT INTO studio_limits VALUES (1, '2026-11-06', '2026-11-06T12:00:00+00:00')")
    c.commit()
    c.close()


def table_hashes(path) -> dict[str, tuple[int, str]]:
    """Row count and a content hash of every v4 table."""
    c = sqlite3.connect(path)
    try:
        out = {}
        for t in DATA_TABLES:
            rows = c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()  # noqa: S608 (fixed names)
            out[t] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest())
        return out
    finally:
        c.close()


def version(path) -> int:
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def has_positions_table(path) -> bool:
    c = sqlite3.connect(path)
    try:
        return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'play_positions'").fetchone())
    finally:
        c.close()


@pytest.fixture
def v4(home, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    path = paths.ledger_path()
    make_v4(path, out)
    return path


# ---------------------------------------------------------------- migration


def test_v4_to_v5_keeps_every_row_and_takes_one_copy(v4):
    before = table_hashes(v4)
    led = ledger_mod.Ledger(v4)
    try:
        assert led.positions_ok
        assert version(v4) == 5
        assert has_positions_table(v4)
    finally:
        led.close()
    assert table_hashes(v4) == before
    copy = v4.parent / ledger_mod.BACKUP_BEFORE_V5
    assert copy.exists() and not (v4.parent / (copy.name + ".part")).exists()
    assert version(copy) == 4 and not has_positions_table(copy)
    assert table_hashes(copy) == before  # a valid v4 ledger with the same content
    c = sqlite3.connect(copy)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()


def test_empty_ledger_takes_no_copy(home):
    led = ledger_mod.Ledger()
    try:
        assert led.positions_ok and version(led.path) == 5
        assert not led.backup_path().exists()
    finally:
        led.close()


def test_an_existing_copy_is_never_overwritten(v4):
    copy = v4.parent / ledger_mod.BACKUP_BEFORE_V5
    copy.write_bytes(b"an earlier copy")
    led = ledger_mod.Ledger(v4)
    try:
        assert led.positions_ok and version(v4) == 5
    finally:
        led.close()
    assert copy.read_bytes() == b"an earlier copy"


def test_a_failed_copy_skips_the_upgrade_and_resume_is_off(v4, monkeypatch, tmp_path):
    before = table_hashes(v4)

    def fail(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(ledger_mod.Ledger, "_copy_to", fail)
    led = ledger_mod.Ledger(v4)
    try:
        assert not led.positions_ok
        assert version(v4) == 4 and not has_positions_table(v4)
        h = led.harvest("art1")
        assert led.position(h) == 0.0 and led.positions() == {}
        app = server.create_app(TOKEN, led)
        app.testing = True
        c = app.test_client()
        r = c.post(f"/api/position/{h.id}", json={"position_s": 300}, headers={"X-NLH-Token": TOKEN})
        assert r.status_code == 200 and r.get_json() == {"saved": False, "reason": "unavailable"}
        assert c.get(f"/api/position/{h.id}", headers={"X-NLH-Token": TOKEN}).get_json()["available"] is False
    finally:
        led.close()
    assert table_hashes(v4) == before
    assert not (v4.parent / ledger_mod.BACKUP_BEFORE_V5).exists()
    assert not (v4.parent / (ledger_mod.BACKUP_BEFORE_V5 + ".part")).exists()
    monkeypatch.undo()  # next start: the copy works, the upgrade happens
    led = ledger_mod.Ledger(v4)
    try:
        assert led.positions_ok and version(v4) == 5
    finally:
        led.close()


def test_a_failed_copy_leaves_no_half_file(v4, monkeypatch):
    real_connect = sqlite3.connect

    class Boom(sqlite3.Connection):
        pass

    def connect(target, *a, **kw):
        if str(target).endswith(".part"):
            con = real_connect(target, *a, **kw)
            con.close()
            raise sqlite3.OperationalError("cannot write")
        return real_connect(target, *a, **kw)

    monkeypatch.setattr(ledger_mod.sqlite3, "connect", connect)
    led = ledger_mod.Ledger(v4)
    try:
        assert not led.positions_ok
    finally:
        led.close()
    assert not (v4.parent / ledger_mod.BACKUP_BEFORE_V5).exists()
    assert not (v4.parent / (ledger_mod.BACKUP_BEFORE_V5 + ".part")).exists()


def test_old_app_on_a_v5_ledger_then_new_app_again(v4):
    """Downgrade: the v4 app ignores the new table; reopening with v5 keeps the positions."""
    led = ledger_mod.Ledger(v4)
    h = led.harvest("art1")
    assert led.set_position(h, 600.0, "laptop") == 600.0
    led.close()
    copy = v4.parent / ledger_mod.BACKUP_BEFORE_V5
    copy_bytes = copy.read_bytes()
    data_v5 = table_hashes(v4)
    c = sqlite3.connect(v4)
    v4_statements(c)  # the old app opens it
    c.close()
    assert version(v4) == 4 and has_positions_table(v4)
    assert table_hashes(v4) == data_v5
    led = ledger_mod.Ledger(v4)  # the new app again
    try:
        assert led.positions_ok and version(v4) == 5
        assert led.position(led.harvest("art1")) == 600.0
    finally:
        led.close()
    assert copy.read_bytes() == copy_bytes  # no second copy, the first untouched


# ---------------------------------------------------------------- stored positions


@pytest.fixture
def led(v4):
    lg = ledger_mod.Ledger(v4)
    yield lg
    lg.close()


def stored(led, harvest_id):
    r = led.conn.execute("SELECT * FROM play_positions WHERE harvest_id = ?", (harvest_id,)).fetchone()
    return dict(r) if r else None


@pytest.mark.parametrize(
    ("given", "kept"),
    [(0, 0.0), (9.9, 0.0), (10, 10.0), (750.44, 750.4), (2384.9, 2384.9), (2385, 0.0), (2400, 0.0)],
)
def test_resume_rules(led, given, kept):
    h = led.harvest("art1")  # 2400 s long
    assert led.set_position(h, given, "laptop") == kept
    row = stored(led, h.id)
    assert row["position_s"] == kept and row["device"] == "laptop"
    assert (row["sha256"], row["size_bytes"], row["duration_s"]) == (h.sha256, h.size_bytes, h.duration_s)
    assert led.position(h) == kept


def test_finished_clears_but_keeps_the_row(led):
    h = led.harvest("art2")
    led.set_position(h, 1200, "phone")
    led.set_position(h, 2399, "laptop")
    assert stored(led, h.id)["position_s"] == 0.0
    assert led.conn.execute("SELECT COUNT(*) FROM play_positions").fetchone()[0] == 1


def test_redownload_into_the_same_row_drops_the_position(led):
    h = led.harvest("art1")
    led.set_position(h, 900, "laptop")
    mp3 = led.harvest("art1").saved_path
    with open(mp3, "ab") as f:
        f.write(b"new file")
    from pathlib import Path

    size = Path(mp3).stat().st_size
    with led.transaction() as c:  # the re-download updates the row in place (same id)
        led.record(
            c,
            account_id=1,
            notebook_id="nb",
            artifact_id="art1",
            overview_title="Title 1",
            notebook_title="Garden",
            created_at=None,
            saved_path=mp3,
            size_bytes=size,
            duration_s=2400.0,
            sha256="other",
        )
    h2 = led.harvest("art1")
    assert h2.id == h.id
    assert led.position(h2) == 0.0
    assert stored(led, h.id)["position_s"] == 0.0  # set to 0, never deleted


def test_a_file_swapped_on_disk_drops_the_position(led):
    h = led.harvest("art3")
    led.set_position(h, 900, "laptop")
    with open(h.saved_path, "ab") as f:
        f.write(b"x")
    assert led.positions() == {}
    assert stored(led, h.id)["position_s"] == 0.0


def test_a_changed_length_drops_the_position(led):
    h = led.harvest("art4")
    led.set_position(h, 900, "laptop")
    led.conn.execute("UPDATE harvests SET duration_s = 2500 WHERE id = ?", (h.id,))
    led.conn.commit()
    assert led.position(led.harvest("art4")) == 0.0


def test_positions_lists_only_valid_non_zero(led):
    a, b = led.harvest("art1"), led.harvest("art2")
    led.set_position(a, 500, "laptop")
    led.set_position(b, 5, "laptop")
    assert led.positions() == {a.id: 500.0}


def test_saves_never_touch_files_tags_or_harvest_rows(led):
    before = table_hashes(led.path)
    files = {h.saved_path: open(h.saved_path, "rb").read() for h in led.harvests()}
    for h in led.harvests():
        for p in (30, 400, 1200, 2399):
            led.set_position(h, p, "laptop")
            led.position(h)
    led.positions()
    after = table_hashes(led.path)
    for t in ("accounts", "harvests", "attempts", "library_cache", "studio_plans", "studio_queue"):
        assert after[t] == before[t], t
    assert {p: open(p, "rb").read() for p in files} == files


# ---------------------------------------------------------------- API


@pytest.fixture
def app(led):
    a = server.create_app(TOKEN, led)
    a.testing = True
    phones = a.config["PHONES"]
    phones.server, phones.address, phones.port = object(), LAN[0], LAN[1]
    return a


def laptop(app):
    c = app.test_client()
    c.environ_base["HTTP_X_NLH_TOKEN"] = TOKEN
    return c


def test_save_and_read_back(app, led):
    c = laptop(app)
    h = led.harvest("art1")
    r = c.post(f"/api/position/{h.id}", json={"position_s": 750.5, "playing": True})
    assert r.status_code == 200 and r.get_json() == {"saved": True, "position_s": 750.5}
    assert stored(led, h.id)["position_s"] == 750.5 and stored(led, h.id)["device"] == "laptop"
    assert c.get(f"/api/position/{h.id}").get_json()["position_s"] == 750.5
    assert app.config["laptop_playing"]() is True
    time.sleep(1.05)
    c.post(f"/api/position/{h.id}", json={"position_s": 760, "playing": False})
    assert app.config["laptop_playing"]() is False


@pytest.mark.parametrize(
    "body",
    [
        {"position_s": -1},
        {"position_s": 2401.5},
        {"position_s": "300"},
        {"position_s": True},
        {"position_s": None},
        {},
        [300],
    ],
)
def test_bad_values_refused(app, led, body):
    h = led.harvest("art1")
    r = laptop(app).post(f"/api/position/{h.id}", json=body)
    assert r.status_code == 400
    assert stored(led, h.id) is None


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", '{"position_s": NaN}', '{"position_s": Infinity}', "not json"]
)
def test_non_finite_refused(app, led, raw):
    h = led.harvest("art1")
    r = laptop(app).post(f"/api/position/{h.id}", data=raw, content_type="application/json")
    assert r.status_code == 400
    assert stored(led, h.id) is None


def test_unknown_file_is_404(app):
    assert laptop(app).post("/api/position/999", json={"position_s": 30}).status_code == 404
    assert laptop(app).get("/api/position/999").status_code == 404


def test_write_for_a_file_that_cannot_play_is_ignored(app, led):
    h = led.harvest("art2")
    from pathlib import Path

    Path(h.saved_path).unlink()
    r = laptop(app).post(f"/api/position/{h.id}", json={"position_s": 300})
    assert r.get_json() == {"saved": False, "reason": "not playable"}
    assert stored(led, h.id) is None
    h3 = led.harvest("art3")
    led.update_account(1, output_dir=str(paths.app_home()))  # file now outside the account's folder
    r = laptop(app).post(f"/api/position/{h3.id}", json={"position_s": 300})
    assert r.get_json() == {"saved": False, "reason": "not playable"}


def test_one_accepted_write_per_second_per_file(app, led):
    c = laptop(app)
    a, b = led.harvest("art1"), led.harvest("art3")
    assert c.post(f"/api/position/{a.id}", json={"position_s": 100}).get_json()["saved"] is True
    r = c.post(f"/api/position/{a.id}", json={"position_s": 200})
    assert r.get_json() == {"saved": False, "reason": "too soon"}
    assert stored(led, a.id)["position_s"] == 100.0
    assert c.post(f"/api/position/{b.id}", json={"position_s": 200}).get_json()["saved"] is True  # other file
    # the last save before closing has its own allowance, so it is not lost behind a regular one
    assert (
        c.post(f"/api/position/{a.id}", json={"position_s": 230, "final": True}).get_json()["saved"] is True
    )
    assert stored(led, a.id)["position_s"] == 230.0
    time.sleep(1.05)
    assert c.post(f"/api/position/{a.id}", json={"position_s": 300}).get_json()["saved"] is True


def test_final_save_signals_the_window(app, led):
    ev = app.config["position_saved"]
    ev.clear()
    h = led.harvest("art1")
    laptop(app).post(f"/api/position/{h.id}", json={"position_s": 300, "final": True})
    assert ev.is_set()


def test_rows_carry_the_position_and_export_does_not(app, led):
    c = laptop(app)
    h = led.harvest("art1")
    c.post(f"/api/position/{h.id}", json={"position_s": 750})
    rows = c.get("/api/history/page").get_json()["rows"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[h.id]["position_s"] == 750.0
    assert by_id[led.harvest("art2").id]["position_s"] == 0.0
    item = c.get("/api/item/art1").get_json()
    assert item["saved"]["position_s"] == 750.0
    header = c.get("/api/export.csv").get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
    assert "position" not in header
    for rec in json.loads(c.get("/api/export.json").get_data(as_text=True)):
        assert not any("position" in k for k in rec)


def test_diagnostics_name_the_copy(app, led):
    text = laptop(app).get("/api/diagnostics").get_json()["text"]
    assert "Resume playback: on" in text
    assert "ledger-before-v5.db" in text and "safe to delete by hand" in text


# ---------------------------------------------------------------- phone (D-037)


class PhoneClient:
    def __init__(self, app, token, host=HOST):
        self.c = app.test_client()
        self.base = f"http://{host}"
        self.c.environ_base.update({phone.ENV_MARK: True, "REMOTE_ADDR": "192.168.1.77"})
        if token:
            self.c.set_cookie(phone.COOKIE, token, domain=host.split(":")[0])

    def open(self, path, **kw):
        return self.c.open(path, base_url=self.base, **kw)


def paired(app):
    phones = app.config["PHONES"]
    code, _ = phones.new_code()
    return phones.pair(code, "Test phone")


def test_paired_phone_saves_and_reads_its_position(app, led):
    pc = PhoneClient(app, paired(app))
    h = led.harvest("art1")
    r = pc.open(f"/api/position/{h.id}", method="POST", json={"position_s": 1300}, headers={"X-NLH-Req": "1"})
    assert r.status_code == 200 and r.get_json()["saved"] is True
    assert stored(led, h.id)["device"] == "phone"
    assert pc.open(f"/api/position/{h.id}", method="GET").get_json()["position_s"] == 1300.0
    assert app.config["laptop_playing"]() is False  # a phone playing never holds the laptop window open


def test_phone_position_write_needs_the_page_header(app, led):
    pc = PhoneClient(app, paired(app))
    h = led.harvest("art1")
    r = pc.open(f"/api/position/{h.id}", method="POST", json={"position_s": 1300})
    assert r.status_code == 403
    assert stored(led, h.id) is None


def test_unpaired_or_wrong_host_phone_cannot_save(app, led):
    h = led.harvest("art1")
    for pc in (
        PhoneClient(app, None),
        PhoneClient(app, "not-a-token"),
        PhoneClient(app, paired(app), host="evil.example:18765"),
    ):
        r = pc.open(
            f"/api/position/{h.id}", method="POST", json={"position_s": 1300}, headers={"X-NLH-Req": "1"}
        )
        assert r.status_code == 403
    assert stored(led, h.id) is None


def test_the_position_save_is_the_only_phone_write(app):
    assert server.PHONE_WRITE_ENDPOINTS == frozenset({"save_position"})
    writes = {
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.methods - {"GET", "HEAD", "OPTIONS"} and rule.endpoint in server.PHONE_WRITE_ENDPOINTS
    }
    assert writes == {"save_position"}
    rules = [r for r in app.url_map.iter_rules() if r.endpoint == "save_position"]
    assert len(rules) == 1 and rules[0].methods - {"HEAD", "OPTIONS"} == {"POST"}


def test_phone_position_answer_is_scrubbed_json(app, led):
    pc = PhoneClient(app, paired(app))
    h = led.harvest("art1")
    body = pc.open(f"/api/position/{h.id}", method="GET").get_data(as_text=True)
    assert set(json.loads(body)) == {"position_s", "available"}
