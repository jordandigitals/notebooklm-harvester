"""Local settings and saved views, stored in <app home>/settings.json (no secrets).

Every key is allow-listed with its type and limits, so the page can never write anything
else into the file. Writes are atomic (temp file, then replace) and owner-only on Mac and
Linux.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

from nlh import paths

THEMES = ("auto", "light", "dark")
SORTS = (
    "newest",
    "oldest",
    "title_asc",
    "title_desc",
    "notebook",
    "status",
    "saved",
    "size",
    "length",
)
GROUPS = ("none", "notebook", "account", "month", "status")
PAGE_SIZES = (50, 100, 250)
MAX_VIEWS = 50
MAX_TEXT = 500
TEMPLATE_TOKENS = ("{notebook}", "{title}", "{date}", "{account}")

DEFAULT_TEMPLATE = "{notebook}/{title}"

# Shipped views (not written to the file until the user changes the list).
DEFAULT_VIEWS = [
    {
        "name": "New to harvest",
        "query": "",
        "filters": {"status": ["new"]},
        "sort": "newest",
        "group": "none",
    },
    {
        "name": "Needs attention",
        "query": "",
        "filters": {"status": ["saved_but_missing", "folder_unreachable", "failed"]},
        "sort": "newest",
        "group": "status",
    },
    {
        "name": "Saved this week",
        "query": "",
        "filters": {"status": ["saved"], "saved": "7d"},
        "sort": "saved",
        "group": "none",
    },
]

DEFAULTS = {
    "theme": "auto",
    "naming_template": DEFAULT_TEMPLATE,
    "default_output": "",
    "default_view": "",
    "notifications": False,
    "sort": "newest",
    "group": "none",
    "history_page_size": 50,
    "views": None,  # None means "use DEFAULT_VIEWS"
    # Studio (D-025)
    "studio_daily_cap": 4,  # generated overviews per account per local day
    "studio_pause_s": 45,  # pause between generation requests
    "studio_shared_accounts": [],  # account ids marked "used by other automated tools"
    "studio_ask_warned": False,  # the one-time warning about chat messages was shown
    "studio_language": "en",
}
STUDIO_CAP_RANGE = (1, 20)
STUDIO_PAUSE_RANGE = (30, 300)

_lock = threading.Lock()


class SettingsError(ValueError):
    """A setting the page sent is not allowed; the message is plain language."""


def _file() -> Path:
    return paths.app_home() / "settings.json"


def load() -> dict:
    """Current settings merged over the defaults; a damaged file falls back to defaults."""
    data = dict(DEFAULTS)
    try:
        stored = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored:
                    data[key] = stored[key]
    except (OSError, ValueError):
        pass
    if data["views"] is None:
        data["views"] = [dict(v) for v in DEFAULT_VIEWS]
    return data


def _text(value, name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise SettingsError(f"{name} must be text of at most {limit} characters.")
    return value


def validate_template(template: str) -> str:
    """A naming template: at most one folder level plus the file, known tokens only, needs {title}."""
    template = _text(template, "The naming template", 120).strip()
    if "{title}" not in template:
        raise SettingsError("The naming template must include {title}.")
    if template.count("/") > 1 or "\\" in template or template.startswith("/") or ".." in template:
        raise SettingsError("The naming template may have one folder level, written as folder/file.")
    leftover = template
    for token in TEMPLATE_TOKENS:
        leftover = leftover.replace(token, "")
    if "{" in leftover or "}" in leftover:
        raise SettingsError("Use only {notebook}, {title}, {date} and {account} in the template.")
    return template


def _view(v) -> dict:
    if not isinstance(v, dict):
        raise SettingsError("A saved view is not valid.")
    name = _text(v.get("name", ""), "A view name", 60).strip()
    if not name:
        raise SettingsError("A saved view needs a name.")
    filters = v.get("filters") or {}
    if not isinstance(filters, dict) or len(json.dumps(filters)) > 4000:
        raise SettingsError("A saved view's filters are not valid.")
    sort = v.get("sort", "newest")
    group = v.get("group", "none")
    if sort not in SORTS or group not in GROUPS:
        raise SettingsError("A saved view's sort or grouping is not valid.")
    return {
        "name": name,
        "query": _text(v.get("query", ""), "A view search"),
        "filters": filters,
        "sort": sort,
        "group": group,
    }


def validate(update: dict) -> dict:
    """Check a partial update from the page; returns the cleaned values."""
    if not isinstance(update, dict):
        raise SettingsError("Settings must be an object.")
    clean: dict = {}
    for key, value in update.items():
        if key not in DEFAULTS:
            raise SettingsError(f"Unknown setting: {key}.")
        if key == "theme":
            if value not in THEMES:
                raise SettingsError("Theme must be auto, light or dark.")
        elif key == "naming_template":
            value = validate_template(value)
        elif key in ("default_output", "default_view"):
            value = _text(value, key)
        elif key == "notifications":
            if not isinstance(value, bool):
                raise SettingsError("Notifications must be on or off.")
        elif key == "sort":
            if value not in SORTS:
                raise SettingsError("Unknown sort.")
        elif key == "group":
            if value not in GROUPS:
                raise SettingsError("Unknown grouping.")
        elif key == "history_page_size":
            if value not in PAGE_SIZES:
                raise SettingsError("Page size must be 50, 100 or 250.")
        elif key == "studio_daily_cap":
            lo, hi = STUDIO_CAP_RANGE
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise SettingsError(f"The daily Studio limit must be a whole number from {lo} to {hi}.")
        elif key == "studio_pause_s":
            lo, hi = STUDIO_PAUSE_RANGE
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise SettingsError(f"The pause between requests must be {lo} to {hi} seconds.")
        elif key == "studio_shared_accounts":
            if (
                not isinstance(value, list)
                or len(value) > 50
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
            ):
                raise SettingsError("The list of shared accounts is not valid.")
            value = sorted(set(value))
        elif key == "studio_ask_warned":
            if not isinstance(value, bool):
                raise SettingsError("That setting must be on or off.")
        elif key == "studio_language":
            value = _text(value, "The language", 12).strip()
            if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,4})?", value):
                raise SettingsError("Use a language code such as en, fr or pt-BR.")
        elif key == "views":
            if not isinstance(value, list) or len(value) > MAX_VIEWS:
                raise SettingsError(f"At most {MAX_VIEWS} saved views.")
            value = [_view(v) for v in value]
            names = [v["name"].casefold() for v in value]
            if len(set(names)) != len(names):
                raise SettingsError("Two saved views have the same name.")
        clean[key] = value
    return clean


def save(update: dict) -> dict:
    """Validate and store a partial update atomically; returns the full settings."""
    clean = validate(update)
    with _lock:
        current = load()
        current.update(clean)
        target = _file()
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    return current
