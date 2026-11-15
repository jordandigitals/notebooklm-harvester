# Manual

The owner's manual with pictures (one PDF, Letter, readable on a laptop and on a phone).

## Rebuild

From the repo root, with the dev environment active:

```
pip install -r docs/manual/requirements.txt
python -m docs.manual.shots          # takes the pictures (about 8 minutes)
python -m docs.manual.build_manual            # full edition: NotebookLM-Harvester-Manual.pdf
python -m docs.manual.build_manual --public   # public edition: NotebookLM-Harvester-Manual-public.pdf
```

Two editions come from the one source. The **public** edition (attached to each GitHub release as
NotebookLM-Harvester-Manual.pdf, linked from the main README) leaves out owner-specific content:
other-account mentions, other people's PCs, the owner's pending items and personal status;
help points to GitHub issues. The **full** edition is the owner's copy (MEGA and the Drive shared
folder). Mark owner-only text in build_manual.py with `ed(full_text, public_text)` (None leaves it
out of the public edition). Check every page and picture of the public edition before publishing.

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

No PDF is kept in git. The public edition goes to the GitHub release; the full edition to MEGA
(a private folder), the Drive shared folder and the relay.
