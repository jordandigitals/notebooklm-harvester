# NotebookLM Harvester

A personal desktop tool for Mac and Windows. It finds the Audio Overviews made in Google's NotebookLM app, downloads each finished overview, converts it to MP3 and saves it to a folder the user chooses.

It is read-only toward NotebookLM: it lists notebooks and downloads audio. It never creates, edits, regenerates or deletes anything in the account.

Built on the unofficial [notebooklm-py](https://github.com/teng-lin/notebooklm-py) library (pinned to 0.8.2). Saved Google sessions stay in the user's app data folder and are never committed.

- Install and use: [INSTALL.md](INSTALL.md)
- Start: `run-mac.command` or `run-windows.bat`
- Command line (for checks): `python -m nlh --help`

## Layout

| Path | What |
|---|---|
| `nlh/adapter.py` | The only module that talks to notebooklm-py |
| `nlh/accounts.py` | Add, check and sign in accounts |
| `nlh/harvest.py` | Download, convert, tag, verify, save, record |
| `nlh/ledger.py` | SQLite ledger keyed by artifact id |
| `nlh/media.py`, `nlh/naming.py` | ffmpeg helpers, file names |
| `nlh/server.py`, `nlh/static/` | Local web page (Flask, 127.0.0.1 only) |
| `tests/` | pytest suite, no real sessions or audio |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
```
