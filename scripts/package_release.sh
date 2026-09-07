#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$ROOT/.build/release-package/PCL Relay.app"
ARCHIVE="$DIST/PCL-Relay-macOS.zip"
CHECKSUM="$ARCHIVE.sha256"
CLIENT_ARCHIVE="$DIST/PCL-Relay-client.tar.gz"
CLIENT_CHECKSUM="$CLIENT_ARCHIVE.sha256"
VERSION="$(tr -d '[:space:]' < "$ROOT/pcl_codex_bridge/VERSION")"

mkdir -p "$DIST" "$(dirname "$APP")"
rm -f "$ARCHIVE" "$CHECKSUM" "$CLIENT_ARCHIVE" "$CLIENT_CHECKSUM"

"$ROOT/scripts/build_macos_app.sh" "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
if [[ "$APP_VERSION" != "$VERSION" ]]; then
  echo "Release version mismatch: expected $VERSION, got $APP_VERSION" >&2
  exit 1
fi
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
/usr/bin/tar -czf "$CLIENT_ARCHIVE" -C "$ROOT" pcl_codex_bridge LICENSE NOTICE

(
  cd "$DIST"
  /usr/bin/shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")"
  /usr/bin/shasum -a 256 "$(basename "$CLIENT_ARCHIVE")" > "$(basename "$CLIENT_CHECKSUM")"
)

echo "PCL Relay $VERSION"
echo "$ARCHIVE"
echo "$CHECKSUM"
echo "$CLIENT_ARCHIVE"
echo "$CLIENT_CHECKSUM"
