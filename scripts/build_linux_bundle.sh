#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
VERSION="$(tr -d '[:space:]' < "$ROOT/pcl_codex_bridge/VERSION")"
OPENCODEX_SOURCE="$ROOT/vendor/opencodex"
OPENCODEX_MANIFEST="$ROOT/vendor/opencodex.UPSTREAM.json"
OPENCODEX_COMMIT="bba63222d3eeb5c8e397edae35798225e4fa1a6f"
OPENCODEX_TREE="068c7640eec7e0fb5bd737920e6b163630e50d6f"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Linux bundles must be built on Linux" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64|amd64) ARCH="x86_64" ;;
  aarch64|arm64) ARCH="aarch64" ;;
  *) echo "Unsupported Linux architecture: $(uname -m)" >&2; exit 1 ;;
esac

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid canonical version: $VERSION" >&2
  exit 1
fi
if [[ ! -f "$OPENCODEX_SOURCE/package.json" || ! -f "$OPENCODEX_MANIFEST" ]]; then
  echo "Pinned OpenCodex source or provenance manifest is missing" >&2
  exit 1
fi
if ! git -C "$ROOT" diff --quiet -- vendor/opencodex || ! git -C "$ROOT" diff --cached --quiet -- vendor/opencodex; then
  echo "Pinned OpenCodex tree has local modifications; update the provenance pin instead" >&2
  exit 1
fi
TRACKED_TREE="$(git -C "$ROOT" rev-parse HEAD:vendor/opencodex 2>/dev/null || true)"
if [[ "$TRACKED_TREE" != "$OPENCODEX_TREE" ]]; then
  echo "Pinned OpenCodex tree mismatch: expected $OPENCODEX_TREE, got ${TRACKED_TREE:-missing}" >&2
  exit 1
fi
if ! grep -q "\"commit\": \"$OPENCODEX_COMMIT\"" "$OPENCODEX_MANIFEST"; then
  echo "OpenCodex provenance commit mismatch" >&2
  exit 1
fi

BUILD_BUN="${PCL_OPENCODEX_BUN:-$(command -v bun 2>/dev/null || true)}"
RUNTIME_BUN="${PCL_OPENCODEX_RUNTIME_BUN:-}"
if [[ ! -x "$BUILD_BUN" || "$($BUILD_BUN --version)" != "1.4.0" ]]; then
  echo "OpenCodex requires pinned Bun 1.4.0 for dependency installation (set PCL_OPENCODEX_BUN)" >&2
  exit 1
fi
if [[ ! -x "$RUNTIME_BUN" || "$($RUNTIME_BUN --version)" != "1.3.14" ]]; then
  echo "PCL Relay requires target-platform Bun 1.3.14 at PCL_OPENCODEX_RUNTIME_BUN" >&2
  exit 1
fi

(cd "$OPENCODEX_SOURCE" && "$BUILD_BUN" install --frozen-lockfile --production)

mkdir -p "$ROOT/.build" "$DIST"
STAGING_PARENT="$(mktemp -d "$ROOT/.build/linux-bundle.XXXXXX")"
trap 'rm -rf "$STAGING_PARENT"' EXIT
BUNDLE_NAME="PCL-Relay-linux-$ARCH"
BUNDLE="$STAGING_PARENT/$BUNDLE_NAME"
RUNTIME="$BUNDLE/opencodex"
ARCHIVE="$DIST/$BUNDLE_NAME.tar.gz"

mkdir -p "$RUNTIME/bin"
cp "$(realpath "$RUNTIME_BUN")" "$RUNTIME/bin/bun"
chmod 755 "$RUNTIME/bin/bun"
rsync -a \
  "$OPENCODEX_SOURCE/src" \
  "$OPENCODEX_SOURCE/bin" \
  "$OPENCODEX_SOURCE/gui" \
  "$OPENCODEX_SOURCE/assets" \
  "$OPENCODEX_SOURCE/node_modules" \
  "$RUNTIME/"
cp \
  "$OPENCODEX_SOURCE/package.json" \
  "$OPENCODEX_SOURCE/bun.lock" \
  "$OPENCODEX_SOURCE/LICENSE" \
  "$OPENCODEX_SOURCE/README.md" \
  "$RUNTIME/"
cp "$OPENCODEX_MANIFEST" "$RUNTIME/UPSTREAM.json"
rsync -a \
  --exclude='__pycache__' \
  --exclude='*.py[co]' \
  --exclude=relay_discovery.py \
  --exclude=remote_clients.py \
  --exclude=direct_clients.py \
  --exclude=bridges.py \
  "$ROOT/pcl_codex_bridge" \
  "$BUNDLE/"
cp "$ROOT/scripts/pcl-codex-linux" "$BUNDLE/pcl-codex"
cp "$ROOT/scripts/install_linux_bundle.sh" "$BUNDLE/install.sh"
cp "$ROOT/LICENSE" "$ROOT/NOTICE" "$BUNDLE/"
chmod 755 "$BUNDLE/pcl-codex" "$BUNDLE/install.sh"

"$RUNTIME/bin/bun" --version | grep -qx '1.3.14'
PYTHONPATH="$BUNDLE" python3 -c 'from pcl_codex_bridge.opencodex_sidecar import runtime_at; from pathlib import Path; runtime_at(Path("'"$RUNTIME"'"))'

rm -f "$ARCHIVE" "$ARCHIVE.sha256"
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner -czf "$ARCHIVE" -C "$STAGING_PARENT" "$BUNDLE_NAME"
(cd "$DIST" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256")

echo "$ARCHIVE"
echo "$ARCHIVE.sha256"
