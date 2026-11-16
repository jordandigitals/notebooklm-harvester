# Manual

The owner's manual with pictures (one PDF, Letter, readable on a laptop and on a phone).

## Rebuild

From the repo root, with the dev environment active:

```
pip install -r docs/manual/requirements.txt
python -m docs.manual.shots          # takes the pictures (about 8 minutes)
python -m docs.manual.build_manual --public          # public edition: NotebookLM-Harvester-Manual-public.pdf
python -m docs.manual.build_manual --private FILE    # owner's edition: NotebookLM-Harvester-Manual.pdf
```

The **public** edition (attached to each GitHub release as NotebookLM-Harvester-Manual.pdf, linked
from the main README) is built from this folder alone; help points to GitHub issues. The owner's
edition swaps in a few owner-specific lines from a private JSON file that is kept outside this
repository and never committed (`--private FILE` or `NLH_MANUAL_PRIVATE`). In build_manual.py,
`own(key, public_text)` marks such a line: the public text is used unless the private file has that
key (None leaves the line out). This repository holds no private text. Check every page and picture
of the public edition before publishing.

- `shots.py` starts the app in **demo mode only** (made-up accounts and notebooks, no Google),
  light theme, with a made-up home folder so no real user name appears. Each picture is cropped to
  the part that matters and marked with orange numbered circles that match the numbered steps.
  Pairing codes it shows are used up before it ends.
- `real-phone/` holds four pictures from a real Android phone in demo mode (the home-screen
  shortcut). They are kept as they are; `shots.py` does not touch them.
- `build_manual.py` holds the text. The footer shows the app version (from `nlh/__init__.py`), the
  manual version and the date it was made.

## Rules

- Every release (0.x.0) regenerates the pictures and the manual.
- No real account data, titles, emails, paths or live pairing codes in any picture. Check every
  picture before publishing.
- No em dashes. Anything not tested live stays marked untested.
- Windows-only pictures (SmartScreen, the firewall alert, a long-path error) are added by the
  Windows session.

No PDF is kept in git. The public edition goes to the GitHub release; the owner's edition stays private.
