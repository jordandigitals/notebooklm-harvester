"""Conversion, tags and verification (D-012, D-013)."""

import pytest
from conftest import needs_ffmpeg

from nlh import media


@needs_ffmpeg
def test_convert_tags_and_verify(sample_m4a, tmp_path):
    src = media.probe(sample_m4a)
    assert src.codec == "aac" and src.channels == 1
    tags = media.build_tags("Overview T", "Notebook N", "2026-11-12T09:00:00+00:00", "nb1", "art1")
    out = tmp_path / "o.mp3"
    media.convert_to_mp3(sample_m4a, out, tags)
    p = media.verify_mp3(out, src.duration, tags)
    assert p.codec == "mp3" and p.channels == 1 and p.sample_rate == 44100
    assert abs(p.duration - src.duration) <= 0.5
    assert p.tags["title"] == "Overview T"
    assert p.tags["album"] == "Notebook N"
    assert p.tags["artist"] == "NotebookLM Audio Overview"
    assert p.tags["date"].startswith("2026-11-12")
    assert p.tags["comment"] == "notebooklm:nb1 artifact:art1"


@needs_ffmpeg
def test_verify_rejects_wrong_duration(sample_m4a, tmp_path):
    tags = media.build_tags("T", "N", None, "nb", "a")
    out = tmp_path / "o.mp3"
    media.convert_to_mp3(sample_m4a, out, tags)
    with pytest.raises(media.MediaError):
        media.verify_mp3(out, 100.0, tags)


@needs_ffmpeg
def test_probe_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not audio at all" * 100)
    with pytest.raises(media.MediaError):
        media.probe(bad)


@needs_ffmpeg
def test_convert_reports_progress(sample_m4a, tmp_path):
    seen = []
    tags = media.build_tags("T", "N", None, "nb", "a")
    src = media.probe(sample_m4a)
    media.convert_to_mp3(sample_m4a, tmp_path / "o.mp3", tags, duration=src.duration, on_progress=seen.append)
    assert seen and all(0 <= f <= 1 for f in seen) and seen[-1] > 0.9


def test_tool_prefers_bundled_copy(tmp_path, monkeypatch):
    import sys

    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    (tmp_path / exe).write_text("")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert media.tool("ffmpeg") == str(tmp_path / exe)
