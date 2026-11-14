"""U2 backend: paged history, export, statistics, the player route and re-download rules."""

import csv
import io
import json
import os
import sys

import pytest

from nlh import server

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}


def _rec(ledger, acct, aid, path, *, title=None, notebook="Notebook", size=1000, dur=60.0, saved_at=None):
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=acct.id,
            notebook_id="nb-" + notebook,
            artifact_id=aid,
            overview_title=title or f"Title {aid}",
            notebook_title=notebook,
            created_at=None,
            saved_path=str(path),
            size_bytes=size,
            duration_s=dur,
            sha256="h",
        )
        if saved_at:
            c.execute("UPDATE harvests SET saved_at = ? WHERE artifact_id = ?", (saved_at, aid))


@pytest.fixture
def big(ledger, tmp_path):
    """260 saved rows over two accounts, files present for the first account."""
    out = tmp_path / "out"
    (out / "Notebook").mkdir(parents=True)
    a1 = ledger.add_account("p1", "One")
    a2 = ledger.add_account("p2", "Two")
    ledger.update_account(a1.id, output_dir=str(out))
    for i in range(260):
        acct = a1 if i % 2 == 0 else a2
        path = out / "Notebook" / f"T{i:03d}.mp3"
        if acct is a1:
            path.write_bytes(b"ID3" + bytes(i % 7) * 100)
        _rec(
            ledger,
            acct,
            f"a{i:03d}",
            path,
            title=f"Episode {i:03d}" + (" garden" if i % 10 == 0 else ""),
            size=1000 + i,
            dur=60.0 + i,
            saved_at=f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T10:00:00+00:00",
        )
    c = server.create_app(TOKEN, ledger).test_client()
    return c, a1, a2, out


def test_history_pages_first_middle_last(big):
    c, *_ = big
    first = c.get("/api/history/page?size=100&page=1&sort=title_asc", headers=H).get_json()
    middle = c.get("/api/history/page?size=100&page=2&sort=title_asc", headers=H).get_json()
    last = c.get("/api/history/page?size=100&page=3&sort=title_asc", headers=H).get_json()
    assert first["total"] == middle["total"] == last["total"] == 260
    assert [r["title"][:11] for r in first["rows"]][:2] == ["Episode 000", "Episode 001"]
    assert len(first["rows"]) == 100 and len(middle["rows"]) == 100 and len(last["rows"]) == 60
    seen = [r["artifact_id"] for p in (first, middle, last) for r in p["rows"]]
    assert len(set(seen)) == 260  # no overlaps, nothing missing


def test_history_filters_search_and_accounts(big):
    c, a1, a2, _ = big
    got = c.get("/api/history/page?q=garden&size=250", headers=H).get_json()
    assert got["total"] == 26 and all("garden" in r["title"] for r in got["rows"])
    got = c.get(f"/api/history/page?accounts={a2.id}&size=250", headers=H).get_json()
    assert got["total"] == 130 and {r["account"] for r in got["rows"]} == {"Two"}
    got = c.get("/api/history/page?saved_from=2026-03-01&saved_to=2026-03-31&size=250", headers=H).get_json()
    assert got["total"] and all(r["saved_at"].startswith("2026-03") for r in got["rows"])
    got = c.get("/api/history/page?size_min=1250&length_max=400&size=250", headers=H).get_json()
    assert got["total"] and all(r["size_bytes"] >= 1250 and r["duration_s"] <= 400 for r in got["rows"])
    # odd input never breaks the query (all values are bound parameters)
    assert c.get("/api/history/page?q=%25'%20OR%201=1--&size=50", headers=H).get_json()["total"] == 0


def test_export_csv_and_json_whole_filtered_ledger(big):
    c, a1, *_ = big
    raw = c.get(f"/api/export.csv?accounts={a1.id}", headers=H).get_data()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 byte-order mark, for Excel on Windows
    text = raw.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 130  # more than one page: the whole filtered set
    assert list(rows[0].keys()) == [
        "id",
        "account_id",
        "notebook_id",
        "artifact_id",
        "overview_title",
        "notebook_title",
        "created_at",
        "saved_path",
        "size_bytes",
        "duration_s",
        "sha256",
        "saved_at",
        "origin",
    ]
    data = json.loads(c.get("/api/export.json", headers=H).get_data(as_text=True))
    assert len(data) == 260 and set(data[0]) == set(rows[0])
    lowered = (text + json.dumps(data)).lower()
    for word in ("cookie", "token", "storage_state", "__secure", "sapisid"):
        assert word not in lowered


def test_stats_totals_match_ledger(big, ledger):
    c, *_ = big
    s = c.get("/api/stats", headers=H).get_json()
    rows = ledger.harvests()
    assert s["totals"]["files"] == 260
    assert s["totals"]["bytes"] == sum(h.size_bytes for h in rows)
    assert abs(s["totals"]["hours"] - sum(h.duration_s for h in rows) / 3600) < 0.01
    assert len(s["by_notebook"]) <= 10 and sum(a["files"] for a in s["by_account"]) == 260
    assert [m["name"] for m in s["by_month"]] == sorted(m["name"] for m in s["by_month"])


def test_play_streams_ledger_file_with_range(big, ledger):
    c, a1, *_ = big
    h = ledger.harvest("a004")
    r = c.get(f"/api/play/{h.id}", headers=H)
    assert r.status_code == 200 and r.mimetype == "audio/mpeg"
    full = open(h.saved_path, "rb").read()
    assert r.data == full
    r = c.get(f"/api/play/{h.id}", headers={**H, "Range": "bytes=2-9"})
    assert r.status_code == 206 and r.data == full[2:10]


def test_play_refuses_everything_else(big, ledger, tmp_path):
    c, a1, a2, out = big
    assert c.get("/api/play/999999", headers=H).status_code == 404
    assert c.get(f"/api/play/{ledger.harvest('a004').id}").status_code == 403  # no key
    assert (
        c.get(f"/api/play/{ledger.harvest('a001').id}", headers=H).status_code == 403
    )  # account 2 has no folder
    outside = tmp_path / "elsewhere.mp3"
    outside.write_bytes(b"ID3")
    _rec(ledger, a1, "out1", outside)
    assert c.get(f"/api/play/{ledger.harvest('out1').id}", headers=H).status_code == 403
    txt = out / "Notebook" / "notes.txt"
    txt.write_text("not audio")
    _rec(ledger, a1, "txt1", txt)
    assert c.get(f"/api/play/{ledger.harvest('txt1').id}", headers=H).status_code == 403
    if sys.platform != "win32":
        link = out / "Notebook" / "sneaky.mp3"
        os.symlink(outside, link)
        _rec(ledger, a1, "lnk1", link)
        assert c.get(f"/api/play/{ledger.harvest('lnk1').id}", headers=H).status_code == 403
    # traversal-looking ids are just not routes
    assert c.get("/api/play/..%2F..%2Fetc%2Fpasswd", headers=H).status_code == 404


def test_redownload_rules(big, ledger, tmp_path):
    c, a1, a2, out = big
    present = ledger.harvest("a004")
    assert c.post(f"/api/history/{present.id}/redownload", headers=H).status_code == 400
    ledger.update_account(a2.id, output_dir=str(tmp_path / "unplugged"))
    _rec(ledger, a2, "off1", tmp_path / "unplugged" / "Notebook" / "Gone.mp3")
    gone = ledger.harvest("off1")  # account 2, its drive is not there
    r = c.post(f"/api/history/{gone.id}/redownload", headers=H)
    assert r.status_code == 400 and "not reachable" in r.get_json()["error"]
    assert ledger.harvest("off1").saved_path == gone.saved_path


def test_history_rows_flag_playable_and_reasons(big):
    c, a1, *_ = big
    rows = c.get(f"/api/history/page?accounts={a1.id}&size=50", headers=H).get_json()["rows"]
    assert all(r["playable"] for r in rows)
    other = c.get("/api/history/page?accounts=2&size=50", headers=H).get_json()["rows"]
    assert not any(r["playable"] for r in other)


def test_export_csv_keeps_accents_and_emoji_for_excel(ledger, tmp_path):
    """Accented and emoji titles survive a round trip through the CSV export."""
    import csv
    import io

    from nlh import server

    acct = ledger.add_account("p-accent", "Café account")
    f = tmp_path / "x.mp3"
    f.write_bytes(b"x")
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=acct.id,
            notebook_id="nb-accent",
            artifact_id="art-accent",
            overview_title="Résumé of Zoë's \U0001f3a7 notes",
            notebook_title="Crème brûlée",
            created_at=None,
            saved_path=str(f),
            size_bytes=1,
            duration_s=1.0,
            sha256="0" * 64,
        )
    app = server.create_app("tok", ledger)
    app.testing = True
    raw = app.test_client().get("/api/export.csv", headers={"X-NLH-Token": "tok"}).get_data()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0]["overview_title"] == "Résumé of Zoë's \U0001f3a7 notes"
    assert rows[0]["notebook_title"] == "Crème brûlée"
