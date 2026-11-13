"""Test setup: every test runs with a throwaway app home and no real session."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Set before any nlh import so the logger and paths never touch the real app home.
_SESSION_HOME = tempfile.mkdtemp(prefix="nlh-test-home-")
os.environ["NLH_HOME"] = _SESSION_HOME

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg and ffprobe required")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh app home for one test."""
    h = tmp_path / "home"
    monkeypatch.setenv("NLH_HOME", str(h))
    return h


@pytest.fixture
def ledger(home):
    """A ledger in the test home."""
    from nlh.ledger import Ledger

    lg = Ledger()
    yield lg
    lg.close()


@pytest.fixture(scope="session")
def sample_m4a():
    """A synthetic 6.2 second mono AAC file in an M4A container (no real audio in git)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg required")
    out = Path(tempfile.mkdtemp(prefix="nlh-sample-")) / "sample.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6.2",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(out),
        ],
        check=True,
    )
    return out
