#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESTINATION="${1:-/Applications/PCL Relay.app}"
STAGING="$ROOT/.build/PCL Relay.app"
STAMP="$(date +%Y%m%d-%H%M%S)"
VERSION="$(tr -d '[:space:]' < "$ROOT/pcl_codex_bridge/VERSION")"
OPENCODEX_SOURCE="$ROOT/vendor/opencodex"
OPENCODEX_MANIFEST="$ROOT/vendor/opencodex.UPSTREAM.json"
OPENCODEX_RUNTIME="$ROOT/.build/opencodex-runtime"
OPENCODEX_COMMIT="bba63222d3eeb5c8e397edae35798225e4fa1a6f"
OPENCODEX_TREE="068c7640eec7e0fb5bd737920e6b163630e50d6f"

if [[ ! "$VERSION" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' ]]; then
  echo "Invalid canonical version: $VERSION" >&2
  exit 1
fi

cd "$ROOT"

# The complete upstream tree is kept under vendor/ at one reviewed commit.
# PCL Relay packages that implementation as an independent sidecar; no Python
# transport code is generated from it and no subset of retry/stream logic is copied.
if [[ ! -f "$OPENCODEX_SOURCE/package.json" || ! -f "$OPENCODEX_MANIFEST" ]]; then
  echo "Pinned OpenCodex source or provenance manifest is missing" >&2
  exit 1
fi
if ! git diff --quiet -- vendor/opencodex || ! git diff --cached --quiet -- vendor/opencodex; then
  echo "Pinned OpenCodex tree has local modifications; update the provenance pin instead" >&2
  exit 1
fi
TRACKED_OPENCODEX_TREE="$(git rev-parse HEAD:vendor/opencodex 2>/dev/null || true)"
if [[ "$TRACKED_OPENCODEX_TREE" != "$OPENCODEX_TREE" ]]; then
  echo "Pinned OpenCodex tree mismatch: expected $OPENCODEX_TREE, got ${TRACKED_OPENCODEX_TREE:-missing}" >&2
  exit 1
fi

OPENCODEX_BUILD_BUN="${PCL_OPENCODEX_BUN:-}"
if [[ -z "$OPENCODEX_BUILD_BUN" ]]; then
  OPENCODEX_BUILD_BUN="$(command -v bun 2>/dev/null || true)"
fi
if [[ ! -x "$OPENCODEX_BUILD_BUN" || "$($OPENCODEX_BUILD_BUN --version)" != "1.4.0" ]]; then
  echo "OpenCodex requires the pinned Bun 1.4.0 build tool (set PCL_OPENCODEX_BUN)" >&2
  exit 1
fi
OPENCODEX_RUNTIME_BUN="${PCL_OPENCODEX_RUNTIME_BUN:-}"
if [[ ! -x "$OPENCODEX_RUNTIME_BUN" || "$($OPENCODEX_RUNTIME_BUN --version)" != "1.3.14" ]]; then
  echo "PCL Relay requires the OpenCodex HTTP-fallback Bun 1.3.14 runtime (set PCL_OPENCODEX_RUNTIME_BUN)" >&2
  exit 1
fi

(cd "$OPENCODEX_SOURCE" && "$OPENCODEX_BUILD_BUN" install --frozen-lockfile --production)
if [[ -e "$OPENCODEX_RUNTIME" ]]; then
  mv "$OPENCODEX_RUNTIME" "$ROOT/.build/opencodex-runtime.backup-$STAMP"
fi
mkdir -p "$OPENCODEX_RUNTIME/bin"
# OpenCodex 2.46.0 documents that stable Bun >=1.4 automatically selects its
# canonical ChatGPT upstream WebSocket transport, while Bun 1.3.14 uses the
# same complete implementation with HTTP/SSE upstream. The latter is pinned
# for macOS proxy compatibility; client-facing Responses WebSocket stays on.
cp "$(/bin/realpath "$OPENCODEX_RUNTIME_BUN")" "$OPENCODEX_RUNTIME/bin/bun"
chmod 755 "$OPENCODEX_RUNTIME/bin/bun"
rsync -a "$OPENCODEX_SOURCE/src" "$OPENCODEX_SOURCE/bin" "$OPENCODEX_SOURCE/gui" "$OPENCODEX_SOURCE/assets" "$OPENCODEX_SOURCE/node_modules" "$OPENCODEX_RUNTIME/"
cp "$OPENCODEX_SOURCE/package.json" "$OPENCODEX_SOURCE/bun.lock" "$OPENCODEX_SOURCE/LICENSE" "$OPENCODEX_SOURCE/README.md" "$OPENCODEX_RUNTIME/"
cp "$OPENCODEX_MANIFEST" "$OPENCODEX_RUNTIME/UPSTREAM.json"

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
mkdir -p "$STAGING/Contents/Resources/bridge/python/bin" "$STAGING/Contents/Resources/bridge/python/lib" "$STAGING/Contents/Resources/bridge/src" "$STAGING/Contents/Resources/bridge/lib"
cp "$ROOT/.build/release/PCLCodexManager" "$STAGING/Contents/MacOS/PCLCodexManager"
cp "$ROOT/macos/Info.plist" "$STAGING/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$STAGING/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$STAGING/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :PCLBuildCommit string $(git describe --always --dirty)" "$STAGING/Contents/Info.plist"
cp "$PYTHON_ROOT/bin/python3.12" "$STAGING/Contents/Resources/bridge/python/bin/python3.12"
ln -s python3.12 "$STAGING/Contents/Resources/bridge/python/bin/python3"
cp "$PYTHON_ROOT/lib/libpython3.12.dylib" "$STAGING/Contents/Resources/bridge/python/lib/libpython3.12.dylib"
ZSTD_LIBRARY="${PCL_EMBED_ZSTD_LIBRARY:-/opt/homebrew/opt/zstd/lib/libzstd.1.dylib}"
if [[ ! -f "$ZSTD_LIBRARY" ]]; then
  echo "libzstd runtime not found at $ZSTD_LIBRARY" >&2
  exit 1
fi
cp "$ZSTD_LIBRARY" "$STAGING/Contents/Resources/bridge/lib/libzstd.1.dylib"
rsync -a --exclude site-packages --exclude __pycache__ --exclude '*.pyc' "$PYTHON_ROOT/lib/python3.12/" "$STAGING/Contents/Resources/bridge/python/lib/python3.12/"
rsync -a \
  --exclude __pycache__ \
  --exclude '*.pyc' \
  --exclude relay_discovery.py \
  --exclude remote_clients.py \
  --exclude direct_clients.py \
  --exclude bridges.py \
  "$ROOT/pcl_codex_bridge/" \
  "$STAGING/Contents/Resources/bridge/src/pcl_codex_bridge/"
rsync -a "$OPENCODEX_RUNTIME/" "$STAGING/Contents/Resources/bridge/opencodex/"
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

# Python must never mutate a signed application bundle.  Remove any bytecode
# inherited from a source/runtime tree and make resources read-only after the
# signature is created; runtime state belongs under the user's Library.
find "$STAGING/Contents/Resources" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGING/Contents/Resources" -type f -name '*.pyc' -delete
codesign --force --deep --sign - "$STAGING"
chmod -R a-w "$STAGING/Contents/Resources"
codesign --verify --deep --strict "$STAGING"

APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGING/Contents/Info.plist")"
if [[ "$APP_VERSION" != "$VERSION" ]]; then
  echo "App version mismatch: expected $VERSION, got $APP_VERSION" >&2
  exit 1
fi

if [[ -e "$DESTINATION" ]]; then
  mkdir -p "$ROOT/.build/app-install-backups"
  mv "$DESTINATION" "$ROOT/.build/app-install-backups/PCL Relay.app.backup-$STAMP"
fi
mkdir -p "$(dirname "$DESTINATION")"
mv "$STAGING" "$DESTINATION"
echo "$DESTINATION"
