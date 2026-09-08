#!/bin/zsh
# Build a GUI + matching control CLI candidate, preserving the complete data plane.
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
# The new view needs the matching read-only runtime snapshot CLI. This changes
# only the candidate bundle, never the installed user service or its resources.
chmod -R u+w "$DEST/Contents/Resources/bridge/src"
rsync -a --delete --exclude __pycache__ --exclude '*.pyc' \
  --exclude relay_discovery.py --exclude remote_clients.py --exclude direct_clients.py --exclude bridges.py \
  "$ROOT/pcl_codex_bridge/" "$DEST/Contents/Resources/bridge/src/pcl_codex_bridge/"
chmod -R a-w "$DEST/Contents/Resources/bridge/src"
chmod u+w "$DEST/Contents/Info.plist"
BUILD="$(git describe --always --dirty)"
/usr/libexec/PlistBuddy -c 'Delete :PCLBuildCommit' "$DEST/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :PCLBuildCommit string $BUILD" "$DEST/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Delete :PCLGUIOnly' "$DEST/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c 'Add :PCLGUIOnly bool true' "$DEST/Contents/Info.plist"
codesign --force --sign - "$DEST"
codesign --verify --deep --strict "$DEST"
diff -qr "$BASE/Contents/Resources/bridge/opencodex" "$DEST/Contents/Resources/bridge/opencodex"
diff -qr "$BASE/Contents/Resources/bridge/python" "$DEST/Contents/Resources/bridge/python"
echo "GUI and control CLI candidate verified: $DEST ($VERSION / $BUILD); data plane unchanged, services were not restarted"
