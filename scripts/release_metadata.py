#!/usr/bin/env python3
"""Emit public fallback metadata only after all platform assets are verified."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pcl_codex_bridge.release_updater import REPOSITORY, RELEASE_ASSET_NAMES


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    version = (ROOT / "pcl_codex_bridge/VERSION").read_text().strip()
    assets = []
    for name in sorted(RELEASE_ASSET_NAMES):
        path = directory / name
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        expected = (directory / (name + ".sha256")).read_text().split()[0]
        if expected != digest.hexdigest():
            raise RuntimeError(f"Checksum mismatch: {name}")
        assets.append({
            "name": name, "size": path.stat().st_size,
            "digest": "sha256:" + digest.hexdigest(),
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}",
        })
    result = {"tag_name": "v" + version, "draft": False, "prerelease": False,
              "html_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
              "assets": assets}
    (directory / "release-metadata.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
