#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$ROOT/.build/release-package/PCL Relay.app"
ARCHIVE="$DIST/PCL-Relay-macOS.zip"
CHECKSUM="$ARCHIVE.sha256"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT/macos/Info.plist")"

mkdir -p "$DIST" "$(dirname "$APP")"
rm -f "$ARCHIVE" "$CHECKSUM"

"$ROOT/scripts/build_macos_app.sh" "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"

(
  cd "$DIST"
  /usr/bin/shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")"
)

echo "PCL Relay $VERSION"
echo "$ARCHIVE"
echo "$CHECKSUM"
