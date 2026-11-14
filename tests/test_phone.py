"""P2-A phone listening (D-026): bind address, pairing, tokens, revoke, and the server's
listen-only rule for phone requests (every route is checked from the app's own map)."""

import json
import re
import shutil

import pytest

from nlh import phone, server
from nlh import settings as app_settings

TOKEN = "k"
H = {"X-NLH-Token": TOKEN}
LAN = ("192.168.1.50", 18765)
HOST = f"{LAN[0]}:{LAN[1]}"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- addresses


@pytest.mark.parametrize(
    "addr,ok",
    [
        ("192.168.8.157", True),
        ("10.1.2.3", True),
        ("172.20.10.10", True),
        ("127.0.0.1", False),
        ("169.254.1.1", False),
        ("8.8.8.8", False),
        ("100.127.175.83", False),
        ("0.0.0.0", False),
        ("fe80::1", False),
        ("nonsense", False),
    ],
)
def test_only_private_lan_addresses(addr, ok):
    assert phone.is_private_lan(addr) is ok


def test_lan_address_is_private_or_none():
    a = phone.lan_address()
    assert a is None or phone.is_private_lan(a)


def test_default_is_off(home):
    s = app_settings.load()
    assert s["phone_enabled"] is False and s["phone_port"] == 0


# ---------------------------------------------------------------- pairing and tokens


def test_pairing_code_single_use_and_expiry(home):
    st = phone.PhoneState(clock=Clock())
    code, ttl = st.new_code()
    assert ttl == 300 and st.code_valid(code)
    token = st.pair(code, "  the person's   phone ")
    assert token and st.device_for(token).name == "the person's phone"
    assert st.pair(code, "again") is None  # single use
    code2, _ = st.new_code()
    st.clock.t += 301
    assert not st.code_valid(code2) and st.pair(code2, "late") is None  # expired


def test_new_code_cancels_the_old_one(home):
    st = phone.PhoneState(clock=Clock())
    a, _ = st.new_code()
    b, _ = st.new_code()
    assert not st.code_valid(a) and st.code_valid(b)


def test_tokens_stored_hashed_owner_only_and_revocable(home):
    st = phone.PhoneState(clock=Clock())
    code, _ = st.new_code()
    token = st.pair(code, "P")
    f = home / "phones.json"
    text = f.read_text()
    assert token not in text and phone._hash(token) in text
    import os
    import sys

    if sys.platform != "win32":
        assert oct(os.stat(f).st_mode)[-3:] == "600"
    again = phone.PhoneState()
    again.load()
    assert again.device_for(token) is not None
    dev_id = json.loads(text)["devices"][0]["id"]
    assert again.revoke(dev_id) and again.device_for(token) is None
    assert again.device_for(None) is None and again.device_for("x" * 500) is None


def test_pairing_rate_limit(home):
    st = phone.PhoneState(clock=Clock())
    for _ in range(phone.PAIR_LIMIT):
        assert st.allow_attempt("1.2.3.4")
        st.note_failure("1.2.3.4")
    assert not st.allow_attempt("1.2.3.4")
    assert st.allow_attempt("5.6.7.8")
    st.clock.t += 61
    assert st.allow_attempt("1.2.3.4")


def test_qr_is_svg():
    svg = phone.qr_svg("http://192.168.1.50:18765/pair/abc")
    assert svg.lstrip().startswith("<svg") and "<script" not in svg


# ---------------------------------------------------------------- server rules


@pytest.fixture
def app(ledger, tmp_path, sample_m4a):
    out = tmp_path / "out"
    out.mkdir()
    acct = ledger.add_account("p1", "Work")
    ledger.update_account(acct.id, output_dir=str(out))
    mp3 = out / "Garden" / "Soil.mp3"
    mp3.parent.mkdir()
    shutil.copyfile(sample_m4a, mp3)  # content type does not matter for these checks
    with ledger.transaction() as c:
        ledger.record(
            c,
            account_id=acct.id,
            notebook_id="nb",
            artifact_id="a1",
            overview_title="Soil",
            notebook_title="Garden",
            created_at=None,
            saved_path=str(mp3),
            size_bytes=mp3.stat().st_size,
            duration_s=6.0,
            sha256="x",
        )
    a = server.create_app(TOKEN, ledger)
    a.testing = True
    phones = a.config["PHONES"]
    phones.server, phones.address, phones.port = object(), LAN[0], LAN[1]  # pretend the listener runs
    return a


class PhoneClient:
    """Test client whose requests arrive as through the phone listener, with the given Host."""

    def __init__(self, app, token, host):
        self.c = app.test_client()
        self.base = f"http://{host}"
        self.c.environ_base.update({phone.ENV_MARK: True, "REMOTE_ADDR": "192.168.1.77"})
        if token:
            self.c.set_cookie(phone.COOKIE, token, domain=host.split(":")[0])

    def open(self, path, **kw):
        return self.c.open(path, base_url=self.base, **kw)

    def get(self, path, **kw):
        return self.open(path, method="GET", **kw)

    def post(self, path, **kw):
        return self.open(path, method="POST", **kw)


def phone_client(app, token=None, host=HOST):
    return PhoneClient(app, token, host)


def paired(app):
    phones = app.config["PHONES"]
    code, _ = phones.new_code()
    return phones.pair(code, "Test phone")


def test_unpaired_phone_gets_nothing(app):
    c = phone_client(app)
    r = c.get("/")
    assert r.status_code == 403 and b"not paired" in r.data and b"vspacer" not in r.data
    for path in ("/api/state", "/api/library", "/api/history/page", "/api/play/1", "/api/stats"):
        assert c.get(path).status_code == 403, path
    assert c.get("/static/app.css").status_code == 200  # the pairing page's stylesheet only
    assert c.get("/static/js/main.js").status_code == 403


def test_wrong_host_refused_even_with_token(app):
    tok = paired(app)
    assert phone_client(app, tok, host="evil.example:18765").get("/api/state").status_code == 403
    assert phone_client(app, tok, host="127.0.0.1:18765").get("/api/state").status_code == 403


def test_pair_flow_sets_cookie_and_page_works(app):
    phones = app.config["PHONES"]
    code, _ = phones.new_code()
    c = phone_client(app)
    page = c.get(f"/pair/{code}")
    assert page.status_code == 200 and b"Pair this phone" in page.data
    r = c.post(f"/pair/{code}", data={"name": "Note 20"})
    assert r.status_code == 303 and r.headers["Location"] == "/"
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and phone.COOKIE in cookie
    token = re.search(rf"{phone.COOKIE}=([^;]+)", cookie).group(1)
    assert phone_client(app, token).get("/api/state").get_json()["role"] == "phone"
    assert phone_client(app).get(f"/pair/{code}").status_code == 410  # used up


def test_pair_page_rate_limited(app):
    c = phone_client(app)
    codes = [c.get("/pair/wrong").status_code for _ in range(phone.PAIR_LIMIT + 2)]
    assert codes[: phone.PAIR_LIMIT] == [410] * phone.PAIR_LIMIT and codes[-1] == 429


def test_pair_routes_not_on_the_laptop_listener(app):
    code, _ = app.config["PHONES"].new_code()
    assert app.test_client().get(f"/pair/{code}", headers=H).status_code == 404


def _sample_path(rule):
    path = rule.rule
    for arg, conv in rule._converters.items():
        val = "1" if type(conv).__name__ == "IntegerConverter" else "x"
        path = re.sub(rf"<(?:[a-z]+:)?{arg}>", val, path)
    return path


def test_every_route_outside_the_allow_list_is_refused_to_a_phone(app):
    tok = paired(app)
    c = phone_client(app, tok)
    checked = 0
    for rule in app.url_map.iter_rules():
        path = _sample_path(rule)
        for method in sorted(rule.methods - {"OPTIONS", "HEAD"}):
            allowed = rule.endpoint in server.PHONE_ENDPOINTS and method == "GET"
            open_ = rule.endpoint in server.PHONE_OPEN_ENDPOINTS
            if allowed or open_:
                continue
            r = c.open(path, method=method, headers={"X-NLH-Req": "1"}, json={})
            assert r.status_code == 403, f"{method} {rule.rule} gave {r.status_code}"
            checked += 1
    assert checked >= 40  # every write route and every non-listening read route


def test_allowed_reads_work_for_a_phone(app):
    tok = paired(app)
    c = phone_client(app, tok)
    for path in (
        "/",
        "/api/state",
        "/api/settings",
        "/api/library",
        "/api/history/page",
        "/api/history/notebooks",
        "/api/stats",
        "/api/item/a1",
    ):
        assert c.get(path).status_code == 200, path
    assert c.get("/static/js/main.js").status_code == 200


def test_phone_stream_supports_range_and_stops_on_revoke(app, ledger):
    phones = app.config["PHONES"]
    tok = paired(app)
    c = phone_client(app, tok)
    hid = ledger.harvest("a1").id
    r = c.get(f"/api/play/{hid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100
    r = c.get(f"/api/play/{hid}", buffered=False)
    it = iter(r.response)
    first = next(it)
    assert first
    dev_id = phones.list_devices()[0]["id"]
    phones.revoke(dev_id)
    rest = b"".join(it)
    assert len(first) + len(rest) < ledger.harvest("a1").size_bytes  # stream ended early
    assert c.get("/api/state").status_code == 403


def test_listener_off_refuses_everything(app):
    tok = paired(app)
    app.config["PHONES"].server = None
    assert phone_client(app, tok).get("/api/state").status_code == 403


def test_phone_settings_routes_are_laptop_only(app):
    tok = paired(app)
    assert phone_client(app, tok).get("/api/phone").status_code == 403
    lap = app.test_client()
    st = lap.get("/api/phone", headers=H).get_json()
    assert st["listening"] is True and len(st["devices"]) == 1
    r = lap.post("/api/phone/pair", headers={**H})
    assert r.status_code == 200 and r.get_json()["link"].startswith(f"http://{HOST}/pair/")


def test_real_listener_binds_lan_only(app, home):
    """Start the real second listener: it binds the LAN address, never 0.0.0.0, and closes on stop."""
    import socket

    phones = phone.PhoneState()
    addr = phone.lan_address()
    if addr is None:
        pytest.skip("no private LAN address on this machine")
    assert phones.start(app, 0) is None
    try:
        assert phones.address == addr and phones.server.server_address[0] == addr
        with socket.create_connection((addr, phones.port), timeout=3):
            pass
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", phones.port), timeout=1).close()
    finally:
        phones.stop()
    with pytest.raises(OSError):
        socket.create_connection((addr, phones.port), timeout=1).close()


# ---------------------------------------------------------------- code review fixes


def test_phone_answers_carry_no_emails_or_full_paths(app, ledger):
    ledger.update_account(ledger.accounts()[0].id, email="private@example.com", chrome_profile="Profile 2")
    tok = paired(app)
    c = phone_client(app, tok)
    hid = ledger.harvest("a1").id
    for path in ("/api/state", "/api/library", "/api/settings", "/api/history/page", "/api/item/a1"):
        body = c.get(path).get_data(as_text=True)
        assert "private@example.com" not in body and "Profile 2" not in body, path
        assert str(ledger.harvest("a1").saved_path) not in body, path
    rows = c.get("/api/history/page").get_json()["rows"]
    assert rows[0]["path"] == "Soil.mp3" and rows[0]["folder"] == "Garden"  # names stay for the page
    assert hid  # the laptop still sees everything
    lap = app.test_client().get("/api/state", headers=H).get_data(as_text=True)
    assert "private@example.com" in lap


def test_phone_keys_not_writable_through_generic_settings(app):
    lap = app.test_client()
    for key, val in (("phone_enabled", True), ("phone_port", 80), ("phone_address", "1.2.3.4")):
        r = lap.post("/api/settings", json={key: val}, headers={**H, "X-NLH-Req": "1"})
        assert r.status_code == 400, key


def test_one_bad_device_entry_does_not_drop_the_others(home):
    st = phone.PhoneState(clock=Clock())
    code, _ = st.new_code()
    token = st.pair(code, "Good")
    f = home / "phones.json"
    data = json.loads(f.read_text())
    data["devices"].insert(0, {"id": "x", "broken": True})
    data["devices"].append({**data["devices"][-1], "id": "zz", "token_hash": "h2", "extra": 1})
    f.write_text(json.dumps(data))
    again = phone.PhoneState()
    again.load()
    assert again.device_for(token) is not None and len(again.list_devices()) == 2


@pytest.mark.parametrize(
    "name,tunnel",
    [
        ("utun4", True),
        ("wg0", True),
        ("tun0", True),
        ("ppp0", True),
        ("en0", False),
        ("wlan0", False),
        (None, False),
        # Windows: "<alias>|<description>" (alias names are often generic)
        ("NordLynx|NordLynx Tunnel", True),
        ("Local Area Connection 4|TAP-NordVPN Windows Adapter V9", True),
        ("Local Area Connection 2|ExpressVPN TAP Adapter", True),
        ("Local Area Connection|ExpressVPN TUN Driver", True),
        ("OpenVPN Data Channel Offload for NordVPN|OpenVPN Data Channel Offload #2", True),
        ("Tailscale|Tailscale Tunnel", True),
        ("Ethernet 3|WireGuard Tunnel", True),
        ("Wi-Fi|Intel(R) Wi-Fi 6 AX200 160MHz", False),
        ("Ethernet|Realtek PCIe GbE Family Controller", False),
    ],
)
def test_tunnel_interfaces_refused(name, tunnel):
    assert phone.is_tunnel(name) is tunnel


def test_listener_closes_when_the_network_changes(app, home, monkeypatch):
    addr = phone.lan_address()
    if addr is None:
        pytest.skip("no private LAN address on this machine")
    monkeypatch.setattr(phone, "WATCH_S", 0.2)
    st = phone.PhoneState()
    assert st.start(app, 0) is None
    monkeypatch.setattr(phone, "lan_address", lambda: "10.9.9.9")  # moved to another network
    import time

    for _ in range(50):
        if not st.listening:
            break
        time.sleep(0.1)
    assert not st.listening and "another network" in st.stopped_reason


def test_head_request_on_a_phone_stream_closes_cleanly(app, ledger):
    tok = paired(app)
    c = phone_client(app, tok)
    r = c.open(f"/api/play/{ledger.harvest('a1').id}", method="HEAD")
    assert r.status_code == 200 and r.data == b""


def test_windows_vpn_address_is_not_used(monkeypatch):
    """On Windows a private address held by a VPN adapter is refused (D-026 scope)."""
    monkeypatch.setattr(phone, "interface_of", lambda addr: "NordLynx|NordLynx Tunnel")

    class FakeSock:
        def connect(self, a):
            pass

        def getsockname(self):
            return ("10.5.0.2", 50000)

        def close(self):
            pass

    monkeypatch.setattr(phone.socket, "socket", lambda *a, **k: FakeSock())
    assert phone.lan_address() is None
    monkeypatch.setattr(phone, "interface_of", lambda addr: "Wi-Fi|Intel(R) Wi-Fi 6 AX200 160MHz")
    assert phone.lan_address() == "10.5.0.2"


def test_windows_interface_lookup_refuses_non_addresses(monkeypatch):
    """Only a real IPv4 address ever reaches the PowerShell command."""
    ran = []
    monkeypatch.setattr(phone.subprocess, "run", lambda *a, **k: ran.append(a))
    phone._WIN_IFACE_CACHE.clear()
    assert phone._windows_interface("1.2.3.4'; Remove-Item x; '") is None
    assert ran == []


# ---------------------------------------------------------------- home-screen shortcut


def test_manifest_valid_with_required_fields():
    from nlh.server import STATIC

    m = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for key in ("name", "short_name", "start_url", "display", "icons", "theme_color", "background_color"):
        assert m.get(key), key
    assert m["display"] == "standalone" and m["start_url"] == "/"
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
    for icon in m["icons"]:
        assert (STATIC / icon["src"].removeprefix("/static/")).exists()


@pytest.mark.parametrize(
    "name,size",
    [
        ("icon-192.png", 192),
        ("icon-512.png", 512),
        ("icon-maskable-512.png", 512),
        ("apple-touch-icon.png", 180),
    ],
)
def test_icon_sizes(name, size):
    import struct

    from nlh.server import STATIC

    data = (STATIC / "icons" / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (size, size)


def test_unpaired_phone_gets_manifest_and_icons_only(app):
    c = phone_client(app)
    r = c.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    for name in sorted(server.PHONE_OPEN_STATIC):
        assert c.get(f"/static/{name}").status_code == 200, name
    for path in ("/static/js/main.js", "/static/index.html", "/api/state", "/"):
        assert c.get(path).status_code == 403, path
    assert c.get("/static/icons/../js/main.js").status_code in (403, 404)
