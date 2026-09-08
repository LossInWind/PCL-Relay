#!/bin/sh
set -eu

BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else "PCL Relay requires Python 3.9+")'
"$BASE/pcl-codex" sidecar stage

printf '%s\n' \
  "PCL Relay and the pinned OpenCodex runtime were verified and staged." \
  "No service was started and no Codex configuration was changed." \
  "To enable explicitly:" \
  "  ~/.local/bin/pcl-codex --gateway-url http://127.0.0.1:15722/v1 integration enable"
