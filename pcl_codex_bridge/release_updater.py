from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from . import __version__


REPOSITORY = "LossInWind/PCL-Relay"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAC_ASSET_NAME = "PCL-Relay-macOS.zip"
MAX_ASSET_BYTES = 1024 * 1024 * 1024


def _version_tuple(value: str) -> Tuple[int, ...]:
    clean = value.strip().lower().removeprefix("v")
    numeric = clean.split("-", 1)[0]
    parts = []
    for item in numeric.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PCL-Relay/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    with urllib.request.urlopen(_request(url), timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("GitHub Releases returned an invalid response")
    return value


def _assets(release: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    values = release.get("assets")
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _asset(release: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    return next((item for item in _assets(release) if item.get("name") == name), None)


def latest_release_status(current_version: str = __version__) -> Dict[str, Any]:
    api_url = os.environ.get("PCL_RELAY_RELEASE_API", RELEASE_API)
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        release = _read_json(api_url)
        tag = str(release.get("tag_name") or "")
        latest = tag.removeprefix("v")
        asset = _asset(release, MAC_ASSET_NAME)
        checksum = _asset(release, MAC_ASSET_NAME + ".sha256")
        if not latest:
            raise RuntimeError("Latest GitHub Release has no version tag")
        if not asset or not asset.get("browser_download_url"):
            raise RuntimeError(f"Latest GitHub Release is missing {MAC_ASSET_NAME}")
        return {
            "available": True,
            "source": f"github:{REPOSITORY}",
            "current_version": current_version,
            "latest_version": latest,
            "update_available": _version_tuple(latest) > _version_tuple(current_version),
            "release_url": str(release.get("html_url") or ""),
            "published_at": str(release.get("published_at") or ""),
            "asset_name": str(asset.get("name") or ""),
            "asset_url": str(asset.get("browser_download_url") or ""),
            "asset_size": int(asset.get("size") or 0),
            "asset_digest": str(asset.get("digest") or ""),
            "checksum_url": str((checksum or {}).get("browser_download_url") or ""),
            "checked_at": checked_at,
            "error": "",
        }
    except Exception as exc:
        return {
            "available": False,
            "source": f"github:{REPOSITORY}",
            "current_version": current_version,
            "latest_version": "",
            "update_available": False,
            "release_url": "",
            "published_at": "",
            "asset_name": "",
            "asset_url": "",
            "asset_size": 0,
            "asset_digest": "",
            "checksum_url": "",
            "checked_at": checked_at,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _download(url: str, destination: Path, expected_size: int = 0) -> str:
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(_request(url), timeout=120) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_ASSET_BYTES:
                raise RuntimeError("Release asset exceeds the safety limit")
            digest.update(chunk)
            output.write(chunk)
    if expected_size and received != expected_size:
        raise RuntimeError(f"Release asset size mismatch: expected {expected_size}, received {received}")
    return digest.hexdigest()


def _expected_digest(status: Dict[str, Any], directory: Path) -> str:
    digest = str(status.get("asset_digest") or "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    checksum_url = str(status.get("checksum_url") or "")
    if not checksum_url:
        raise RuntimeError("Release has no SHA-256 digest or checksum asset")
    checksum_path = directory / (MAC_ASSET_NAME + ".sha256")
    _download(checksum_url, checksum_path)
    first = checksum_path.read_text(encoding="utf-8", errors="replace").strip().split()[0]
    if len(first) != 64 or any(character not in "0123456789abcdefABCDEF" for character in first):
        raise RuntimeError("Release checksum asset is invalid")
    return first.lower()


def _verify_app(app: Path, expected_version: str) -> None:
    info = app / "Contents" / "Info.plist"
    executable = app / "Contents" / "MacOS" / "PCLCodexManager"
    if not info.is_file() or not executable.is_file():
        raise RuntimeError("Downloaded archive does not contain a complete PCL Relay.app")
    with info.open("rb") as handle:
        version = str(plistlib.load(handle).get("CFBundleShortVersionString") or "")
    if version != expected_version:
        raise RuntimeError(f"Downloaded app version mismatch: expected {expected_version}, got {version}")
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True,
        text=True,
        check=False,
    )
    if verified.returncode != 0:
        raise RuntimeError(verified.stderr.strip() or "Downloaded app signature verification failed")


def install_latest_release(force: bool = False) -> Dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("The desktop app updater is available only on macOS")
    status = latest_release_status()
    if not status["available"]:
        raise RuntimeError(status["error"] or "GitHub Release is unavailable")
    if not status["update_available"] and not force:
        return {**status, "installed": False, "restart_required": False, "reason": "already_latest"}

    install_path = Path(os.environ.get("PCL_RELAY_APP_PATH", "/Applications/PCL Relay.app")).expanduser()
    if not install_path.parent.exists() or not os.access(install_path.parent, os.W_OK):
        raise RuntimeError(f"Cannot write to the application directory: {install_path.parent}")

    cache = Path.home() / "Library" / "Caches" / "PCL Relay" / "Updates"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcl-relay-update-", dir=cache) as temporary:
        directory = Path(temporary)
        archive = directory / MAC_ASSET_NAME
        actual_digest = _download(str(status["asset_url"]), archive, int(status["asset_size"] or 0))
        expected_digest = _expected_digest(status, directory)
        if actual_digest.lower() != expected_digest:
            raise RuntimeError("Release SHA-256 verification failed")

        expanded = directory / "expanded"
        expanded.mkdir()
        result = subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive), str(expanded)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not extract the release archive")
        candidates = list(expanded.glob("*.app"))
        if len(candidates) != 1:
            raise RuntimeError("Release archive must contain exactly one macOS app")
        downloaded = candidates[0]
        _verify_app(downloaded, str(status["latest_version"]))

        staging = install_path.parent / f".{install_path.name}.update-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(downloaded, staging, symlinks=True)
        _verify_app(staging, str(status["latest_version"]))

        backup_root = cache / "Backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"{install_path.name}.{time.time_ns()}"
        moved_existing = False
        installed_new = False
        try:
            if install_path.exists():
                shutil.move(str(install_path), str(backup))
                moved_existing = True
            shutil.move(str(staging), str(install_path))
            installed_new = True
            _verify_app(install_path, str(status["latest_version"]))
        except Exception:
            if installed_new and install_path.exists():
                shutil.rmtree(install_path, ignore_errors=True)
            if moved_existing and backup.exists():
                shutil.move(str(backup), str(install_path))
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    return {
        **status,
        "installed": True,
        "installed_path": str(install_path),
        "verified_sha256": actual_digest,
        "restart_required": True,
        "remote_upgrade_source": "installed_release_bundle",
    }
