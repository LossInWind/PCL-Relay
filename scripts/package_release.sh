#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$ROOT/.build/release-package/PCL Relay.app"
ARCHIVE="$DIST/PCL-Relay-macOS.zip"
CHECKSUM="$ARCHIVE.sha256"
VERSION="$(tr -d '[:space:]' < "$ROOT/pcl_codex_bridge/VERSION")"

mkdir -p "$DIST" "$(dirname "$APP")"
rm -f "$ARCHIVE" "$CHECKSUM"

"$ROOT/scripts/build_macos_app.sh" "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
if [[ "$APP_VERSION" != "$VERSION" ]]; then
  echo "Release version mismatch: expected $VERSION, got $APP_VERSION" >&2
  exit 1
fi
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
(
  cd "$DIST"
  /usr/bin/shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")"
)

echo "PCL Relay $VERSION"
echo "$ARCHIVE"
echo "$CHECKSUM"
echo "Linux bundles are built on their target architecture with scripts/build_linux_bundle.sh"
