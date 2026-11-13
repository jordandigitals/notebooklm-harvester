#!/bin/bash
# NotebookLM Harvester launcher for Mac. Double-click to start.
# Creates or reuses .venv next to this file, installs pinned requirements when they
# change, checks ffmpeg, then starts the local page in your browser.
set -e
cd "$(dirname "$0")"

PY=""
for cand in python3.14 python3.13 python3.12 python3.11; do
  for dir in /usr/local/bin /opt/homebrew/bin /usr/bin; do
    if [ -x "$dir/$cand" ]; then PY="$dir/$cand"; break 2; fi
  done
  if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 to 3.14 is needed. Install it with: brew install python@3.13"
  read -r -p "Press Enter to close." _
  exit 1
fi

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1 && [ ! -x "/usr/local/bin/$tool" ] && [ ! -x "/opt/homebrew/bin/$tool" ]; then
    echo "$tool is needed. Install it with: brew install ffmpeg"
    read -r -p "Press Enter to close." _
    exit 1
  fi
done
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (about a minute)..."
  "$PY" -m venv .venv
fi
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.req-hash 2>/dev/null)" != "$REQ_HASH" ]; then
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  echo "$REQ_HASH" > .venv/.req-hash
fi

exec .venv/bin/python -m nlh serve
