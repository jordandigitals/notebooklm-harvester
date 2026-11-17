"""Build the NotebookLM Harvester manual (PDF) from the screenshots in docs/manual/shots and
docs/manual/real-phone. Run docs/manual/shots.py first (demo mode only).

Usage (from the repo root):  python -m docs.manual.build_manual [out.pdf]
Letter portrait; images are compressed (JPEG) so the PDF stays small enough for a phone.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHOTS = HERE / "shots"
PHONE = HERE / "real-phone"
sys.path.insert(0, str(ROOT))
from nlh import __version__ as APP_VERSION  # noqa: E402

MANUAL_VERSION = "v3"
BUILT = date.today().strftime("%d %b %Y").lstrip("0")
# Two editions from one source. "public" (the GitHub release) is built from this file alone. "full" is the
# owner's copy: it also needs a private JSON file kept OUTSIDE this public repo (--private PATH, or the
# NLH_MANUAL_PRIVATE environment variable) that holds the owner-only wording, keyed as in own() below.
# Nothing private lives in this repo. Usage:
#   python -m docs.manual.build_manual --public [out.pdf]
#   python -m docs.manual.build_manual --private PATH [out.pdf]
_argv = sys.argv[1:]
PUBLIC = "--public" in _argv
_private_path = os.environ.get("NLH_MANUAL_PRIVATE", "")
if "--private" in _argv:
    _i = _argv.index("--private")
    _private_path = _argv[_i + 1] if _i + 1 < len(_argv) else ""
    del _argv[_i : _i + 2]
_args = [a for a in _argv if a != "--public"]
EDITION = "public" if PUBLIC else "full"
PRIVATE: dict = {}
if not PUBLIC:
    if not _private_path or not Path(_private_path).is_file():
        sys.exit(
            "The full edition needs the owner's private text file (--private PATH or NLH_MANUAL_PRIVATE). "
            "Build the public edition with --public."
        )
    PRIVATE = json.loads(Path(_private_path).read_text(encoding="utf-8"))
OUT = (
    Path(_args[0])
    if _args
    else HERE / ("NotebookLM-Harvester-Manual-public.pdf" if PUBLIC else "NotebookLM-Harvester-Manual.pdf")
)


def own(key, public):
    """The owner's wording for key in the full edition (from the private file), else the public one.

    A missing key falls back to the public wording; None leaves the item out.
    """
    return public if PUBLIC else PRIVATE.get(key, public)


BLUE = colors.HexColor("#1d5bb8")
MARK = colors.HexColor("#e8590c")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#5b6472")
SOFT = colors.HexColor("#eaf1fb")
WARN = colors.HexColor("#fdf1e6")
LINE = colors.HexColor("#d5dbe5")
WIDTH = 6.5 * inch

ss = getSampleStyleSheet()
body = ParagraphStyle(
    "body", parent=ss["Normal"], fontName="Helvetica", fontSize=10.5, leading=15, textColor=INK, spaceAfter=6
)
small = ParagraphStyle("small", parent=body, fontSize=8.8, leading=12, textColor=MUTED)
caption = ParagraphStyle(
    "caption", parent=body, fontSize=9, leading=12, textColor=MUTED, spaceBefore=3, spaceAfter=10
)
h1 = ParagraphStyle(
    "h1",
    parent=body,
    fontName="Helvetica-Bold",
    fontSize=19,
    leading=23,
    textColor=BLUE,
    spaceBefore=2,
    spaceAfter=8,
)
h2 = ParagraphStyle(
    "h2",
    parent=body,
    fontName="Helvetica-Bold",
    fontSize=12.5,
    leading=16,
    textColor=INK,
    spaceBefore=8,
    spaceAfter=4,
    keepWithNext=1,
)
title = ParagraphStyle(
    "title", parent=body, fontName="Helvetica-Bold", fontSize=32, leading=36, textColor=BLUE, spaceAfter=6
)
sub = ParagraphStyle("sub", parent=body, fontSize=13, leading=18, textColor=MUTED, spaceAfter=4)
cell = ParagraphStyle("cell", parent=body, fontSize=9.4, leading=12.8, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
note = ParagraphStyle("note", parent=body, fontSize=9.8, leading=13.8, spaceAfter=0)
stepnum = ParagraphStyle(
    "stepnum",
    parent=body,
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=13,
    textColor=colors.white,
    alignment=1,
    spaceAfter=0,
)


def P(t, s=body):
    return Paragraph(t, s)


def bullets(items):
    return [
        ListFlowable(
            [ListItem(P(i), leftIndent=14, bulletColor=BLUE) for i in items],
            bulletType="bullet",
            start="•",
            leftIndent=14,
            bulletFontSize=9,
        ),
        Spacer(1, 4),
    ]


class Num(Flowable):
    """A small filled circle with a step number (orange when it matches marks in a picture)."""

    def __init__(self, n, color):
        super().__init__()
        self.n, self.color = n, color
        self.width = self.height = 17

    def draw(self):
        c = self.canv
        c.setFillColor(self.color)
        c.circle(8.5, 8.5, 8.5, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawCentredString(8.5, 5.3, str(self.n))


def steps(items, marked=True):
    """Numbered steps. With marked=True the numbers are orange circles that match the picture."""
    rows = [[Num(i, MARK if marked else BLUE), P(t)] for i, t in enumerate(items, 1)]
    t = Table(rows, colWidths=[0.32 * inch, WIDTH - 0.32 * inch])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [t, Spacer(1, 6)]


def box(text, label="Note", kind="info"):
    t = Table([[P("<b>%s.</b> %s" % (label, text), note)]], colWidths=[WIDTH])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT if kind == "info" else WARN),
                ("LINEBEFORE", (0, 0), (0, -1), 3, BLUE if kind == "info" else MARK),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [t, Spacer(1, 8)]


def wrong(text):
    """The short "If something goes wrong" box."""
    return box(text, "If something goes wrong", kind="warn")


def table(rows, widths, header=True):
    data = [[P(c, cellb if (header and ri == 0) else cell) for c in r] for ri, r in enumerate(rows)]
    t = Table(data, colWidths=[w * inch for w in widths], repeatRows=1 if header else 0)
    st = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), SOFT))
    t.setStyle(TableStyle(st))
    return [t, Spacer(1, 8)]


def _jpeg(
    path: Path, crop: tuple[float, float, float, float] | None = None, max_px: int = 1500
) -> tuple[io.BytesIO, int, int]:
    """Compress a screenshot to JPEG in memory (optionally cropping by fractions l, t, r, b)."""
    im = PILImage.open(path).convert("RGB")
    if crop:
        w, h = im.size
        im = im.crop((int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)))
    im.thumbnail((max_px, max_px * 3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78, optimize=True, progressive=True)
    buf.seek(0)
    return buf, im.size[0], im.size[1]


def pic(name, cap, width=1.0, max_h=5.4, folder=SHOTS, crop=None, keep=True, head=None):
    """A screenshot with its one-line caption. width is a fraction of the text width."""
    path = folder / (name if name.endswith(".png") else name + ".png")
    if not path.exists():
        return [P(f"<i>Missing picture: {name}</i>", small)]
    buf, w, h = _jpeg(path, crop)
    dw = WIDTH * width
    dh = dw * h / w
    if dh > max_h * inch:
        dh = max_h * inch
        dw = dh * w / h
    img = Image(buf, width=dw, height=dh)
    img.hAlign = "CENTER"
    frame = Table([[img]], colWidths=[dw + 4])
    frame.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    frame.hAlign = "CENTER"
    parts = ([P(head, h2)] if head else []) + [frame, P(cap, caption)]
    return [KeepTogether(parts)] if keep else parts


def side_by_side(a, b, head=None):
    """Two phone pictures next to each other with captions."""
    cells = []
    for name, cap, folder, crop in (a, b):
        cells.append(pic(name, cap, width=0.44, max_h=4.4, folder=folder, crop=crop, keep=False))
    t = Table([[cells[0], cells[1]]], colWidths=[WIDTH / 2, WIDTH / 2])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return [KeepTogether(([P(head, h2)] if head else []) + [t])]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        inch,
        0.55 * inch,
        f"NotebookLM Harvester manual ({EDITION} edition)  |  app {APP_VERSION}  |  manual {MANUAL_VERSION}, {BUILT}",
    )
    canvas.drawRightString(letter[0] - inch, 0.55 * inch, "Page %d" % doc.page)
    canvas.restoreState()


def build():
    S = []
    # ---------------- cover
    S += [
        Spacer(1, 1.1 * inch),
        P("NotebookLM Harvester", title),
        P("Manual and owner's guide, with pictures", sub),
        Spacer(1, 10),
    ]
    S += [
        P(
            "A personal tool that collects your finished NotebookLM Audio Overviews, converts them to MP3 and saves them in a folder you choose. You can also make new overviews from a plan (Studio) and listen from your phone at home."
        )
    ]
    S += [Spacer(1, 14)]
    S += table(
        [
            [
                "App version",
                APP_VERSION + " when this manual was made (a newer package may be in the folder)",
            ],
            ["Manual version", f"{MANUAL_VERSION} ({EDITION} edition), made {BUILT} from the app itself"],
            [
                "Runs on",
                "Mac (Intel) and Windows 11. The app opens in its own window; it can also open in your browser",
            ],
            ["Built on", "notebooklm-py 0.8.2, an unofficial library (see Limits and risks)"],
        ],
        [1.6, 4.9],
        header=False,
    )
    S += box(
        "Every picture in this manual comes from the app's demo mode: made-up accounts and notebooks that never touch Google. "
        "Your own screens show your accounts and titles instead. Orange numbered circles in a picture match the numbered steps next to it.",
        "About the pictures",
    )
    S += box(
        "Anything not yet tested live is marked <b>untested</b>. Nothing here is shown as working unless it was checked.",
        "Honesty",
    )
    S.append(PageBreak())

    # ---------------- start here
    S += [P("Start here: the five things to do first", h1)]
    S += steps(
        [
            "<b>Start the app.</b> Mac: open <b>NotebookLM Harvester</b> (the first time, right-click it and choose <b>Open</b>). "
            "Windows: open the <b>NotebookLM Harvester</b> folder and double-click <b>NotebookLM Harvester.exe</b>. The app opens in its own window.",
            "<b>Add your Google account.</b> Press <b>Add account</b>, then <b>Continue with Google</b> and sign in once (Section 3).",
            "<b>Choose where files go.</b> Click the account, press <b>Choose folder</b> and pick or make a folder (Section 3).",
            "<b>Read your notebooks.</b> Press <b>Refresh notebooks</b> (or <b>Refresh all accounts</b>). Each overview shows a status such as New or Saved.",
            "<b>Save the new ones.</b> Press <b>Select new</b>, then the blue <b>Harvest</b> button at the bottom. Press Play on any saved row to listen.",
        ],
        marked=False,
    )
    S += pic(
        "01-first-start",
        "The first page: (1) the tabs at the top, (2) Add account, (3) the search box.",
        max_h=4.6,
    )
    S += box(
        "Close the app's window (or press <b>Quit</b> at the top right) when you are done. If a harvest is running, closing asks first and then finishes the current file. "
        "If you chose to open the app in your browser, press <b>Quit</b>: closing only the browser tab leaves the app running.",
        "Stopping",
    )
    S.append(PageBreak())

    # ---------------- contents
    S += [P("Contents", h1)]
    toc = [
        ("1", "What it is and what it is not"),
        ("2", "Installing and starting"),
        ("3", "Adding accounts and choosing a folder"),
        ("4", "The Library: find and harvest"),
        ("5", "Where files go, names and quality"),
        ("6", "History, player and export"),
        ("7", "Stats and Settings"),
        ("8", "Studio: ask for a plan and make overviews"),
        ("9", "Shared with me"),
        ("10", "Listening from your phone"),
        ("11", "Limits, safety rules and risks"),
        ("12", "Status and what is still untested"),
        ("13", "Troubleshooting"),
        ("14", "Quick reference and glossary"),
    ]
    S += table([[n, t] for n, t in toc], [0.5, 6.0], header=False)
    S.append(PageBreak())

    # ---------------- 1 what it is
    S += [P("1. What it is and what it is not", h1)]
    S += [
        P(
            "NotebookLM lets you listen to an Audio Overview in the app or download it by hand. NotebookLM Harvester does the collecting for you: "
            "it lists your notebooks, saves each finished Audio Overview as an MP3 in a folder you choose and remembers what it saved, so nothing is downloaded twice."
        )
    ]
    S += [P("What it does", h2)]
    S += bullets(
        [
            "Lists the notebooks and Audio Overviews on each Google account you add.",
            "Harvests the ones you pick, or all new ones, to MP3 named after the overview's own title.",
            "Keeps a History with a built-in player, Stats and export.",
            "Studio: asks a notebook for an overview plan and makes the overviews you approve, then saves them as they finish.",
            "Phone listening: plays your saved overviews on your phone over the same Wi-Fi.",
            "Shows notebooks shared with you and keeps their files apart from yours.",
        ]
    )
    S += [P("What it is not", h2)]
    S += bullets(
        [
            *([own("not-part-of", None)] if own("not-part-of", None) else []),
            "Not a cloud service or a phone app. It runs on your laptop. No tracking of any kind.",
            "Not a way around Google's limits. It uses your own account for your own content.",
            "It never deletes, renames, edits or regenerates anything in NotebookLM. The only two things it ever writes there are a chat question and an audio overview you approved in Studio (Section 8).",
        ]
    )
    S += box(
        "The page you see is served by the app on your own laptop at the address 127.0.0.1, which only your laptop can reach. "
        "The app asks NotebookLM for the list and the audio with your saved Google sign-in, converts the audio to MP3 and writes a record. Nothing else leaves your laptop.",
        "How it works",
    )
    S.append(PageBreak())

    # ---------------- 2 installing
    S += [P("2. Installing and starting", h1)]
    S += [
        P(
            own(
                "install-copies",
                "Each computer gets its own copy and its own sign-in. Sign-ins are never copied between computers.",
            )
        )
    ]
    S += [P("Mac", h2)]
    S += steps(
        [
            "Download the newest <b>NotebookLM-Harvester-mac-x86_64</b> zip and open it.",
            "Drag <b>NotebookLM Harvester.app</b> into Applications (or anywhere you like).",
            "The first time, right-click the app and choose <b>Open</b>, then <b>Open</b> again. macOS asks because the app is not signed by Apple. If it still refuses: System Settings, Privacy and Security, <b>Open Anyway</b>.",
            "When the app first reads Chrome's sign-in, macOS asks to use <b>Chrome Safe Storage</b> from your keychain. Enter your Mac password and choose <b>Always Allow</b>.",
        ],
        marked=False,
    )
    S += [P("Windows", h2)]
    S += steps(
        [
            "Download the newest <b>NotebookLM-Harvester-win-x64</b> zip and unzip it to a folder.",
            "Open the <b>NotebookLM Harvester</b> folder and double-click <b>NotebookLM Harvester.exe</b>.",
            "If Windows SmartScreen says <b>Windows protected your PC</b>, choose <b>More info</b>, then <b>Run anyway</b>. README-FIRST-win.txt in the zip says the same.",
        ],
        marked=False,
    )
    S += box(
        "Pictures of the Mac and Windows security prompts are not included: they are system windows outside the app, and Code takes pictures only of the app in demo mode. "
        "The Windows pictures (SmartScreen, the firewall alert) are added by the Windows build.",
        "Note",
    )
    S += [P("Starting and stopping", h2)]
    S += bullets(
        [
            "The app opens in its own window. Starting it again while it runs brings that window to the front.",
            "To use your browser instead: Settings, <b>Open the app in</b>, Browser. If the window cannot open on a computer, the app opens in the browser by itself and says so.",
            "Stop it by closing the window (Mac: also Command-Q) or with <b>Quit</b> at the top right. If a harvest is running, closing asks first and finishes the current file; Quit waits until the job ends. In the browser, closing the tab leaves the app running, so use Quit.",
            "Updating: replace the app (Mac) or the folder (Windows) with the new one. No copy of the old version is kept or needed; your saved files, history and settings stay.",
            "Nothing else is needed: Python and ffmpeg are inside the app.",
        ]
    )
    S += wrong(
        "Windows Defender may report the Chrome sign-in reader as a threat the first time (it reads Chrome's saved Google sign-in on this computer). "
        "To allow it: Windows Security, Virus and threat protection, Protection history, select the entry, Actions, <b>Allow on device</b>. "
        + own(
            "defender-untested",
            "On a PC that has never run the app this is <b>untested</b>.",
        )
    )
    S += [P("Your data on disk", h2)]
    S += [
        P(
            "The app keeps its own folder, the app home (on a Mac: <b>~/.nlh</b>), with the record of saved overviews (the ledger), settings and your saved sign-ins, readable only by you. "
            "Your MP3s are in the output folder you choose."
        )
    ]
    S.append(PageBreak())

    # ---------------- 3 accounts and folder
    S += [P("3. Adding accounts and choosing a folder", h1)]
    S += [
        P(
            "You can add as many Google accounts as you like. Each needs a saved Google sign-in and an output folder."
        )
    ]
    S += pic(
        "02-add-account",
        "Adding an account: (1) Add account, (2) Continue with Google, (3) Use a Chrome profile instead.",
        width=0.62,
        max_h=4.0,
    )
    S += steps(
        [
            "Press <b>Add account</b>.",
            "Press <b>Continue with Google</b> (recommended). A Google window opens in your Chrome, or in Microsoft Edge if Chrome is missing. It is a separate blank window that looks like a guest profile; that is expected. Sign in there once and the app remembers the account. The app never sees your password, and your own Chrome profiles are not touched. You can type a name for the account first; it is optional.",
            "Shortcut for an account already signed in to Chrome on this computer: press <b>Use a Chrome profile instead</b>.",
        ]
    )
    S += pic(
        "02b-chrome-list",
        "The Chrome route: (1) Add next to a profile, (2) Refresh the list.",
        width=0.62,
        max_h=3.4,
    )
    S += steps(
        [
            "The app lists the Chrome profiles on this computer, each with its email and profile name. Press <b>Add</b> next to the one you want. Profiles already added say <b>Added</b>.",
            "Signed in to a new profile in Chrome just now? Press <b>Refresh</b> to read the list again.",
        ]
    )
    S += table(
        [
            ["Route", "Needs", "Good to know"],
            [
                "Continue with Google (own window)",
                "Chrome or Edge installed",
                "Recommended on Mac and Windows. No sign-in inside Chrome is needed beforehand. A Mac with only Safari needs Chrome or Edge (both free) to add an account; playing saved files does not.",
            ],
            [
                "Use a Chrome profile instead",
                "A Chrome profile signed in to the account",
                "Quick. A sign-in copied from Chrome can go stale within hours while Chrome stays open; the account then says Sign in again, which moves it to the Google window (Continue with Google).",
            ],
        ],
        [1.7, 1.5, 3.3],
    )
    S += wrong(
        '"This needs Google Chrome or Microsoft Edge on this computer": install one of them, then press Continue with Google again.'
    )
    S += wrong(
        '"Chrome is open, so its sign-in cannot be read" (Windows): quit Chrome completely, also its icon near the clock, then press Add again. '
        '"...is signed out or has expired": open Chrome in that profile, sign in to NotebookLM again, quit Chrome, then try again. '
        "On a Mac, choose <b>Always Allow</b> when macOS asks for Chrome Safe Storage."
    )
    S += pic(
        "03-account-card",
        "The account card with More open: (1) Choose folder, (2) Refresh notebooks, (3) Scan folder.",
        max_h=2.2,
    )
    S += steps(
        [
            "The card is one line: the account, whether it is signed in, where it saves, and <b>Refresh notebooks</b>. Press <b>More</b> for the rest (Check sign-in, Choose folder, Open, Scan folder). The app remembers whether More is open; until a folder is chosen the card stays open.",
            "Press <b>Choose folder</b> and pick where this account's MP3s go. Any folder works, including one that Google Drive, MEGA or OneDrive syncs.",
            "Press <b>Refresh notebooks</b> to read this account's notebooks (or <b>Refresh all accounts</b> above).",
            "<b>Scan folder</b> finds MP3s this app saved before, for example on another computer, so they are not downloaded again.",
        ]
    )
    S += pic(
        "04-choose-folder",
        "The folder picker: (1) Use a recent folder, (2) Forget it, (3) the note when another account saves there, (4) Use this folder.",
        width=0.62,
        max_h=4.2,
    )
    S += steps(
        [
            "<b>Recent folders</b> at the top lists the folders you chose before on this computer, newest first. <b>Use</b> picks one in one click. A folder that is not there now (an unplugged drive, an offline synced folder) says <b>Not available</b>; the app never creates it.",
            "<b>Forget</b> only takes a folder off the list. Nothing on disk changes.",
            "Below the list you can browse, type a path, go up a level, go Home or make a <b>New folder</b>, then press <b>Use this folder</b>. The picker opens at the account's folder, or at the folder you chose last.",
        ]
    )
    S += box(
        "Several accounts can save into the same folder. The picker then says which other account already uses it: files from both accounts are saved together, and existing files are never overwritten (a second file with the same name gets (2) added). Scan folder only ever picks up an account's own files, recognised by the tag inside each MP3, never by the name. A new account is never given a folder by itself: its card offers your recent folders as one-click choices.",
        "One folder for several accounts",
    )
    S += wrong(
        "The account says <b>Sign in again</b>: press it and sign in; your saved MP3s are not affected. "
        'Never sign in two accounts at the same moment. On Windows a very deep folder is refused with "This folder path is too long for Windows. Pick a shorter output folder."'
    )
    S += box(
        own(
            "other-tools-box",
            'If another tool also uses one of your accounts, tick "used by other automated tools" for it in Settings; Studio then warns you when you pick it, and Studio never picks an account for you.',
        ),
        "Accounts used by other tools",
    )
    S.append(PageBreak())

    # ---------------- 4 library
    S += [P("4. The Library: find and harvest", h1)]
    S += pic(
        "05-library-statuses",
        "The Library grouped by notebook: (1) a status, (2) Group, (3) saved Views.",
        max_h=3.9,
    )
    S += table(
        [
            ["Status", "Meaning"],
            ["New", "Finished in NotebookLM and not yet saved."],
            ["Saved", "Saved as an MP3 and recorded. Shows length and size."],
            [
                "File missing",
                "The record exists but the file is gone. The app never deletes your files; if you moved it, use Scan folder.",
            ],
            [
                "Folder not reachable",
                "The output folder is not available, for example a drive that is unplugged.",
            ],
            ["Not finished", "Still being made in NotebookLM."],
            ["Failed last time", "A previous try failed. Select it and harvest again."],
        ],
        [1.7, 4.8],
    )
    S += pic(
        "06-search",
        '(1) The search box, here "garden status:new". (2) Matches are highlighted.',
        max_h=3.3,
        head="Searching",
    )
    S += pic(
        "07-search-help",
        "Press the ? in the search box for this list of search tricks.",
        width=0.55,
        max_h=3.0,
    )
    S += bullets(
        [
            "Press <b>/</b> to jump to the search box. It searches titles, notebook names, accounts, file names and dates, not what is said in the audio.",
            "Prefixes narrow it: <b>status:new</b>, <b>account:work</b>, <b>notebook:garden</b>, <b>after:2026-11-01</b>, <b>shared:yes</b> or <b>shared:no</b>.",
            "The address bar keeps your search and filters, so reload and Back keep your place.",
        ]
    )
    S += pic(
        "08-filters",
        "The filter panel: (1) Status with counts, (2) Owner (mine or shared), (3) Clear all.",
        width=0.42,
        max_h=6.0,
        head="Filtering",
    )
    S += bullets(
        [
            "Tick one or more boxes; the counts show how many rows match.",
            "Sort and Group sit above the list. <b>Views</b> saves a combination under a name. Three are ready-made: New to harvest, Needs attention, Saved this week.",
            "Keys: <b>/</b> search, <b>j</b> and <b>k</b> move down and up, <b>x</b> selects, <b>?</b> shows all shortcuts, <b>g</b> then a letter jumps to a page (g then t opens Studio).",
        ]
    )
    S += pic(
        "09-select-new",
        "(1) Select new, (2) the Harvest button in the bar at the bottom, (3) Include shared.",
        max_h=3.8,
        head="Selecting and harvesting",
    )
    S += steps(
        [
            "Press <b>Select new</b> (every New row in view). Or tick rows by hand; shift-click picks a range. <b>Select all in view</b> and <b>Invert</b> are next to it.",
            "Press the blue <b>Harvest</b> button in the bar that appears at the bottom.",
            "Tick <b>Include shared</b> first if Select new should also pick notebooks shared with you (Section 9).",
        ]
    )
    S += pic(
        "10-harvest-queue",
        "The harvest line while harvesting: the counts, (1) Stop, and Details open to show each item's step.",
        max_h=2.2,
    )
    S += bullets(
        [
            "One line shows what runs and the counts, with <b>Stop</b> (stops after the step in progress, leaving nothing half-saved), <b>Details</b> (each item and the time left), <b>Retry failed</b> and <b>Clear finished</b>. Details stays open or closed as you left it.",
            "The whole Library page scrolls. While a harvest runs, its line stays at the top under the sort and group bar; when it is done it scrolls away with the page.",
            'Harvesting twice never downloads twice. A name clash gets " (2)", never an overwrite.',
        ]
    )
    S += wrong(
        '"Not started: their account has no reachable folder" means that account\'s folder is missing or unplugged: choose a folder or plug the drive in.'
    )
    S.append(PageBreak())

    # ---------------- 5 files
    S += [P("5. Where files go, names and quality", h1)]
    S += table(
        [
            ["Thing", "How it works"],
            [
                "Output folder",
                "One folder per account. It can be inside a Google Drive, MEGA or OneDrive folder that syncs.",
            ],
            [
                "Folders",
                "Each notebook gets its own subfolder, named after the notebook, made once and reused.",
            ],
            [
                "File name",
                "The overview's own title plus .mp3, for example <i>Garden Planning / Why Raised Beds Warm Up Faster.mp3</i>. Characters Windows cannot use are removed.",
            ],
            [
                "Template",
                "Settings, File names: tokens {notebook}, {title}, {date}, {account}, with a live example. One folder level at most. Files already saved are never renamed.",
            ],
            [
                "Quality",
                "MP3 with variable bitrate (around 90 kbps for speech), same channels as NotebookLM's audio. Tags: title, notebook as album, and the ids that let Scan folder recognise the file.",
            ],
            [
                "Safety",
                "Written to a temporary .part file, checked, then renamed. Never overwrites, never deletes your MP3s.",
            ],
            [
                "Shared notebooks",
                "Saved under folders named <b>Shared - </b> plus the notebook name, never mixed with your own.",
            ],
            [
                "Windows path length",
                "Full paths stay under about 240 characters: long titles are shortened, a folder that is too deep is refused.",
            ],
        ],
        [1.6, 4.9],
    )
    S.append(PageBreak())

    # ---------------- 6 history
    S += [P("6. History, player and export", h1)]
    S += pic("11-history", "History: (1) Play, (2) Copy path, (3) More actions, (4) Filters.", max_h=3.0)
    S += bullets(
        [
            "Everything saved, newest first, a page at a time (50, 100 or 250; <b>Load more</b> on phones).",
            "Each row: <b>Play</b>, <b>Show in folder</b>, <b>Copy path</b> and <b>More actions</b> (Download again, Open notebook in NotebookLM). A row whose folder is unreachable greys its buttons out.",
            "Filters: accounts, notebooks, saved date, size, length and Owner.",
        ]
    )
    S += pic(
        "12-player",
        "The player stays at the bottom while you browse: (1) back 15 seconds, (2) speed, (3) Start from the beginning.",
        max_h=1.8,
    )
    S += bullets(
        [
            "<b>It continues where you stopped.</b> Press Play on a file you were part way through and it starts a few seconds before that place, with the line <b>Continuing from</b> and the time. Press <b>Start from the beginning</b> to hear it from the start instead.",
            "The place is kept by the app on your laptop, so the laptop and a paired phone share it. It is saved every few seconds while playing, and when you pause, close the window or quit.",
            "A file played to the end starts from the beginning next time. A thin line under a row in History and Library shows how far you got; the file's details say <b>Listened to</b> and the time.",
            "If a file is replaced (downloaded again), its place is forgotten, so it never jumps into the middle of a different file.",
            "Press <b>p</b> to play or pause.",
        ]
    )
    S += side_by_side(
        (
            "13-export",
            "Export gives every matching row, not only this page, as CSV (opens in Excel, accents included) or JSON.",
            SHOTS,
            None,
        ),
        (
            "14-details",
            "Click any row's title for its details: folder, size, length, tags and every harvest attempt.",
            SHOTS,
            None,
        ),
    )
    S.append(PageBreak())

    # ---------------- 7 stats and settings
    S += [P("7. Stats and Settings", h1)]
    S += pic(
        "15-stats",
        "Stats: totals, saved files by account, top notebooks and saved files by month.",
        max_h=3.6,
    )
    S += pic("16-settings-naming", "Settings: (1) the file name template, (2) its live example.", max_h=2.4)
    S += bullets(
        [
            "<b>Appearance</b>: match system, light or dark.",
            "<b>Defaults</b>: a suggested folder for new accounts and the view to open with.",
            "<b>Open the app in</b>: App window (the default) or Browser.",
            "<b>Studio</b>, <b>Phone</b>, <b>Notifications</b> (a browser message when a long harvest ends; when the app opens in your browser. Native messages for the app window come with watch mode), <b>Diagnostics</b> (facts to include when asking for help; no account names, emails or sign-in data) and the trash for failed sign-ins.",
        ]
    )
    S += pic(
        "17-settings-studio",
        "Settings, Studio: daily limit per account (default 4, 1 to 20), pause between requests (default 45 seconds; set it in seconds, minutes or hours, from 30 seconds to 24 hours), language, and accounts used by other tools.",
        max_h=4.0,
    )
    S += box(
        "Demo mode: starting the app with NLH_DEMO=1 uses made-up accounts and notebooks and never touches Google. A blue banner says so. Every picture in this manual comes from it, and you can use it to practise without risk.",
        "Practise safely",
    )
    S.append(PageBreak())

    # ---------------- 8 studio
    S += [P("8. Studio: ask for a plan and make overviews", h1)]
    S += box(
        "Studio is the only part of the app that writes to NotebookLM, and it does nothing on its own: every question and every overview starts from your click.",
        "Important",
    )
    S += pic(
        "19-studio-pick",
        "(1) Choose the account, (2) find a notebook, (3) Ask for a plan. (4) View-only shared notebooks are greyed out.",
        max_h=4.4,
    )
    S += steps(
        [
            'Open <b>Studio</b> and choose the account. It starts empty every time; the app never picks one for you. The page shows "0 of 4 overviews made today with this account".',
            "Type part of a notebook's name and pick it. Only notebooks you own or can edit can be picked.",
            "Press <b>Ask for a plan</b>. It takes about half a minute.",
        ]
    )
    S += pic(
        "20-studio-warning",
        "The first time only: asking adds your question and the answer to that notebook's chat. Press Ask.",
        width=0.7,
        max_h=2.0,
    )
    S += pic(
        "21-studio-plan",
        "The plan: (1) edit a title or focus, (2) Add an overview, (3) Keep this plan, (4) the answer as received.",
        max_h=4.6,
    )
    S += [
        P(
            "NotebookLM's answer is shown as received; the app reads it into rows you can edit, add, move up or remove. The answer itself is not saved, only the plan you keep."
        )
    ]
    S += pic(
        "22-studio-make",
        "On a kept plan press Make overviews...: (1) tick the ones to make, (2) format and length, (3) Add to the queue.",
        width=0.7,
        max_h=4.4,
    )
    S += steps(
        [
            "Under <b>Kept plans</b>, press <b>Make overviews...</b> and tick the items you want.",
            "For each, check the focus and choose the format (deep dive, brief, critique or debate), length (short, default or long) and language.",
            "Press <b>Add to the queue</b>. Nothing is sent yet.",
        ]
    )
    S += pic(
        "23-studio-queue", "The queue before starting: (1) Start. Each waiting row can be dropped.", max_h=3.6
    )
    S += pic("24-studio-start-confirm", "Start asks once more and repeats the rules.", width=0.7, max_h=1.8)
    S += [
        P(
            "After <b>Start</b> the app sends one request, follows it to the end (about 3 minutes), saves the MP3, pauses, then sends the next."
        )
    ]
    S += pic(
        "25-studio-running",
        "While it runs: one row is Being made, the rest wait. (1) Cancel stops after the current step.",
        max_h=3.2,
    )
    S += pic(
        "26-studio-done",
        "When finished, each row says Saved with a Play button (1). The files are in the account's folder and in History.",
        max_h=3.2,
    )
    S += pic(
        "27-studio-limit",
        "Usage limit reached (shown in demo mode): (1) the warning, (2) Queue again for tomorrow.",
        max_h=2.8,
    )
    S += pic(
        "28-studio-check-needed",
        "After a crash or an unclear error: (1) Check NotebookLM looks, without writing anything, for an overview that was made.",
        max_h=1.5,
    )
    S += table(
        [
            ["Control", "What it does"],
            [
                "Cancel",
                "Stops after the current safe step. Waiting rows become Dropped. One already being made stays Being made; Resume collects it.",
            ],
            [
                "Drop and Queue again",
                "Remove a waiting row, or put a dropped, failed or paused one back (once per row).",
            ],
            [
                "Daily limit",
                "Default 4 per account per day, counted when a request is sent. Extra rows wait for tomorrow.",
            ],
            [
                "Usage limit",
                "If Google refuses for a limit, that account is paused for the day; its other rows show Paused: limit.",
            ],
            [
                "Check needed",
                "The app does not guess whether a request reached Google. Check NotebookLM finds it, or says it was not made. Nothing is sent again without your click.",
            ],
        ],
        [1.6, 4.9],
    )
    S += box(
        "NotebookLM does not show your real daily allowance, so the app keeps its own count; 4 is a safe guess. Generated overviews use up your allowance and cannot be taken back.",
        "Good to know",
    )
    S.append(PageBreak())

    # ---------------- 9 shared
    S += [P("9. Shared with me", h1)]
    S += pic(
        "29-shared-badge",
        "Shared notebooks: (1) the Shared with me badge, (2) the Owner filter chip, (3) Include shared.",
        max_h=4.2,
    )
    S += bullets(
        [
            "A notebook someone shared with you appears after you have opened it once in NotebookLM, with a <b>Shared with me</b> badge. Library and History both have the <b>Owner</b> filter (My notebooks or Shared with me).",
            "Their overviews are saved under folders named <b>Shared - </b> plus the notebook name.",
            "<b>Select new</b> skips shared notebooks unless you tick <b>Include shared</b>. Rows you tick by hand are harvested as chosen.",
            'Studio cannot use a notebook you can only view: it is greyed out with "View only: Studio cannot ask or generate here." (Section 8, picture 4).',
            "If access is withdrawn the notebook drops out of the list. MP3s already saved stay.",
        ]
    )
    S += box(
        own(
            "shared-tested",
            "A view-only share was checked live (listed, one overview, downloaded). An editor share is <b>untested</b>; it does not change harvesting.",
        ),
        "Tested",
    )
    S.append(PageBreak())

    # ---------------- 10 phone
    S += [P("10. Listening from your phone", h1)]
    S += [
        P(
            "Your phone becomes a listening remote for the laptop, on the same Wi-Fi only. The audio streams from the laptop; nothing is uploaded and nothing is opened to the internet."
        )
    ]
    S += pic(
        "30-phone-settings",
        "On the laptop, Settings, Phone: (1) Allow my phone on this Wi-Fi, (2) Pair a phone, (3) the QR code.",
        max_h=4.8,
    )
    S += steps(
        [
            "Tick <b>Allow my phone on this Wi-Fi</b>. The card shows the address for the phone.",
            "Press <b>Pair a phone</b>. A QR code and a link appear; they work once and expire in 5 minutes.",
            "Scan the QR code with the phone's camera, or open the link on the phone.",
        ]
    )
    S += side_by_side(
        ("31-phone-pair", "On the phone: (1) name it, (2) Pair this phone.", SHOTS, None),
        ("32-phone-library", "The phone's Library: browse and search; no harvest or settings.", SHOTS, None),
    )
    S += side_by_side(
        (
            "33-phone-player",
            "History on the phone: (1) Play. The player has back, forward and speed.",
            SHOTS,
            None,
        ),
        ("36-phone-unreachable", "If the laptop is off or on another Wi-Fi, the page says so.", SHOTS, None),
    )
    S += side_by_side(
        (
            "android-create-shortcut",
            "Chrome menu, Install and create shortcut, then Create shortcut: name Harvester, press Add.",
            PHONE,
            None,
        ),
        ("android-add-to-home-screen", "The phone asks Add to Home screen?: press Add.", PHONE, None),
        head="Putting it on the phone's home screen (Android, tested)",
    )
    S += side_by_side(
        (
            "android-home-screen-icon",
            "The Harvester icon on the home screen.",
            PHONE,
            (0.27, 0.0, 0.53, 0.72) if PUBLIC else None,
        ),
        ("android-opened-from-icon", "Tapping it opens the paired page (in a Chrome tab).", PHONE, None),
    )
    S += box(
        "It is a shortcut, not an offline app: it plays only while the laptop is on, the app is running and the phone is on the same Wi-Fi. Over a home network Android opens it in a Chrome tab, not full screen. "
        "iPhone (Share, Add to Home Screen) is <b>untested</b>, including audio with the screen locked.",
        "Good to know",
    )
    S += pic(
        "34-phone-paired-list",
        "Paired phones are listed with their last use and (1) Remove, which cuts that phone off at once.",
        max_h=1.8,
    )
    S += pic(
        "35-phone-homescreen-note",
        "Settings, Phone, Put it on the phone's home screen: the same steps in the app.",
        max_h=2.4,
    )
    S += pic(
        "37-phone-firewall-windows",
        "On Windows the card says to choose Allow, not Cancel, in the Windows Security window.",
        max_h=1.6,
    )
    S += wrong(
        "The phone cannot connect: same Wi-Fi? App running? Box ticked? On Windows, if you pressed Cancel in the Windows Security window, allow the app in Windows Security, Firewall, Allow an app through firewall, then pair again. "
        "If Windows marks the Wi-Fi as Public, tick Public networks in that window or set the Wi-Fi to Private. The app never changes firewall settings itself."
    )
    S += bullets(
        [
            "Off by default. Unticking, quitting or removing a phone cuts access at once, even mid-song.",
            "It listens only on your private home network address, never a VPN, and stops by itself if the laptop changes network.",
            "The phone can browse, play and save where you stopped listening, nothing else. The laptop refuses everything else, and phone replies carry no emails or folder paths.",
        ]
    )
    S.append(PageBreak())

    # ---------------- 11 limits
    S += [P("11. Limits, safety rules and risks", h1)]
    S += bullets(
        [
            "Toward NotebookLM the app only lists and downloads, except Studio's two actions, always on your click and capped.",
            "It never deletes or overwrites your saved MP3s, and never deletes, edits or regenerates anything in NotebookLM.",
            "Saved sign-ins are readable only by you and never appear in logs or reports.",
            "No tracking. The only network calls are to Google.",
            "It backs off on rate limits and never runs two sign-ins at once.",
        ]
    )
    S += table(
        [
            ["Risk", "What it means for you"],
            [
                "Unofficial library",
                "NotebookLM has no public download. The library copies what the web app does; Google can change it and the app may stop listing or downloading until it is fixed. Saved MP3s are unaffected.",
            ],
            [
                "Google's terms",
                "Your own account, your own content, at human speed. Not a way around Google's limits.",
            ],
            [
                "Studio allowance",
                "The daily number Google allows is not shown anywhere; the default cap of 4 is a safe guess.",
            ],
            [
                "Sessions expire",
                "Chrome-copied sign-ins go stale within hours. How long an own-window sign-in lasts is <b>untested</b>.",
            ],
            [
                "Phone over HTTP",
                "On your home Wi-Fi others on the network could see the traffic. Pairing keys and the listen-only rule limit what a phone can do.",
            ],
            [
                "Public code",
                "The code is public on GitHub with secret scanning. Your sign-ins, audio and data are never in it.",
            ],
        ],
        [1.6, 4.9],
    )
    S.append(PageBreak())

    # ---------------- 12 status
    S += [P("12. Status and what is still untested", h1)]
    S += table(
        [
            ["Part", "State on %s" % BUILT],
            ["Harvester (Library, History, Stats, Settings)", "Done, Mac and Windows"],
            [
                "Studio (plan, generate, save)",
                own("studio-state", "Done, checked live"),
            ],
            ["Phone listening and home-screen shortcut", "Done, checked live on an Android phone"],
            [
                "App window (Mac and Windows)",
                "Done; your own sign-in with the window open is part of your check",
            ],
            ["Continue where you stopped", "Done on the laptop; on the phone, part of your check"],
            ["Shared with me", "Done, view-only share checked live"],
        ],
        [3.5, 3.0],
    )
    S += [P("Untested so far", h2)]
    S += bullets(
        [
            "iPhone: Add to Home Screen, play, and audio with the screen locked.",
            "An editor share (a notebook shared with you with edit rights).",
            "How long an own-window sign-in lasts untouched (measured before watch mode is built).",
            own(
                "defender-untested-list",
                "Windows Defender and SmartScreen on a PC that has never run the app.",
            ),
        ]
    )
    waiting = own("waiting-on-you", None)  # the owner's pending items (private file only)
    if waiting:
        S += [P("Waiting on you", h2)]
        S += bullets(waiting)
    S += [P("What comes next", h2)]
    S += bullets(
        [
            "Small leftover fixes from the reports.",
            "Watch mode with notifications and a sign-in reminder.",
            "Optional video download.",
            "Phone control (harvest or Studio from the phone), only if you ask for it.",
        ]
    )
    S.append(PageBreak())

    # ---------------- 13 troubleshooting
    S += [P("13. Troubleshooting", h1)]
    S += table(
        [
            ["You see", "Try this"],
            [
                "Account says Sign in again, or the list is empty",
                "Press Sign in again (it opens the Google window, also for accounts added from Chrome), then Refresh notebooks.",
            ],
            [
                "A new overview is not listed",
                "Press Refresh notebooks. If it still is not there it may still be generating (Not finished).",
            ],
            [
                "A shared notebook is missing",
                "Open it once in NotebookLM, then Refresh. Tick Include shared when using Select new.",
            ],
            [
                "File missing or Folder not reachable",
                "Reconnect the drive or folder. If you moved files, press Scan folder.",
            ],
            ["Windows: folder path too long", "Pick a shorter output folder."],
            [
                "Windows protected your PC",
                "More info, then Run anyway. It appears because the file came from the internet.",
            ],
            [
                "The phone cannot connect",
                "Same Wi-Fi? App running? Box ticked? Windows: Allow in Windows Security (see Section 10). Then pair again.",
            ],
            [
                "Phone says Cannot reach the laptop",
                "The laptop is off, the app is closed or the Wi-Fi changed. Tick the box again and check the address in Settings, Phone.",
            ],
            [
                "Studio says usage limit reached",
                "Nothing more is sent for that account until tomorrow. Press Queue again then.",
            ],
            ["Studio row says Check needed", "Press Check NotebookLM. It looks without writing anything."],
            [
                "Nothing works after a long time",
                own(
                    "help-row",
                    "Google may have changed something the library relies on. Open an issue on the GitHub repository and include Settings, Diagnostics.",
                ),
            ],
        ],
        [2.4, 4.1],
    )
    S += box(
        own(
            "help-box",
            "Open an issue on the GitHub repository (jordandigitals/notebooklm-harvester). Settings, Diagnostics gives facts that are safe to share.",
        ),
        "Getting help",
    )
    S.append(PageBreak())

    # ---------------- 14 glossary
    S += [P("14. Quick reference and glossary", h1)]
    S += [P("One-minute routine", h2)]
    S += steps(
        [
            "Start the app.",
            "Library, then Refresh all accounts.",
            "Select new, then Harvest.",
            "Play in the page, or on your phone.",
            "Quit when done.",
        ],
        marked=False,
    )
    S += table(
        [
            ["Term", "Meaning"],
            ["Audio Overview", "The two-voice audio summary NotebookLM makes from a notebook's sources."],
            ["Harvest", "Download an overview, convert it to MP3 and save it with a record."],
            ["Ledger", "The app's record of every saved overview. It is why nothing downloads twice."],
            ["App home", "The app's own folder with the ledger, settings and sign-ins (~/.nlh on a Mac)."],
            ["Studio", "Asks for plans and makes overviews. The only part that writes to NotebookLM."],
            ["Plan", "Suggested overview titles and focus lines for a notebook."],
            ["Daily limit (cap)", "The number of overviews Studio may make per account per day (default 4)."],
            ["Paired phone", "A phone given its own key to listen over your Wi-Fi."],
            ["Demo mode", "Made-up accounts and notebooks that never touch Google."],
        ],
        [1.6, 4.9],
    )
    S += table(
        [
            ["What", "Where"],
            *own(
                "where-things-are",
                [
                    ["This manual", "GitHub, jordandigitals/notebooklm-harvester, Releases"],
                    [
                        "Code and the manual's source",
                        "GitHub, jordandigitals/notebooklm-harvester (docs/manual)",
                    ],
                ],
            ),
        ],
        [2.4, 4.1],
    )
    S += [
        P(
            f"Manual {MANUAL_VERSION}, made {BUILT} for app {APP_VERSION} with pictures taken from the app in demo mode. Each release (0.x.0) regenerates this manual with fresh pictures.",
            small,
        )
    ]

    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.8 * inch,
        bottomMargin=0.85 * inch,
        title="NotebookLM Harvester manual",
        author="NotebookLM Harvester",
        subject=f"Manual {MANUAL_VERSION} for app {APP_VERSION}",
    )
    doc.build(S, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    out = build()
    print(out, out.stat().st_size, "bytes")
