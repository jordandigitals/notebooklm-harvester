"""Demo mode (NLH_DEMO=1): synthetic accounts, notebooks and overviews, no network.

Used for every screenshot, UI test and relay evidence (the repo is public, so no real
titles, emails or ids may appear anywhere). Demo mode:
- points the app home at a fresh temporary folder (never the real ledger or sessions);
- seeds the ledger with generated accounts, listings, saved files, failures and gaps;
- replaces the adapter's functions with fakes that generate audio locally with ffmpeg.

generate() is pure and deterministic, so tests can compute expected answers from the
same data independently of the page.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ENV_FLAG = "NLH_DEMO"
ROWS_ENV = "NLH_DEMO_ROWS"

ACCOUNTS = [
    {
        "profile": "demo-work",
        "label": "Work account",
        "email": "demo.work@example.com",
        "chrome": "Profile 2",
    },
    {
        "profile": "demo-home",
        "label": "Home account",
        "email": "demo.home@example.com",
        "chrome": "Profile 5",
    },
    {"profile": "demo-travel", "label": "Travel drive", "email": "demo.travel@example.com", "chrome": None},
]

_WORDS = (
    "garden soil compost seasons orchard printing typography europe river trade maps "
    "astronomy telescope comets climate cities bridges railways music harmony rhythm "
    "nutrition sleep memory language grammar poetry history empire coast tides ocean "
    "volcano glacier forest birds insects bees honey bread coffee tea spices markets "
    "startup pricing referral growth design colour light shadow chess strategy"
).split()
_ACCENTED = ["Café", "Señora", "Crème brûlée", "Zürich", "Ångström", "Fiancée", "Naïve", "São Paulo"]
_EMOJI = ["🌱", "🎧", "📚", "🚀", "🌊"]
BASE_DATE = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class DemoRow:
    """One synthetic overview with its intended state."""

    account: int  # index into ACCOUNTS
    notebook_id: str
    notebook_title: str
    artifact_id: str
    title: str
    created_at: str
    status: str  # new | saved | saved_but_missing | folder_unreachable | in_progress | failed
    origin: str = "harvest"
    clash: bool = False
    saved_days_ago: int | None = None
    size_bytes: int = 0
    duration_s: float = 0.0


def enabled() -> bool:
    """True when demo mode is switched on."""
    return os.environ.get(ENV_FLAG) == "1"


def row_count() -> int:
    """How many overviews to generate (NLH_DEMO_ROWS, default 2,000)."""
    try:
        return max(10, min(int(os.environ.get(ROWS_ENV, "2000")), 20000))
    except ValueError:
        return 2000


def _title(rng: random.Random, n: int) -> str:
    words = [rng.choice(_WORDS) for _ in range(rng.randint(3, 7))]
    title = " ".join(words).capitalize()
    roll = n % 23
    if roll == 0:
        title = f"{rng.choice(_ACCENTED)} and the {title.lower()}"
    elif roll == 5:
        title = f"{title} {rng.choice(_EMOJI)}"
    elif roll == 11:
        title = (title + " ") * 6  # long title
        title = title.strip()
    return title


def generate(n: int | None = None, seed: int = 42) -> list[DemoRow]:
    """Deterministic synthetic library of n overviews across the demo accounts."""
    n = n or row_count()
    rng = random.Random(seed)
    rows: list[DemoRow] = []
    nb_count = max(3, n // 4)
    notebooks = []
    for i in range(nb_count):
        title = _title(rng, i + 7).title()
        if i % 40 == 3 and notebooks:
            title = notebooks[-1][1]  # two notebooks with the same name
        notebooks.append((f"demo-nb-{i:05d}", title[:90], i % len(ACCOUNTS)))
    statuses = (
        ["new"] * 60 + ["saved"] * 25 + ["saved_but_missing"] * 3 + ["in_progress"] * 4 + ["failed"] * 4
    )
    for k in range(n):
        nb_id, nb_title, acct = notebooks[k % nb_count] if k < nb_count else rng.choice(notebooks)
        created = BASE_DATE - timedelta(days=rng.randint(0, 420), minutes=rng.randint(0, 1440))
        status = rng.choice(statuses)
        if acct == 2 and status in ("saved", "saved_but_missing"):
            status = "folder_unreachable"  # the travel account's drive is unplugged
        title = _title(rng, k)
        if k % 97 == 13 and rows:
            title = rows[-1].title  # duplicate overview title in another place
        saved_days = (
            rng.randint(0, 60) if status in ("saved", "saved_but_missing", "folder_unreachable") else None
        )
        origin = "harvest"
        if status == "saved" and k % 29 == 0:
            origin = "relinked"
        elif status == "saved" and k % 31 == 0:
            origin = "scan"
        rows.append(
            DemoRow(
                account=acct,
                notebook_id=nb_id,
                notebook_title=nb_title,
                artifact_id=f"demo-art-{k:06d}",
                title=title,
                created_at=created.replace(microsecond=0).isoformat(),
                status=status,
                origin=origin,
                clash=status == "saved" and k % 17 == 0,
                saved_days_ago=saved_days,
                size_bytes=rng.randint(4_000_000, 60_000_000) if saved_days is not None else 0,
                duration_s=round(rng.uniform(300, 3900), 2) if saved_days is not None else 0.0,
            )
        )
    return rows


# ---------------------------------------------------------------- activation


def activate() -> Path | None:
    """Switch demo mode on before anything opens the app home. Returns the demo home."""
    if not enabled():
        return None
    home = Path(os.environ.get("NLH_DEMO_HOME") or tempfile.mkdtemp(prefix="nlh-demo-"))
    os.environ["NLH_HOME"] = str(home)
    return home


def _tone(dest: Path, seconds: float = 3.0) -> None:
    """A short generated tone as M4A (stands in for a downloaded overview)."""
    from nlh import media

    subprocess.run(
        [
            media.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:duration={seconds}",
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(dest),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def seed(ledger, rows: list[DemoRow] | None = None) -> None:
    """Fill a fresh demo ledger: accounts, listings, saved files and attempts."""
    from nlh import adapter, media, naming, paths
    from nlh.server import _dump_listing

    rows = rows if rows is not None else generate()
    home = paths.app_home()
    outputs = [
        home / "demo-output" / "Work",
        home / "demo-output" / "Home",
        home / "unplugged-drive" / "Travel",
    ]
    outputs[0].mkdir(parents=True, exist_ok=True)
    outputs[1].mkdir(parents=True, exist_ok=True)
    sample = home / "demo-sample.mp3"
    tone = home / "demo-tone.m4a"
    _tone(tone, 3.0)
    media.convert_to_mp3(tone, sample, media.build_tags("Demo", "Demo", None, "demo", "demo"))
    accts = []
    for i, a in enumerate(ACCOUNTS):
        acct = ledger.add_account(a["profile"], a["label"], a["email"], a["chrome"])
        ledger.update_account(
            acct.id, output_dir=str(outputs[i]), last_status="ok" if i != 1 else "needs_sign_in"
        )
        accts.append(acct)
    by_account: dict[int, dict[str, tuple]] = {}
    for r in rows:
        nbs = by_account.setdefault(r.account, {})
        nb, ovs = nbs.setdefault(r.notebook_id, (adapter.Notebook(r.notebook_id, r.notebook_title, None), []))
        status = "in_progress" if r.status == "in_progress" else "completed"
        ovs.append(
            adapter.AudioOverview(
                r.artifact_id, r.title, r.notebook_id, r.notebook_title, r.created_at, status
            )
        )
    for i, acct in enumerate(accts):
        listing = list(by_account.get(i, {}).values())
        ledger.save_listing(acct.id, _dump_listing(listing))
    for r in rows:
        acct = accts[r.account]
        if r.status in ("saved", "saved_but_missing", "folder_unreachable"):
            folder = outputs[r.account] / naming.folder_name(r.notebook_title)
            name = naming.safe_stem(r.title) + (" (2)" if r.clash else "") + ".mp3"
            path = folder / name
            if r.status == "saved":
                folder.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    shutil.copyfile(sample, path)
            saved_at = (BASE_DATE - timedelta(days=r.saved_days_ago or 0)).replace(microsecond=0).isoformat()
            with ledger.transaction() as c:
                ledger.record(
                    c,
                    account_id=acct.id,
                    notebook_id=r.notebook_id,
                    artifact_id=r.artifact_id,
                    overview_title=r.title,
                    notebook_title=r.notebook_title,
                    created_at=r.created_at,
                    saved_path=str(path),
                    size_bytes=r.size_bytes,
                    duration_s=r.duration_s,
                    sha256="demo",
                    origin=r.origin,
                )
                c.execute("UPDATE harvests SET saved_at = ? WHERE artifact_id = ?", (saved_at, r.artifact_id))
            ledger.record_attempt(
                acct.id, r.artifact_id, "saved", "Saved.", 12.0 + (hash(r.artifact_id) % 20), 40.0
            )
        elif r.status == "failed":
            ledger.record_attempt(acct.id, r.artifact_id, "failed", "Could not reach NotebookLM.", None, 5.0)


# Demo plan answers in the shapes seen in the D-024 spike and their likely variants
# (synthetic text only; the repo is public).
PLAN_SHAPES = [
    # 0: bold count, numbered list, Focus lines, citations
    "Based on the sources, I recommend **3 long audio overviews** so nothing is skipped [1].\n\n"
    "1. **Soil and What Lives In It**\n"
    "   * **Focus:** What soil is made of, how worms and fungi keep it alive, and simple tests [1, 2].\n"
    "   * **Key topics:** texture, organic matter, drainage.\n"
    "2. **Compost From Start to Finish**\n"
    "   * **Focus:** Building a heap, the browns and greens balance, turning and timing [3].\n"
    "3. **Planting Through the Seasons**\n"
    "   * **Focus:** A year in the garden: sowing, mulching, watering and putting beds to rest [4].\n",
    # 1: headings with "Overview n:" and plain Focus lines, no count
    "### Overview 1: How Railways Began\nFocus: early lines, the first engines and who paid for them.\n\n"
    "### Overview 2: Bridges and Tunnels\nFocus: the engineering that crossed rivers and hills.\n",
    # 2: a count in words and a plain numbered list, focus as bullets under some items
    "This notebook splits naturally into four overviews.\n"
    "1. Tides and the moon\n   - why there are two tides a day\n   - spring and neap tides\n"
    "2. Coastal erosion\n3. Life in rock pools\n4. Reading a tide table\n",
    # 3: no list at all
    "The sources are short and closely linked, so one overview would cover them well. "
    "You could ask it to focus on how harmony and rhythm work together.",
]


def install_fake_adapter() -> None:
    """Replace the adapter's network functions with local fakes (demo mode only)."""
    from nlh import adapter, paths
    from nlh.ledger import Ledger

    tone_cache = {}

    def list_library(profile_name, on_progress=None):
        led = Ledger()
        acct = led.account_by_profile(profile_name)
        saved = led.load_listing(acct.id) if acct else None
        led.close()
        from nlh.server import _load_listing

        return _load_listing(saved[0]) if saved else []

    def download_overview(profile_name, overview, dest):
        if "glacier" in overview.title.lower():
            # A predictable failure so the queue's failed state and Retry can be seen and tested.
            raise adapter.AdapterError("Could not reach NotebookLM (demo failure for titles with glacier).")
        if "src" not in tone_cache:
            src = paths.app_home() / "demo-download.m4a"
            _tone(src, 4.0)
            tone_cache["src"] = src
        shutil.copyfile(tone_cache["src"], dest)
        return dest

    def ask_for_plan(profile_name, notebook_id, question):
        # Four recorded answer shapes, picked by notebook so tests can predict them.
        import time as _t

        _t.sleep(float(os.environ.get("NLH_DEMO_ASK_S", "1.2")))
        shape = sum(map(ord, notebook_id)) % 4
        return adapter.PlanAnswer(PLAN_SHAPES[shape])

    generated: dict[str, dict] = {}  # task id -> {notebook_id, title, started}

    def generate_audio(
        profile_name, notebook_id, focus, audio_format="deep_dive", audio_length="default", language="en"
    ):
        # Focus text containing "limit" stands in for Google's usage-limit refusal.
        if "limit" in (focus or "").lower():
            raise adapter.AdapterError(
                "NotebookLM refused because of a usage limit. Studio stops for this account today.", "limit"
            )
        task = f"demo-gen-{len(generated) + 1:04d}-{abs(hash((notebook_id, focus))) % 10000:04d}"
        title = (focus or "Generated overview").split(".")[0][:80].strip() or "Generated overview"
        generated[task] = {
            "notebook_id": notebook_id,
            "title": title,
            "started": __import__("time").monotonic(),
        }
        return adapter.Submitted(task, "pending")

    def list_audio_overviews(profile_name, notebook):
        import time as _t

        gen_s = float(os.environ.get("NLH_DEMO_GEN_S", "6"))
        out = []
        for task, g in generated.items():
            if g["notebook_id"] != notebook.id:
                continue
            done = _t.monotonic() - g["started"] >= gen_s
            out.append(
                adapter.AudioOverview(
                    task,
                    g["title"] if done else notebook.title,  # the real title arrives at completion
                    notebook.id,
                    notebook.title,
                    datetime.now(UTC).replace(microsecond=0).isoformat(),
                    "completed" if done else "in_progress",
                )
            )
        return out

    adapter.ask_for_plan = ask_for_plan
    adapter.generate_audio = generate_audio
    adapter.list_audio_overviews = list_audio_overviews
    adapter.list_library = list_library
    adapter.download_overview = download_overview
    adapter.check_session = lambda name: adapter.SessionStatus("ok", "Signed in (demo).", None)
    adapter.login_from_chrome = lambda *a, **k: adapter.SessionStatus("ok", "Signed in (demo).", None)
    adapter.login_interactive = lambda *a, **k: adapter.SessionStatus(
        "ok", "Signed in (demo).", "demo.new@example.com"
    )
    adapter.list_chrome_profiles = lambda *a: (
        [adapter.ChromeProfile(a["chrome"] or "Profile 9", a["label"], a["email"]) for a in ACCOUNTS]
        + [adapter.ChromeProfile("Profile 7", "Side project", "demo.side@example.com")]
    )


def prepare() -> None:
    """Activate, seed (once per demo home) and install the fakes."""
    from nlh.ledger import Ledger

    activate()
    led = Ledger()
    try:
        if not led.accounts():
            seed(led)
    finally:
        led.close()
    install_fake_adapter()
