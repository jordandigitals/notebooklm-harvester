"""Local technical log with a redaction filter: no secrets, no cookie names (R10)."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from nlh import paths

_PATTERNS = [
    # Cookie names Google uses for sign-in, and any __Secure- or __Host- cookie.
    re.compile(r"__(?:Secure|Host)-[A-Za-z0-9_-]+"),
    re.compile(r"\b[A-Z0-9]*(?:SID|SIDTS|SIDCC)[A-Z0-9_-]*\b"),
    re.compile(r"\b(?:NID|AEC|OTZ|SOCS)\b"),
    # Anything that looks like a key=value secret or a long opaque token.
    re.compile(r"(?i)(cookie|token|authorization|csrf|session)[\"']?\s*[:=]\s*[\"']?[^\s,;\"']+"),
    re.compile(r"[A-Za-z0-9_\-./+=]{32,}"),
]


def redact(text: str) -> str:
    """Remove cookie names, token-like strings and key=value secrets from text."""
    for pattern in _PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


class RedactFilter(logging.Filter):
    """Logging filter that redacts every formatted message before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


_configured = False


def get_logger() -> logging.Logger:
    """Return the app logger, writing to <home>/logs/nlh.log with redaction."""
    global _configured
    logger = logging.getLogger("nlh")
    if not _configured:
        handler = RotatingFileHandler(
            paths.log_dir() / "nlh.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        # Keep the library's own logging quiet; it never reaches our file.
        logging.getLogger("notebooklm").setLevel(logging.ERROR)
        _configured = True
    return logger
