from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from . import __version__


REPOSITORY = "LossInWind/PCL-Relay"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAC_ASSET_NAME = "PCL-Relay-macOS.zip"
LINUX_X86_64_ASSET_NAME = "PCL-Relay-linux-x86_64.tar.gz"
LINUX_AARCH64_ASSET_NAME = "PCL-Relay-linux-aarch64.tar.gz"
# Import compatibility for quarantined remote deployment modules. No release
# workflow publishes or selects this obsolete asset.
CLIENT_ASSET_NAME = "PCL-Relay-client.tar.gz"
MAX_ASSET_BYTES = 1024 * 1024 * 1024
RELEASE_ASSET_NAMES = {
    MAC_ASSET_NAME,
    LINUX_X86_64_ASSET_NAME,
    LINUX_AARCH64_ASSET_NAME,
}


def release_cache_root() -> Path:
    return Path(
        os.environ.get(
            "PCL_RELAY_RELEASE_CACHE",
            Path.home() / ".cache" / "pcl-relay" / "verified-releases",
        )
    ).expanduser()


def release_cache_path(version: str, asset_name: str) -> Path:
    if not version or Path(version).name != version:
        raise RuntimeError("Invalid cached release version")
    if asset_name not in RELEASE_ASSET_NAMES:
        raise RuntimeError("Invalid cached release asset name")
    return release_cache_root() / version / asset_name


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def normalize_sha256(value: str) -> str:
    digest = value.strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1].strip()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RuntimeError("Release SHA-256 digest is invalid")
    return digest


def _cache_metadata_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".verified.json")


def _write_cache_metadata(archive: Path, version: str, asset_name: str, digest: str) -> None:
    metadata = {
        "version": version,
        "asset_name": asset_name,
        "sha256": normalize_sha256(digest),
        "size": archive.stat().st_size,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = _cache_metadata_path(archive)
    temporary = path.with_name(f".{path.name}.part-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verified_cached_release(version: str, asset_name: str) -> Dict[str, Any]:
    """Return a durable artifact only after its verification record is rechecked."""
    archive = release_cache_path(version, asset_name)
    metadata_path = _cache_metadata_path(archive)
    if not archive.is_file() or not metadata_path.is_file():
        raise FileNotFoundError("Verified release artifact is not cached")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cached release verification record is invalid") from exc
    if not isinstance(metadata, dict):
        raise RuntimeError("Cached release verification record is invalid")
    expected = normalize_sha256(str(metadata.get("sha256") or ""))
    expected_size = int(metadata.get("size") or 0)
    if (
        metadata.get("version") != version
        or metadata.get("asset_name") != asset_name
        or expected_size <= 0
        or expected_size > MAX_ASSET_BYTES
        or archive.stat().st_size != expected_size
    ):
        raise RuntimeError("Cached release verification record does not match the artifact")
    actual = file_sha256(archive)
    if not hmac.compare_digest(actual, expected):
        raise RuntimeError("Cached release artifact no longer matches its verified SHA-256")
    return {"path": str(archive), "sha256": actual, "size": expected_size, "cached": True}


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


def release_asset_name(
    platform_name: Optional[str] = None,
    machine: Optional[str] = None,
) -> str:
    system = platform_name or sys.platform
    architecture = (machine or platform.machine()).lower()
    if system == "darwin":
        return MAC_ASSET_NAME
    if system.startswith("linux"):
        if architecture in {"x86_64", "amd64"}:
            return LINUX_X86_64_ASSET_NAME
        if architecture in {"aarch64", "arm64"}:
            return LINUX_AARCH64_ASSET_NAME
        raise RuntimeError(f"Unsupported Linux architecture: {architecture or 'unknown'}")
    raise RuntimeError(f"Unsupported PCL Relay platform: {system}")


def _release_asset_status(
    release: Dict[str, Any],
    asset_name: str,
    current_version: str,
    checked_at: str,
) -> Dict[str, Any]:
    tag = str(release.get("tag_name") or "")
    latest = tag.removeprefix("v")
    asset = _asset(release, asset_name)
    checksum = _asset(release, asset_name + ".sha256")
    if not latest:
        raise RuntimeError("Latest GitHub Release has no version tag")
    if not asset or not asset.get("browser_download_url"):
        raise RuntimeError(f"Latest GitHub Release is missing {asset_name}")
    size = int(asset.get("size") or 0)
    if size <= 0 or size > MAX_ASSET_BYTES:
        raise RuntimeError(f"Latest GitHub Release has an invalid size for {asset_name}")
    current_key = _version_tuple(current_version)
    latest_key = _version_tuple(latest)
    return {
        "available": True,
        "source": f"github:{REPOSITORY}",
        "current_version": current_version,
        "latest_version": latest,
        "update_available": latest_key > current_key,
        "local_newer_than_published": current_key > latest_key,
        "topology_deployment_ready": latest_key >= current_key,
        "release_url": str(release.get("html_url") or ""),
        "published_at": str(release.get("published_at") or ""),
        "asset_name": str(asset.get("name") or ""),
        "asset_url": str(asset.get("browser_download_url") or ""),
        "asset_size": size,
        "asset_digest": str(asset.get("digest") or ""),
        "checksum_url": str((checksum or {}).get("browser_download_url") or ""),
        "checked_at": checked_at,
        "error": "",
    }


def latest_release_status(current_version: str = __version__) -> Dict[str, Any]:
    api_url = os.environ.get("PCL_RELAY_RELEASE_API", RELEASE_API)
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        asset_name = release_asset_name()
        release = _read_json(api_url)
        return _release_asset_status(release, asset_name, current_version, checked_at)
    except Exception as exc:
        return {
            "available": False,
            "source": f"github:{REPOSITORY}",
            "current_version": current_version,
            "latest_version": "",
            "update_available": False,
            "local_newer_than_published": False,
            "topology_deployment_ready": False,
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


def latest_release_manifest(current_version: str = __version__) -> Dict[str, Any]:
    """Resolve immutable platform metadata before broadcasting an update offer."""
    api_url = os.environ.get("PCL_RELAY_RELEASE_API", RELEASE_API)
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    release = _read_json(api_url)
    assets: Dict[str, Dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="pcl-relay-release-manifest-") as temporary:
        directory = Path(temporary)
        for name in sorted(RELEASE_ASSET_NAMES):
            status = _release_asset_status(release, name, current_version, checked_at)
            digest = _expected_digest(status, directory)
            assets[name] = {
                "asset_name": name,
                "asset_url": status["asset_url"],
                "asset_size": status["asset_size"],
                "asset_sha256": digest,
            }
    version = str(release.get("tag_name") or "").removeprefix("v")
    return {
        "available": True,
        "source": f"github:{REPOSITORY}",
        "current_version": current_version,
        "latest_version": version,
        "update_available": _version_tuple(version) > _version_tuple(current_version),
        "release_url": str(release.get("html_url") or ""),
        "published_at": str(release.get("published_at") or ""),
        "checked_at": checked_at,
        "assets": assets,
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
    if digest:
        return normalize_sha256(digest)
    checksum_url = str(status.get("checksum_url") or "")
    if not checksum_url:
        raise RuntimeError("Release has no SHA-256 digest or checksum asset")
    asset_name = str(status.get("asset_name") or release_asset_name())
    checksum_path = directory / (asset_name + ".sha256")
    _download(checksum_url, checksum_path)
    first = checksum_path.read_text(encoding="utf-8", errors="replace").strip().split()[0]
    return normalize_sha256(first)


def cache_release_asset(status: Dict[str, Any]) -> Dict[str, Any]:
    """Download once, verify, and retain an artifact for Tailnet fallback."""
    version = str(status.get("latest_version") or "")
    asset_name = str(status.get("asset_name") or "")
    destination = release_cache_path(version, asset_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = _expected_digest(status, destination.parent)
    expected_size = int(status.get("asset_size") or 0)
    if destination.is_file():
        actual = file_sha256(destination)
        if actual == expected and (not expected_size or destination.stat().st_size == expected_size):
            _write_cache_metadata(destination, version, asset_name, actual)
            return {
                "path": str(destination),
                "sha256": actual,
                "size": destination.stat().st_size,
                "cached": True,
                "downloaded": False,
            }
    temporary = destination.with_name(f".{destination.name}.part-{os.getpid()}-{uuid.uuid4().hex}")
    if temporary.exists():
        temporary.unlink()
    try:
        actual = _download(str(status["asset_url"]), temporary, expected_size)
        if actual.lower() != expected:
            raise RuntimeError("Release SHA-256 verification failed")
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
        _write_cache_metadata(destination, version, asset_name, actual)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": str(destination),
        "sha256": expected,
        "size": destination.stat().st_size,
        "cached": True,
        "downloaded": True,
    }


def promote_verified_release(
    status: Dict[str, Any],
    temporary: Path,
    expected_digest: str,
    expected_size: int,
) -> Dict[str, Any]:
    """Atomically promote a peer download into the durable verified cache."""
    version = str(status.get("latest_version") or "")
    asset_name = str(status.get("asset_name") or "")
    destination = release_cache_path(version, asset_name)
    digest = normalize_sha256(expected_digest)
    if expected_size <= 0 or expected_size > MAX_ASSET_BYTES:
        raise RuntimeError("Release asset size is invalid")
    if not temporary.is_file() or temporary.stat().st_size != expected_size:
        raise RuntimeError("Peer release asset size verification failed")
    actual = file_sha256(temporary)
    if not hmac.compare_digest(actual, digest):
        raise RuntimeError("Peer release asset SHA-256 verification failed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    _write_cache_metadata(destination, version, asset_name, actual)
    return {
        "path": str(destination),
        "sha256": actual,
        "size": expected_size,
        "cached": True,
        "downloaded": True,
    }


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


def _safe_extract_linux(archive: Path, destination: Path) -> Path:
    destination_root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if target != destination_root and destination_root not in target.parents:
                raise RuntimeError("Linux release archive contains an unsafe path")
            if member.issym() or member.islnk():
                link_base = target.parent if member.issym() else destination
                link_target = (link_base / member.linkname).resolve()
                if link_target != destination_root and destination_root not in link_target.parents:
                    raise RuntimeError("Linux release archive contains an unsafe link")
        bundle.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("Linux release archive must contain exactly one bundle directory")
    return roots[0]


def _verify_linux_bundle(bundle: Path, expected_version: str) -> None:
    required = (
        bundle / "pcl-codex",
        bundle / "install.sh",
        bundle / "pcl_codex_bridge" / "VERSION",
        bundle / "opencodex" / "UPSTREAM.json",
        bundle / "opencodex" / "package.json",
        bundle / "opencodex" / "src" / "cli" / "index.ts",
        bundle / "opencodex" / "bin" / "bun",
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError("Downloaded Linux archive does not contain a complete PCL Relay bundle")
    version = (bundle / "pcl_codex_bridge" / "VERSION").read_text(encoding="utf-8").strip()
    if version != expected_version:
        raise RuntimeError(f"Downloaded bundle version mismatch: expected {expected_version}, got {version}")
    manifest = json.loads((bundle / "opencodex" / "UPSTREAM.json").read_text(encoding="utf-8"))
    package = json.loads((bundle / "opencodex" / "package.json").read_text(encoding="utf-8"))
    if manifest.get("commit") != "bba63222d3eeb5c8e397edae35798225e4fa1a6f":
        raise RuntimeError("Downloaded bundle has an unexpected OpenCodex commit")
    if manifest.get("version") != "2.46.0" or package.get("version") != "2.46.0":
        raise RuntimeError("Downloaded bundle has an unexpected OpenCodex version")
    runtime = subprocess.run(
        [str(bundle / "opencodex" / "bin" / "bun"), "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if runtime.returncode != 0 or runtime.stdout.strip() != "1.3.14":
        raise RuntimeError("Downloaded bundle does not contain the pinned Bun 1.3.14 runtime")


def _stage_linux_archive(status: Dict[str, Any], archive: Path, actual_digest: str) -> Dict[str, Any]:
    cache = Path(os.environ.get(
        "PCL_RELAY_UPDATE_CACHE",
        Path.home() / ".cache" / "pcl-relay" / "updates",
    )).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcl-relay-update-", dir=cache) as temporary:
        directory = Path(temporary)
        expanded = directory / "expanded"
        expanded.mkdir()
        bundle = _safe_extract_linux(archive, expanded)
        _verify_linux_bundle(bundle, str(status["latest_version"]))
        staged = subprocess.run(
            [str(bundle / "install.sh")],
            cwd=bundle,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if staged.returncode != 0:
            raise RuntimeError(staged.stderr.strip() or staged.stdout.strip() or "Linux bundle staging failed")
    return {
        **status,
        "installed": True,
        "staged": True,
        "verified_sha256": actual_digest,
        "restart_required": False,
        "activation_required": True,
        "service_restarted": False,
        "codex_config_changed": False,
        "remote_upgrade_source": "installed_release_bundle",
    }


def _install_linux_release(status: Dict[str, Any]) -> Dict[str, Any]:
    cached = cache_release_asset(status)
    return _stage_linux_archive(status, Path(str(cached["path"])), str(cached["sha256"]))


def _install_macos_release(status: Dict[str, Any], archive: Path, actual_digest: str) -> Dict[str, Any]:
    install_path = Path(os.environ.get("PCL_RELAY_APP_PATH", "/Applications/PCL Relay.app")).expanduser()
    if not install_path.parent.exists() or not os.access(install_path.parent, os.W_OK):
        raise RuntimeError(f"Cannot write to the application directory: {install_path.parent}")
    cache = Path.home() / "Library" / "Caches" / "PCL Relay" / "Updates"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pcl-relay-update-", dir=cache) as temporary:
        directory = Path(temporary)
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
        "service_restarted": False,
        "codex_config_changed": False,
        "remote_upgrade_source": "verified_release_archive",
    }


def install_cached_release(status: Dict[str, Any], archive: Path, expected_digest: str) -> Dict[str, Any]:
    if not archive.is_file():
        raise RuntimeError("Cached release artifact is missing")
    actual = file_sha256(archive)
    if actual.lower() != expected_digest.lower():
        raise RuntimeError("Cached release SHA-256 verification failed")
    expected_size = int(status.get("asset_size") or 0)
    if expected_size and archive.stat().st_size != expected_size:
        raise RuntimeError("Cached release size verification failed")
    if sys.platform.startswith("linux"):
        return _stage_linux_archive(status, archive, actual)
    if sys.platform == "darwin":
        return _install_macos_release(status, archive, actual)
    raise RuntimeError("PCL Relay updates are supported only on macOS and Linux")


def install_latest_release(force: bool = False) -> Dict[str, Any]:
    status = latest_release_status()
    if not status["available"]:
        raise RuntimeError(status["error"] or "GitHub Release is unavailable")
    if not status["update_available"] and not force:
        return {**status, "installed": False, "restart_required": False, "reason": "already_latest"}
    if sys.platform.startswith("linux"):
        return _install_linux_release(status)
    if sys.platform != "darwin":
        raise RuntimeError("PCL Relay updates are supported only on macOS and Linux")

    cached = cache_release_asset(status)
    return _install_macos_release(status, Path(str(cached["path"])), str(cached["sha256"]))
