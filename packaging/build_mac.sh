#!/bin/bash
# Build the self-contained Mac app (R11, D-018): bundles Python and a static ffmpeg.
# Run from the repo root on a Mac: packaging/build_mac.sh
# Output: dist/NotebookLM-Harvester-mac-<arch>-<version>.zip
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"", open("nlh/__init__.py").read()).group(1))')"
ARCH="$(uname -m)"
NAME="NotebookLM Harvester"
BUILD_VENV="${BUILD_VENV:-$HOME/.nlh-build-venv}"
CACHE="build/ffmpeg-cache"

# Static ffmpeg 9.0.2 for macOS x86_64 from martin-riedl.de, pinned by SHA-256.
FF_BASE="https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2"
FF_ZIP_SHA="7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4"
FP_ZIP_SHA="2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd"
if [ "$ARCH" != "x86_64" ]; then
  echo "This script pins the Intel (x86_64) ffmpeg. Add the arm64 build and checksums first."
  exit 1
fi

mkdir -p "$CACHE"
fetch() {  # name expected_sha
  local zip="$CACHE/$1.zip"
  [ -f "$zip" ] || curl -sSL -o "$zip" "$FF_BASE/$1.zip"
  echo "$2  $zip" | shasum -a 256 -c - >/dev/null
  unzip -oq "$zip" -d "$CACHE"
  chmod +x "$CACHE/$1"
}
fetch ffmpeg "$FF_ZIP_SHA"
fetch ffprobe "$FP_ZIP_SHA"

if [ ! -x "$BUILD_VENV/bin/pyinstaller" ]; then
  /usr/local/bin/python3.14 -m venv "$BUILD_VENV"
  "$BUILD_VENV/bin/pip" install -q -r requirements.txt pyinstaller==6.22.3
fi

rm -rf build/pyinstaller "dist/$NAME.app" dist/NotebookLM-Harvester-mac-*
"$BUILD_VENV/bin/pyinstaller" --noconfirm --clean --windowed \
  --name "$NAME" \
  --osx-bundle-identifier com.jordandigitals.notebooklm-harvester \
  --workpath build/pyinstaller --distpath dist --specpath build/pyinstaller \
  --add-data "$PWD/nlh/static:nlh/static" \
  --add-binary "$PWD/$CACHE/ffmpeg:." \
  --add-binary "$PWD/$CACHE/ffprobe:." \
  --collect-all notebooklm \
  --collect-all rookie_cookies \
  --exclude-module playwright \
  nlh/app_entry.py

cp packaging/THIRD-PARTY-NOTICES.txt "dist/$NAME.app/Contents/Resources/"
STAGE="dist/NotebookLM-Harvester-mac-$ARCH-$VERSION"
mkdir -p "$STAGE"
cp -R "dist/$NAME.app" "$STAGE/"
cp packaging/THIRD-PARTY-NOTICES.txt packaging/README-FIRST-mac.txt "$STAGE/"
(cd dist && ditto -c -k --keepParent "$(basename "$STAGE")" "$(basename "$STAGE").zip")
echo "Built dist/$(basename "$STAGE").zip"
du -sh "dist/$NAME.app" "dist/$(basename "$STAGE").zip"
