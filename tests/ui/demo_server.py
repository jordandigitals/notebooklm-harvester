"""Start the app in demo mode on a free port for UI tests and screenshots."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DemoServer:
    """Context manager: a demo-mode server and its first-open URL (with the page key)."""

    def __init__(self, rows: int = 2000, home: str | None = None):
        self.rows = rows
        self.home = home or tempfile.mkdtemp(prefix="nlh-ui-")
        self.port = free_port()
        self.proc = None

    def __enter__(self):
        env = {
            **os.environ,
            "NLH_DEMO": "1",
            "NLH_DEMO_HOME": self.home,
            "NLH_DEMO_ROWS": str(self.rows),
            "NLH_PORT": str(self.port),
            "NLH_NO_BROWSER": "1",
            "PYTHONPATH": str(ROOT),
        }
        env.pop("NLH_HOME", None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "nlh", "serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 180
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("demo server exited: " + (self.proc.stderr.read() or "")[-2000:])
            try:
                cfg = json.loads((Path(self.home) / "server.json").read_text())
                urllib.request.urlopen(f"http://127.0.0.1:{cfg['port']}/favicon.ico", timeout=2)
                self.token = cfg["token"]
                self.base = f"http://127.0.0.1:{cfg['port']}"
                return self
            except (OSError, ValueError, KeyError):
                time.sleep(0.5)
        raise RuntimeError("demo server did not start")

    @property
    def url(self) -> str:
        return f"{self.base}/?t={self.token}"

    def api(self, path: str):
        req = urllib.request.Request(self.base + path, headers={"X-NLH-Token": self.token})
        return json.load(urllib.request.urlopen(req, timeout=30))

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
