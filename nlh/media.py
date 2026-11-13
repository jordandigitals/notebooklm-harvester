"""ffprobe and ffmpeg helpers: detect type, convert to MP3 (D-012), tag (D-013), verify."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DURATION_TOLERANCE_S = 0.5


class MediaError(Exception):
    """Conversion or verification failure, with a plain-language message."""


@dataclass(frozen=True)
class Probe:
    """What ffprobe reports about an audio file."""

    format_name: str
    codec: str
    duration: float
    channels: int
    sample_rate: int
    bit_rate: int
    tags: dict


def tools_available() -> dict[str, bool]:
    """Report whether ffmpeg and ffprobe are on PATH (D-017)."""
    return {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")}


def probe(path: Path) -> Probe:
    """Read container, codec, duration, channels, sample rate, bitrate and tags."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "a:0",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise MediaError("The downloaded file is not readable audio.")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("The downloaded file has no audio track.")
    st = streams[0]
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    return Probe(
        format_name=fmt.get("format_name", ""),
        codec=st.get("codec_name", ""),
        duration=float(fmt.get("duration") or st.get("duration") or 0),
        channels=int(st.get("channels") or 0),
        sample_rate=int(st.get("sample_rate") or 0),
        bit_rate=int(fmt.get("bit_rate") or 0),
        tags=tags,
    )


def build_tags(
    overview_title: str, notebook_title: str, created: str | None, notebook_id: str, artifact_id: str
) -> dict[str, str]:
    """MP3 tags per D-013."""
    return {
        "title": overview_title,
        "album": notebook_title,
        "artist": "NotebookLM Audio Overview",
        "date": (created or "")[:10],
        "comment": f"notebooklm:{notebook_id} artifact:{artifact_id}",
    }


def convert_to_mp3(src: Path, dst: Path, tags: dict[str, str]) -> None:
    """Convert to MP3 with libmp3lame VBR -q:a 4, keeping channels and sample rate (D-012)."""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:a:0",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        "-id3v2_version",
        "3",
        "-map_metadata",
        "-1",
    ]
    for key, value in tags.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-f", "mp3", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not dst.exists():
        raise MediaError("Converting the audio to MP3 failed.")


def verify_mp3(path: Path, source_duration: float, tags: dict[str, str]) -> Probe:
    """Check the MP3: non-empty, MP3 codec, duration within tolerance, tags readable."""
    if not path.exists() or path.stat().st_size == 0:
        raise MediaError("The MP3 file is empty.")
    p = probe(path)
    if p.codec != "mp3":
        raise MediaError("The converted file is not MP3.")
    if abs(p.duration - source_duration) > DURATION_TOLERANCE_S:
        raise MediaError(
            f"The MP3 length ({p.duration:.1f}s) does not match the source ({source_duration:.1f}s)."
        )
    for key in ("title", "album", "comment"):
        if p.tags.get(key) != tags.get(key):
            raise MediaError(f"The MP3 tag '{key}' was not written correctly.")
    return p


def sha256(path: Path) -> str:
    """SHA-256 of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
