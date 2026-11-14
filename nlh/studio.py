"""Studio (D-025): turn a notebook's plan answer into editable items, and keep the daily cap.

Nothing here talks to NotebookLM. The adapter's StudioClient is the only write path; this
module holds the pure parts so they can be tested on recorded answer shapes:

- parse_plan(): best-effort reading of a free-text plan. The answer's wording varies
  between asks, so the parser is tolerant and the page always shows the raw text with
  every row editable, addable and removable. Nothing is ever auto-approved.
- daily cap: counted per account per local calendar day, on submit (not on success), so
  a crash can never undercount.
"""

from __future__ import annotations

import re
from datetime import datetime

DEFAULT_QUESTION = (
    "I want to turn this notebook into a series of long audio overviews that together cover "
    "all of its sources without overlap. How many overviews do you suggest? Start with the "
    "number in bold, then give a numbered list. For each overview put a short title on the "
    'numbered line, then a line starting with "Focus:" that says exactly what it should cover.'
)

MAX_ITEMS = 20
MAX_TITLE = 200
MAX_FOCUS = 2000

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_NUM = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_COUNT_BOLD = re.compile(r"\*\*[^*\n]*?\b" + _NUM + r"\b[^*\n]*?\*\*", re.I)
_COUNT_PLAIN = re.compile(
    r"\b" + _NUM + r"\s+(?:long\s+|separate\s+|audio\s+|deep[- ]dive\s+)*overviews?\b", re.I
)
# A numbered line: "1. Title", "1) Title", "**1. Title**", "### 1. Title", "Overview 1: Title",
# "**Overview 2 - Title**". The number is captured with the line's indentation.
_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:(?:audio\s+)?overview\s+|part\s+|episode\s+)?"
    r"(?P<n>\d{1,2})\s*(?:[.):]|\s-\s|\s:\s)\s*(?P<rest>.*)$",
    re.I,
)
_FOCUS = re.compile(
    r"^[ \t]*(?:[-*+]\s*)?(?:\*\*|__)?\s*focus\s*(?:\*\*|__)?\s*[:\-]\s*(?:\*\*|__)?\s*(?P<f>.*)$", re.I
)
_CITATION = re.compile(r"\s*\[\d+(?:\s*[,-]\s*\d+)*\]")
_BULLET = re.compile(r"^[ \t]*(?:[-*+]|\d{1,2}[.)])\s+(?P<b>.+)$")


def _clean(text: str) -> str:
    """Drop markdown emphasis, heading marks, citations and stray separators."""
    text = _CITATION.sub("", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = text.strip().strip(":").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _number(word: str) -> int | None:
    word = word.lower()
    if word.isdigit():
        return int(word)
    return _NUMBER_WORDS.get(word)


def parse_count(text: str) -> int | None:
    """The suggested number of overviews: a bold phrase with a number, else "<n> overviews"."""
    for pat in (_COUNT_BOLD, _COUNT_PLAIN):
        for m in pat.finditer(text or ""):
            n = _number(m.group(1))
            if n and 0 < n <= MAX_ITEMS:
                return n
    return None


def parse_plan(text: str) -> dict:
    """Read a plan answer into {count, items: [{title, focus}], method}.

    method is "numbered_focus" (numbered items with Focus lines), "numbered" (numbered items,
    focus taken from the lines under each), or "none" (no list found: Jordan types rows in).
    """
    lines = (text or "").splitlines()
    count = parse_count(text or "")
    starts: list[tuple[int, int, str]] = []  # (line index, indent, rest)
    level = None
    expected = 1
    for i, line in enumerate(lines):
        m = _ITEM.match(line)
        if not m:
            continue
        indent = len(m.group("indent").expandtabs(4))
        n = int(m.group("n"))
        if level is None:
            if n != 1:
                continue
            level = indent
        if indent != level or n != expected:
            continue  # a nested list inside an item, or numbers that are not the plan's list
        starts.append((i, indent, m.group("rest")))
        expected += 1
        if len(starts) >= MAX_ITEMS:
            break
    items = []
    with_focus = 0
    for k, (i, _indent, rest) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        body = lines[i + 1 : end]
        title = _clean(rest)
        focus = ""
        # "Title: focus text" on the numbered line itself, when no Focus line follows.
        inline_focus = ""
        if ":" in title and len(title) > 60:
            head, tail = title.split(":", 1)
            if 3 <= len(head) <= 100:
                title, inline_focus = head.strip(), tail.strip()
        for b in body:
            fm = _FOCUS.match(b)
            if fm:
                focus = _clean(fm.group("f"))
                if not focus:  # "Focus:" alone, text on the following lines
                    after = [_clean(x) for x in body[body.index(b) + 1 :] if x.strip()]
                    focus = after[0] if after else ""
                break
        if focus:
            with_focus += 1
        else:
            bullets = []
            for b in body:
                if not b.strip():
                    if bullets:
                        break
                    continue
                bm = _BULLET.match(b)
                bullets.append(_clean(bm.group("b") if bm else b))
                if len(" ".join(bullets)) > 600:
                    break
            focus = inline_focus or "; ".join(x for x in bullets if x)
        items.append({"title": title[:MAX_TITLE] or f"Overview {k + 1}", "focus": focus[:MAX_FOCUS]})
    if not items:
        method = "none"
    elif with_focus == len(items):
        method = "numbered_focus"
    else:
        method = "numbered"
    return {"count": count, "items": items, "method": method}


def clean_items(items) -> list[dict]:
    """Validate plan rows from the page: 1 to MAX_ITEMS rows, each with a title."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"A plan needs between 1 and {MAX_ITEMS} overviews.")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("A plan row is not valid.")
        title = str(it.get("title") or "").strip()
        focus = str(it.get("focus") or "").strip()
        if not title:
            raise ValueError("Every overview in the plan needs a title.")
        if len(title) > MAX_TITLE or len(focus) > MAX_FOCUS:
            raise ValueError(f"Titles are at most {MAX_TITLE} characters and focus text {MAX_FOCUS}.")
        out.append({"title": title, "focus": focus})
    return out


def local_day(when: datetime | None = None) -> str:
    """The local calendar day as YYYY-MM-DD (the cap resets at local midnight)."""
    return (when or datetime.now()).astimezone().strftime("%Y-%m-%d")
