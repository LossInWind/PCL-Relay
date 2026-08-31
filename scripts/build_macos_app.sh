#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESTINATION="${1:-/Applications/PCL Relay.app}"
STAGING="$ROOT/.build/PCL Relay.app"
STAMP="$(date +%Y%m%d-%H%M%S)"

cd "$ROOT"
swift build -c release --product PCLCodexManager

PYTHON_ROOT="${PCL_EMBED_PYTHON_ROOT:-${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python}"
if [[ ! -x "$PYTHON_ROOT/bin/python3.12" || ! -f "$PYTHON_ROOT/lib/libpython3.12.dylib" ]]; then
  echo "Embedded Python 3.12 runtime not found at $PYTHON_ROOT" >&2
  exit 1
fi

if [[ -e "$STAGING" ]]; then
  mv "$STAGING" "$ROOT/.build/PCL Relay.app.backup-$STAMP"
fi

mkdir -p "$STAGING/Contents/MacOS" "$STAGING/Contents/Resources"
mkdir -p "$STAGING/Contents/Resources/bridge/python/bin" "$STAGING/Contents/Resources/bridge/python/lib" "$STAGING/Contents/Resources/bridge/src"
cp "$ROOT/.build/release/PCLCodexManager" "$STAGING/Contents/MacOS/PCLCodexManager"
cp "$ROOT/macos/Info.plist" "$STAGING/Contents/Info.plist"
cp "$PYTHON_ROOT/bin/python3.12" "$STAGING/Contents/Resources/bridge/python/bin/python3.12"
ln -s python3.12 "$STAGING/Contents/Resources/bridge/python/bin/python3"
cp "$PYTHON_ROOT/lib/libpython3.12.dylib" "$STAGING/Contents/Resources/bridge/python/lib/libpython3.12.dylib"
rsync -a --exclude site-packages --exclude __pycache__ --exclude '*.pyc' "$PYTHON_ROOT/lib/python3.12/" "$STAGING/Contents/Resources/bridge/python/lib/python3.12/"
rsync -a --exclude __pycache__ --exclude '*.pyc' "$ROOT/pcl_codex_bridge/" "$STAGING/Contents/Resources/bridge/src/pcl_codex_bridge/"
cp "$ROOT/scripts/pcl-codex-bundled" "$STAGING/Contents/Resources/bridge/pcl-codex"
cp "$ROOT/LICENSE" "$STAGING/Contents/Resources/bridge/LICENSE"
cp "$ROOT/NOTICE" "$STAGING/Contents/Resources/bridge/NOTICE"
cp "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/LICENSE.txt" "$STAGING/Contents/Resources/bridge/PYTHON-LICENSE.txt"
chmod 755 "$STAGING/Contents/Resources/bridge/pcl-codex"

ICONSET="$ROOT/.build/PCLRelay.iconset"
if [[ -e "$ICONSET" ]]; then
  mv "$ICONSET" "$ROOT/.build/PCLRelay.iconset.backup-$STAMP"
fi
swift "$ROOT/scripts/make_icon.swift" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$STAGING/Contents/Resources/AppIcon.icns"

codesign --force --deep --sign - "$STAGING"

if [[ -e "$DESTINATION" ]]; then
  mkdir -p "$ROOT/.build/app-install-backups"
  mv "$DESTINATION" "$ROOT/.build/app-install-backups/PCL Relay.app.backup-$STAMP"
fi
mkdir -p "$(dirname "$DESTINATION")"
mv "$STAGING" "$DESTINATION"
echo "$DESTINATION"
