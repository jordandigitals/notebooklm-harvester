#!/bin/bash
# Build the self-contained Mac app (R11, D-018): bundles Python and a static ffmpeg.
# Run from the repo root on a Mac: packaging/build_mac.sh
# The app window (D-035): pywebview is imported by name at run time, so PyInstaller is told about it;
# packaging/app.icns is made from nlh/static/icons/icon-512.png (sips + iconutil).
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

if [ ! -x "$BUILD_VENV/bin/python" ]; then
  /usr/local/bin/python3.14 -m venv "$BUILD_VENV"
fi
# Every build: a reused build environment must pick up new pinned requirements (0.3.0 added segno).
"$BUILD_VENV/bin/pip" install -q -r requirements.txt pyinstaller==6.22.3

rm -rf build/pyinstaller "dist/$NAME.app" dist/NotebookLM-Harvester-mac-*
# Two steps so the bundle gets its real version (the command line has no option for it): make the spec,
# write the version into BUNDLE, then build. Everything that goes inside the .app is given to PyInstaller
# (the notices too), so nothing is added after it signs the bundle: a file copied in later breaks the
# ad hoc seal, and a downloaded (quarantined) copy can then be called "damaged" by Gatekeeper.
mkdir -p build/pyinstaller
"$BUILD_VENV/bin/pyi-makespec" --windowed \
  --name "$NAME" \
  --osx-bundle-identifier com.jordandigitals.notebooklm-harvester \
  --specpath build/pyinstaller \
  --add-data "$PWD/nlh/static:nlh/static" \
  --add-data "$PWD/nlh/static/THIRD-PARTY-NOTICES.txt:." \
  --add-binary "$PWD/$CACHE/ffmpeg:." \
  --add-binary "$PWD/$CACHE/ffprobe:." \
  --collect-all notebooklm \
  --collect-all rookie_cookies \
  --collect-all playwright \
  --hidden-import webview --hidden-import webview.platforms.cocoa \
  --icon "$PWD/packaging/app.icns" \
  nlh/app_entry.py >/dev/null
SPEC="build/pyinstaller/$NAME.spec"
"$BUILD_VENV/bin/python" - "$SPEC" "$VERSION" <<'PY'
import sys
spec, version = sys.argv[1], sys.argv[2]
text = open(spec).read()
marker = "app = BUNDLE(\n    coll,\n"
assert marker in text, "BUNDLE call not found in the generated spec"
text = text.replace(marker, marker + f"    version={version!r},\n", 1)
open(spec, "w").write(text)
PY
"$BUILD_VENV/bin/pyinstaller" --noconfirm --clean \
  --workpath build/pyinstaller --distpath dist "$SPEC"

STAGE="dist/NotebookLM-Harvester-mac-$ARCH-$VERSION"
mkdir -p "$STAGE"
cp -R "dist/$NAME.app" "$STAGE/"
cp nlh/static/THIRD-PARTY-NOTICES.txt packaging/README-FIRST-mac.txt "$STAGE/"
(cd dist && ditto -c -k --keepParent "$(basename "$STAGE")" "$(basename "$STAGE").zip")
echo "Built dist/$(basename "$STAGE").zip"
du -sh "dist/$NAME.app" "dist/$(basename "$STAGE").zip"
