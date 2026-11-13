"""Adapter pieces that run without a real session, and log redaction."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from nlh import adapter
from nlh.log import redact


def test_chrome_profiles_names_and_emails_only(tmp_path):
    ls = tmp_path / "Local State"
    ls.write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Your Chrome", "user_name": ""},
                        "Profile 2": {"name": "Jordan", "user_name": "j@example.com"},
                        "Profile 1": {"name": "Other", "user_name": "o@example.com"},
                    }
                }
            }
        )
    )
    got = adapter.list_chrome_profiles(ls)
    assert [(p.folder, p.name, p.email) for p in got] == [
        ("Profile 1", "Other", "o@example.com"),
        ("Profile 2", "Jordan", "j@example.com"),
    ]


def test_backoff_retries_rate_limit_then_succeeds():
    calls, sleeps = [], []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 Too Many Requests")
        return "ok"

    async def fake_sleep(s):
        sleeps.append(s)

    assert asyncio.run(adapter._with_backoff(call, sleep=fake_sleep)) == "ok"
    assert sleeps == [4, 8]


def test_backoff_does_not_retry_other_errors():
    async def call():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(adapter._with_backoff(call, sleep=lambda s: None))


def test_overview_mapping():
    nb = adapter.Notebook("nb", "Notebook", None)
    a = SimpleNamespace(id="a1", title="", status=3, created_at=None)
    ov = adapter._overview(a, nb)
    assert ov.title == "Notebook" and ov.completed
    a2 = SimpleNamespace(id="a2", title="T", status=2, created_at=None)
    assert adapter._overview(a2, nb).status == "in_progress"


def test_check_session_without_file(home):
    st = adapter.check_session("nobody")
    assert st.state == "needs_sign_in"


def test_redact_removes_cookie_names_and_tokens():
    text = "cookies SID HSID __Secure-1PSID SAPISID NID token=abc123 " + "x" * 40
    out = redact(text)
    for bad in ("SID", "__Secure", "SAPISID", "NID", "abc123", "x" * 40):
        assert bad not in out


def test_redact_keeps_uuids_and_paths():
    line = "saved artifact f9dd4176-ac01-49f8-bd2c-77b8876b04a8 to /Users/someone/Music/Long Name/T.mp3"
    assert redact(line) == line
