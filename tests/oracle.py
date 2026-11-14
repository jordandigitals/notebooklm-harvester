"""Independent Python version of the page's search, filter and sort rules (nlh/static/js/search.js).

UI tests compute the expected answer here from the server's rows and compare it with what
the page shows. Written separately from the JavaScript on purpose.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta

STATUS_WORDS = {
    "new": "new",
    "saved": "saved",
    "missing": "saved_but_missing",
    "unreachable": "folder_unreachable",
    "offline": "folder_unreachable",
    "notfinished": "in_progress",
    "unfinished": "in_progress",
    "inprogress": "in_progress",
    "failed": "failed",
}
_TOKEN = re.compile(r'(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)')


def fold(text) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not ("̀" <= ch <= "ͯ")).lower()


def parse(query: str) -> dict:
    out = {"words": [], "account": [], "notebook": [], "status": [], "before": None, "after": None}
    for m in _TOKEN.finditer(query or ""):
        key = (m.group(1) or m.group(3) or "").lower()
        val = m.group(2) if m.group(2) is not None else m.group(4)
        if key in ("account", "notebook", "status", "before", "after"):
            if key in ("before", "after"):
                if val and re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
                    out[key] = val
            elif key == "status":
                s = STATUS_WORDS.get(re.sub(r"[^a-z]", "", fold(val)))
                if s:
                    out["status"].append(s)
            elif val:
                out[key].append(fold(val))
            continue
        word = m.group(5) if m.group(5) is not None else (m.group(6) or f"{m.group(1) or m.group(3)}:{val}")
        f = fold(word).strip()
        if f:
            out["words"].append(f)
    return out


def hay(row: dict, acct: dict | None) -> str:
    parts = [
        row["title"],
        row["notebook"],
        acct["label"] if acct else "",
        (acct or {}).get("email") or "",
        row.get("file_name") or "",
        row.get("saved_path") or "",
        (row.get("created_at") or "")[:10],
        (row.get("saved_at") or "")[:10],
        row["artifact_id"],
        row["notebook_id"],
        row["status"],
    ]
    return fold(" \u0001 ".join(str(p) for p in parts))


def match_query(row, acct, q) -> bool:
    h = hay(row, acct)
    ah = fold(f"{acct['label']} {acct.get('email') or ''}" if acct else "")
    if any(w not in h for w in q["words"]):
        return False
    if any(a not in ah for a in q["account"]):
        return False
    if q["notebook"] and any(n not in fold(row["notebook"]) for n in q["notebook"]):
        return False
    if q["status"] and row["status"] not in q["status"]:
        return False
    created = (row.get("created_at") or "")[:10]
    if q["before"] and not (created and created < q["before"]):
        return False
    if q["after"] and not (created and created > q["after"]):
        return False
    return True


def preset_start(preset: str, now: datetime) -> str:
    if preset == "today":
        return now.strftime("%Y-%m-%d")
    if preset == "7d":
        return (now - timedelta(days=6)).strftime("%Y-%m-%d")
    if preset == "30d":
        return (now - timedelta(days=29)).strftime("%Y-%m-%d")
    if preset == "year":
        return f"{now.year}-01-01"
    raise ValueError(preset)


def in_range(date, spec, now) -> bool:
    if not spec:
        return True
    if not date:
        return False
    day = date[:10]
    if isinstance(spec, str):
        return day >= preset_start(spec, now)
    if spec.get("from") and day < spec["from"]:
        return False
    if spec.get("to") and day > spec["to"]:
        return False
    return True


def match_filters(row, f, now) -> bool:
    if f.get("status") and row["status"] not in f["status"]:
        return False
    if f.get("account") and row["account_id"] not in f["account"]:
        return False
    if f.get("notebook") and row["notebook_id"] not in f["notebook"]:
        return False
    if not in_range(row.get("created_at"), f.get("created"), now):
        return False
    if f.get("saved") and not in_range(row.get("saved_at"), f["saved"], now):
        return False
    if f.get("multi") and not row["multi"]:
        return False
    if f.get("clash") and not row["clash"]:
        return False
    if f.get("moved") and row.get("origin") not in ("relinked", "scan"):
        return False
    if f.get("include") and not all(w in fold(row["title"]) for w in fold(f["include"]).split()):
        return False
    if f.get("exclude") and any(w in fold(row["title"]) for w in fold(f["exclude"]).split()):
        return False
    return True


def _u16(s: str) -> bytes:
    return s.encode("utf-16-be")


def sort_rows(rows, sort):
    rows = sorted(rows, key=lambda r: r["artifact_id"])
    keys = {
        "oldest": (lambda r: r.get("created_at") or "", False),
        "title_asc": (lambda r: _u16(fold(r["title"])), False),
        "title_desc": (lambda r: _u16(fold(r["title"])), True),
        "saved": (lambda r: r.get("saved_at") or "", True),
        "size": (lambda r: r["size_bytes"] if r.get("size_bytes") is not None else -1, True),
        "length": (lambda r: r["duration_s"] if r.get("duration_s") is not None else -1, True),
    }
    fn, rev = keys.get(sort, (lambda r: r.get("created_at") or "", True))
    return sorted(rows, key=fn, reverse=rev)


def expected(data: dict, query="", filters=None, scope=None, sort="newest") -> list[str]:
    """Artifact ids the page should show, in order."""
    now = datetime.now(UTC)
    accts = {a["id"]: a for a in data["accounts"]}
    q = parse(query)
    f = filters or {}
    rows = [
        r
        for r in data["rows"]
        if (scope is None or r["account_id"] == scope)
        and match_query(r, accts.get(r["account_id"]), q)
        and match_filters(r, f, now)
    ]
    return [r["artifact_id"] for r in sort_rows(rows, sort)]
