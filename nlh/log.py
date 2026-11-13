"""Local technical log with a redaction filter: no secrets, no cookie names (R10)."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from nlh import paths

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

_COOKIE_NAME = (
    r"(?:__(?:Secure|Host)-[A-Za-z0-9_-]+"
    r"|\b[A-Z0-9]*(?:SID|SIDTS|SIDCC)[A-Z0-9_-]*\b"
    r"|\b(?:NID|AEC|OTZ|SOCS)\b)"
)
# A slash-joined token that looks random (long parts with digits and capitals), such as
# the SAPISID family's "xxxx/yyyy" values. File paths have short or lower-case parts.
_SLASH_TOKEN = re.compile(r"[A-Za-z0-9_\-+=]{10,}(?:/[A-Za-z0-9_\-+=]{6,})+")

_PATTERNS = [
    # A cookie's value after its name (name=value or "name": "value"), then JSON value fields.
    re.compile(_COOKIE_NAME + r"[\"']?\s*[:=]\s*[\"']?[^\s,;\"']+"),
    re.compile(r"(?i)[\"']value[\"']\s*:\s*[\"'][^\"']*[\"']"),
    # Cookie names Google uses for sign-in, and any __Secure- or __Host- cookie.
    re.compile(r"__(?:Secure|Host)-[A-Za-z0-9_-]+"),
    re.compile(r"\b[A-Z0-9]*(?:SID|SIDTS|SIDCC)[A-Z0-9_-]*\b"),
    re.compile(r"\b(?:NID|AEC|OTZ|SOCS)\b"),
    # Anything that looks like a key=value secret.
    re.compile(r"(?i)(cookie|token|authorization|csrf|session)[\"']?\s*[:=]\s*[\"']?[^\s,;\"']+"),
    # Long opaque tokens (cookie values, base64). Path separators and dots are not part of
    # the class, so file paths survive; UUIDs are protected separately.
    re.compile(r"[A-Za-z0-9_\-+=]{32,}"),
]


def _slash_token(m: re.Match) -> str:
    """Redact a slash-joined token only when it looks random (digits and capitals)."""
    token = m.group(0)
    looks_random = any(c.isdigit() for c in token) and any(c.isupper() for c in token)
    return "[redacted]" if looks_random else token


def redact(text: str) -> str:
    """Remove cookie names, token-like strings and key=value secrets from text.

    Notebook and artifact ids (UUIDs) are kept so the log stays useful.
    """
    kept: list[str] = []

    def hold(m: re.Match) -> str:
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = _UUID.sub(hold, text)
    text = _SLASH_TOKEN.sub(_slash_token, text)
    for pattern in _PATTERNS:
        text = pattern.sub("[redacted]", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


class RedactFilter(logging.Filter):
    """Logging filter that redacts every formatted message before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the whole formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_configured = False


def get_logger() -> logging.Logger:
    """Return the app logger, writing to <home>/logs/nlh.log with redaction."""
    global _configured
    logger = logging.getLogger("nlh")
    if not _configured:
        handler = RotatingFileHandler(
            paths.log_dir() / "nlh.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        # Keep the library's own logging quiet; it never reaches our file.
        logging.getLogger("notebooklm").setLevel(logging.ERROR)
        _configured = True
    return logger
