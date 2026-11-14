"""Phone listening on the same Wi-Fi (D-026, P2-A).

Off by default: the app listens on 127.0.0.1 only. When the person turns the setting on, a
second listener starts on the laptop's private LAN address (never 0.0.0.0, never a public
address). Requests through it are "phone requests": the server lets them read and stream
only, and only from a paired device (see server.guard). Nothing here opens anything to the
internet: no UPnP, no port forwarding, no tunnel, no external call.

Pairing: a single-use code that expires after five minutes becomes a per-device token.
Only the token's SHA-256 is stored, in <home>/phones.json (owner-only, atomic write).
Tokens are never logged and travel only in an HttpOnly cookie.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nlh import paths
from nlh.log import get_logger

log = get_logger()

COOKIE = "nlh_phone"
PAIR_TTL_S = 5 * 60
PAIR_LIMIT = 10  # failed pairing tries per address per minute
NAME_MAX = 40
ENV_MARK = "nlh.phone"  # set by the phone listener only; clients cannot set it


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_private_lan(addr: str) -> bool:
    """True for a private IPv4 address a home network uses (not loopback, link-local or public)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return (
        ip.version == 4
        and ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    )


TUNNEL_PREFIXES = ("utun", "tun", "tap", "wg", "ppp", "ipsec", "gif", "stf", "zt", "tailscale", "nord")
WATCH_S = 10.0


def interface_of(addr: str) -> str | None:
    """The network interface that holds an address (macOS and Linux; None if unknown)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
            name = None
            for line in out.splitlines():
                if line and not line[0].isspace():
                    name = line.split(":", 1)[0]
                elif f"inet {addr} " in line:
                    return name
        elif sys.platform.startswith("linux"):
            out = subprocess.run(["ip", "-o", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) > 3 and parts[3].split("/")[0] == addr:
                    return parts[1]
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def is_tunnel(ifname: str | None) -> bool:
    """True for a VPN or tunnel interface: the phone on Wi-Fi cannot reach it, VPN peers could."""
    return bool(ifname) and ifname.lower().startswith(TUNNEL_PREFIXES)


def lan_address() -> str | None:
    """The laptop's address on the local network, or None.

    A UDP socket "connected" to a documentation address picks the interface the system would
    use; connect() on UDP sends no packet, so this makes no network call.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        addr = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    if not is_private_lan(addr) or is_tunnel(interface_of(addr)):
        return None
    return addr


@dataclass
class Device:
    """A paired phone (the token itself is never kept, only its hash)."""

    id: str
    name: str
    token_hash: str
    created_at: str
    last_seen: str | None = None

    def view(self) -> dict:
        """What the laptop's list shows (never the hash)."""
        return {"id": self.id, "name": self.name, "created_at": self.created_at, "last_seen": self.last_seen}


@dataclass
class _Code:
    expires: float
    used: bool = False


@dataclass
class PhoneState:
    """Paired devices, pairing codes, rate limits and the listener (one per app)."""

    devices: dict[str, Device] = field(default_factory=dict)  # by token hash
    codes: dict[str, _Code] = field(default_factory=dict)
    attempts: dict[str, deque] = field(default_factory=dict)
    server: object | None = None
    thread: threading.Thread | None = None
    address: str | None = None
    port: int | None = None
    clock: object = time.monotonic
    lock: threading.RLock = field(default_factory=threading.RLock)
    _seen_written: dict[str, float] = field(default_factory=dict)
    stopped_reason: str = ""

    # ------------------------------------------------------------ devices

    def load(self) -> None:
        """Read paired devices from <home>/phones.json. Each entry is read on its own and unknown
        fields are ignored, so one damaged entry never drops the others."""
        with self.lock:
            self.devices = {}
            try:
                data = json.loads(_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            for d in data.get("devices", []) if isinstance(data, dict) else []:
                try:
                    dev = Device(
                        id=str(d["id"]),
                        name=str(d["name"]),
                        token_hash=str(d["token_hash"]),
                        created_at=str(d["created_at"]),
                        last_seen=d.get("last_seen"),
                    )
                except (KeyError, TypeError, AttributeError):
                    log.warning("phones.json: one entry could not be read and was skipped")
                    continue
                self.devices[dev.token_hash] = dev

    def _save(self) -> None:
        """Write the paired devices atomically, owner-only."""
        target = _file()
        tmp = target.with_suffix(".json.tmp")
        data = {"devices": [vars(d) for d in self.devices.values()]}
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)

    def device_for(self, token: str | None) -> Device | None:
        """The paired device for a cookie value, or None."""
        if not token or len(token) > 200:
            return None
        with self.lock:
            return self.devices.get(_hash(token))

    def touch(self, dev: Device) -> None:
        """Record last-seen time (written to disk at most once a minute per device)."""
        with self.lock:
            dev.last_seen = _now_iso()
            now = self.clock()
            if now - self._seen_written.get(dev.id, -1e9) >= 60:
                self._seen_written[dev.id] = now
                self._save()

    def list_devices(self) -> list[dict]:
        """Paired phones for the laptop's list, oldest first (no token hashes)."""
        with self.lock:
            return sorted((d.view() for d in self.devices.values()), key=lambda d: d["created_at"])

    def revoke(self, device_id: str) -> bool:
        """Remove a paired phone; its next request (and any stream in progress) is refused."""
        with self.lock:
            for h, d in list(self.devices.items()):
                if d.id == device_id:
                    del self.devices[h]
                    self._save()
                    log.info("phone %s revoked", device_id)
                    return True
        return False

    # ------------------------------------------------------------ pairing

    def new_code(self) -> tuple[str, int]:
        """A single-use pairing code and its lifetime in seconds. Older unused codes stop working."""
        with self.lock:
            self.codes = {}
            code = secrets.token_urlsafe(16)
            self.codes[code] = _Code(self.clock() + PAIR_TTL_S)
            return code, PAIR_TTL_S

    def _live(self, c: _Code | None) -> bool:
        """True for a code that is unused and not expired (the one rule for codes)."""
        return bool(c and not c.used and self.clock() < c.expires)

    def code_valid(self, code: str) -> bool:
        """True if the code can still pair a phone."""
        with self.lock:
            return self._live(self.codes.get(code or ""))

    def code_left_s(self) -> int | None:
        """Seconds left on the current code, for the laptop's countdown."""
        with self.lock:
            live = [c for c in self.codes.values() if self._live(c)]
            return round(live[0].expires - self.clock()) if live else None

    def _recent(self, who: str) -> deque:
        """Failed tries from one address in the last minute."""
        q = self.attempts.setdefault(who, deque())
        now = self.clock()
        while q and now - q[0] > 60:
            q.popleft()
        return q

    def allow_attempt(self, who: str) -> bool:
        """False once an address has PAIR_LIMIT failed pairing tries in the last minute."""
        with self.lock:
            return len(self._recent(who)) < PAIR_LIMIT

    def note_failure(self, who: str) -> None:
        """Count a wrong, used or expired code against the address (guessing is slowed down)."""
        with self.lock:
            self._recent(who).append(self.clock())

    def pair(self, code: str, name: str) -> str | None:
        """Use a code once and create a device. Returns the new token (for the cookie only)."""
        name = " ".join((name or "").split())[:NAME_MAX] or "Phone"
        with self.lock:
            c = self.codes.get(code or "")
            if not self._live(c):
                return None
            c.used = True
            token = secrets.token_urlsafe(32)
            dev = Device(secrets.token_hex(6), name, _hash(token), _now_iso())
            self.devices[dev.token_hash] = dev
            self._save()
            log.info("phone %s paired", dev.id)
            return token

    # ------------------------------------------------------------ listener

    @property
    def listening(self) -> bool:
        return self.server is not None

    def start(self, app, port: int) -> str | None:
        """Start the LAN listener. Returns a plain error message, or None on success."""
        from werkzeug.serving import make_server

        with self.lock:
            if self.server is not None:
                return None
            addr = lan_address()
            if addr is None:
                return "This laptop is not on a home network right now (no Wi-Fi or local network address)."
            try:
                srv = make_server(addr, port, _Marked(app), threaded=True)
            except OSError:
                try:
                    srv = make_server(addr, 0, _Marked(app), threaded=True)
                except OSError:
                    return "Could not open the phone connection on this network."
            self.server, self.address, self.port = srv, addr, srv.server_port
            self.thread = threading.Thread(target=srv.serve_forever, daemon=True, name="nlh-phone")
            self.thread.start()
            self.stopped_reason = ""
            threading.Thread(
                target=self._watch, args=(srv, addr), daemon=True, name="nlh-phone-watch"
            ).start()
            log.info("phone listener on the local network, port %d", self.port)
            return None

    def _watch(self, srv, addr: str) -> None:
        """Close the listener if the laptop's network address changes (another network, a VPN)."""
        while self.server is srv:
            time.sleep(WATCH_S)
            if self.server is srv and lan_address() != addr:
                self.stopped_reason = "The laptop moved to another network, so phone listening stopped."
                log.info("phone listener stopped: network changed")
                self.stop()
                return

    def stop(self) -> None:
        """Close the LAN listener now; streams in progress end at their next chunk."""
        with self.lock:
            srv, self.server = self.server, None
            self.codes = {}
        if srv is not None:
            srv.shutdown()
            srv.server_close()
            log.info("phone listener closed")

    def url(self) -> str | None:
        """The address the phone opens, while listening."""
        return f"http://{self.address}:{self.port}" if self.server is not None else None


class _Marked:
    """WSGI wrapper used only by the phone listener: marks the request as a phone request."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        """Mark and pass on one request."""
        environ[ENV_MARK] = True
        return self.app(environ, start_response)


def _file():
    """Where paired devices are kept."""
    return paths.app_home() / "phones.json"


def qr_svg(text: str) -> str:
    """The pairing link as an SVG QR code (made on the laptop, no network)."""
    import io

    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(
        buf, kind="svg", scale=6, border=2, dark="#000", light="#fff", xmldecl=False
    )
    return buf.getvalue().decode("utf-8")
