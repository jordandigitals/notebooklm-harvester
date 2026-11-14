"""U3 backend: naming template, preview, diagnostics redaction, item details."""

import shutil

import pytest
from conftest import needs_ffmpeg

from nlh import harvest, naming, paths, server
from nlh import settings as app_settings
from nlh.adapter import AudioOverview, Notebook

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}


def test_render_template_variants():
    kw = {"notebook": 'Notes: "A/B"', "title": "Why? Now", "date": "2026-11-14T09:00:00", "account": "Work"}
    assert naming.render_template("{notebook}/{title}", **kw) == ("Notes AB", "Why Now")
    assert naming.render_template("{title}", **kw) == (None, "Why Now")
    assert naming.render_template("{account}/{date} {title}", **kw) == ("Work", "2026-11-14 Why Now")
    folder, _ = naming.render_template("{notebook}/{title}", **{**kw, "notebook": "x" * 300})
    assert len(folder) == naming.MAX_FOLDER


def test_template_target_reuses_folder_and_avoids_clashes(tmp_path):
    kw = {"notebook": "N", "title": "T", "date": "2026-11-14", "account": "Work"}
    first = naming.template_target(tmp_path, "{account}/{date} {title}", **kw)
    first.write_bytes(b"x")
    second = naming.template_target(tmp_path, "{account}/{date} {title}", **kw)
    assert first.parent == second.parent == tmp_path / "Work"
    assert second.name == "2026-11-14 T (2).mp3"


def test_template_too_deep_refused_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    deep = tmp_path / ("d" * 120) / ("e" * 60)
    deep.mkdir(parents=True)
    with pytest.raises(naming.PathTooLong):
        naming.check_template_fits(
            deep, "{notebook}/{title}", notebook="n" * 80, title="t", date=None, account="a"
        )
    assert list(deep.iterdir()) == []


@needs_ffmpeg
def test_harvest_honours_template(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Work")
    out = tmp_path / "out"
    out.mkdir()
    ov = AudioOverview("a1", "Episode One", "nb", "Garden", "2026-11-03T10:00:00+00:00", "completed")

    def dl(profile, ov, dest):
        shutil.copyfile(sample_m4a, dest)
        return dest

    r = harvest.harvest_one(ledger, acct, ov, out, downloader=dl, template="{account}/{date} {title}")
    assert r.outcome == "saved"
    assert (out / "Work" / "2026-11-03 Episode One.mp3").exists()
    assert ledger.harvest("a1").saved_path == str(out / "Work" / "2026-11-03 Episode One.mp3")


@pytest.fixture
def client(ledger):
    return server.create_app(TOKEN, ledger).test_client()


def test_preview_endpoint(client):
    r = client.post("/api/naming/preview", json={"template": "{notebook}/{title}"}, headers=H).get_json()
    assert r["path"] == "Garden Planning SpringSummer/Why Raised Beds Warm Up Faster.mp3"
    assert client.post("/api/naming/preview", json={"template": "{nope}"}, headers=H).status_code == 400


def test_diagnostics_has_no_account_details(client, ledger):
    ledger.add_account("acct-x", "Secret Label", "private.person@example.com")
    text = client.get("/api/diagnostics", headers=H).get_json()["text"]
    assert "Accounts: 1" in text and "App home:" in text
    for bad in ("private.person@example.com", "Secret Label", "SID", "__Secure", "token="):
        assert bad not in text


@needs_ffmpeg
def test_item_details_include_tags_and_attempts(ledger, tmp_path, sample_m4a):
    acct = ledger.add_account("p1", "Work")
    out = tmp_path / "out"
    out.mkdir()
    ledger.update_account(acct.id, output_dir=str(out))
    ov = AudioOverview("a9", "Ep", "nb", "Garden", None, "completed")
    ledger.save_listing(acct.id, server._dump_listing([(Notebook("nb", "Garden", None), [ov])]))

    def dl(profile, ov, dest):
        shutil.copyfile(sample_m4a, dest)
        return dest

    harvest.harvest_many(ledger, acct, [ov], out, downloader=dl)
    c = server.create_app(TOKEN, ledger).test_client()
    d = c.get("/api/item/a9", headers=H).get_json()
    assert d["saved"]["title"] == "Ep" and d["tags"]["comment"] == "notebooklm:nb artifact:a9"
    assert d["attempts"][0]["outcome"] == "saved"
    assert c.get("/api/item/unknown", headers=H).get_json()["saved"] is None


def test_settings_template_validation_matches_preview(home):
    app_settings.save({"naming_template": "{account}/{title}"})
    assert app_settings.load()["naming_template"] == "{account}/{title}"
