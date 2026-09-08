#!/bin/zsh
# Build a GUI-only candidate, preserving the installed bundle's complete data plane.
# Does not stop or launch any process. Destination must be an unused staging path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${PCL_GUI_BASE_APP:-/Applications/PCL Relay.app}"
DEST="${1:?Usage: update_macos_gui.sh /unused/staging/PCL-Relay.app}"
[[ ! -e "$DEST" ]] || { echo 'Destination already exists; preserving it' >&2; exit 1; }
[[ -x "$BASE/Contents/MacOS/PCLCodexManager" ]] || { echo 'Installed base application missing' >&2; exit 1; }
cd "$ROOT"
VERSION="$(tr -d '[:space:]' < pcl_codex_bridge/VERSION)"
BASE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$BASE/Contents/Info.plist")"
[[ "$VERSION" == "$BASE_VERSION" ]] || { echo 'GUI-only update requires matching component release version; use full packaging for release changes' >&2; exit 1; }
codesign --verify --deep --strict "$BASE"
swift build -c release --product PCLCodexManager
mkdir -p "$(dirname "$DEST")"
ditto "$BASE" "$DEST"
cp .build/release/PCLCodexManager "$DEST/Contents/MacOS/PCLCodexManager"
chmod u+w "$DEST/Contents/Info.plist"
BUILD="$(git describe --always --dirty)"
/usr/libexec/PlistBuddy -c 'Delete :PCLBuildCommit' "$DEST/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :PCLBuildCommit string $BUILD" "$DEST/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Delete :PCLGUIOnly' "$DEST/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c 'Add :PCLGUIOnly bool true' "$DEST/Contents/Info.plist"
codesign --force --sign - "$DEST"
codesign --verify --deep --strict "$DEST"
diff -qr "$BASE/Contents/Resources" "$DEST/Contents/Resources"
echo "GUI candidate verified: $DEST ($VERSION / $BUILD); services were not restarted"
