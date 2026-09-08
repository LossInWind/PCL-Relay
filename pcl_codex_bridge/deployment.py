from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__
from .release_updater import _version_tuple, cache_release_asset, latest_release_manifest
from .topology_sync import add_peer, heartbeat, normalize_control_url, sync_once


TARGETS_PATH = Path.home() / ".config" / "pcl-codex-bridge" / "deployment-targets.json"
SAFE_SSH_TARGET = re.compile(r"^[A-Za-z0-9_.@:-]+$")
SSH_OPTIONS = (
    "-o", "ClearAllForwardings=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=2",
)


def _validate_ssh_target(value: str) -> str:
    target = value.strip()
    if not target or not SAFE_SSH_TARGET.fullmatch(target):
        raise RuntimeError("SSH target must be a literal host alias or user@host")
    return target


def _target_id(ssh_target: str, control_url: str) -> str:
    return hashlib.sha256(f"{ssh_target}\n{control_url}".encode()).hexdigest()[:16]


def _control_url_from_ssh(target: str) -> str:
    effective = _ssh_effective(target)
    hostname = effective["hostname"]
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    return normalize_control_url(f"http://{host}:15726")


def _ssh_effective(target: str) -> Dict[str, str]:
    completed = subprocess.run(
        ["ssh", "-G", _validate_ssh_target(target)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Cannot resolve the SSH alias")
    values: Dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key.lower() in {"hostname", "user"} and value.strip():
            values[key.lower()] = value.strip()
    hostname = values.get("hostname", "").rstrip(".")
    if not hostname:
        raise RuntimeError("The SSH alias has no effective HostName")
    if any(character in hostname for character in "/?#@"):
        raise RuntimeError("The SSH alias resolved to an invalid control host")
    return {"hostname": hostname, "user": values.get("user", "")}


def _literal_ssh_aliases(config: Optional[Path] = None) -> List[str]:
    path = config or Path.home() / ".ssh" / "config"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuntimeError(f"Cannot read the local SSH config: {exc}") from exc
    aliases: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if not any(character in alias for character in "*?!"):
                aliases.append(_validate_ssh_target(alias))
    return list(dict.fromkeys(aliases))


def import_ssh_targets(config: Optional[Path] = None) -> Dict[str, Any]:
    """Import literal aliases only after an explicit user action.

    Resolution uses ``ssh -G`` and does not open a network connection. Multiple
    aliases for one effective HostName are collapsed, preferring the ordinary
    login name over bootstrap/recovery aliases.
    """
    candidates: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    aliases = sorted(
        _literal_ssh_aliases(config),
        key=lambda value: (any(word in value.lower() for word in ("bootstrap", "recovery")), value),
    )
    seen_hosts = set()
    for alias in aliases:
        try:
            effective = _ssh_effective(alias)
            hostname = effective["hostname"].lower()
            if hostname in seen_hosts:
                continue
            seen_hosts.add(hostname)
            control_url = _control_url_from_ssh(alias)
            candidates.append({
                "id": _target_id(alias, control_url),
                "name": alias,
                "ssh_target": alias,
                "control_url": control_url,
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
        except Exception as exc:
            errors.append({"ssh_target": alias, "error": f"{type(exc).__name__}: {exc}"})
    existing = _load_targets()
    by_host_url = {item["control_url"]: item for item in existing}
    imported = 0
    for candidate in candidates:
        if candidate["control_url"] not in by_host_url:
            imported += 1
        by_host_url[candidate["control_url"]] = candidate
    merged = list(by_host_url.values())
    _save_targets(merged)
    return {
        "imported": imported,
        "count": len(merged),
        "targets": merged,
        "errors": errors,
        "network_connections_opened": False,
        "credentials_read": False,
    }


def _load_targets() -> List[Dict[str, str]]:
    try:
        raw = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Cannot read deployment targets: {exc}") from exc
    values = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        raise RuntimeError("Deployment target registry has an invalid format")
    result: List[Dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            ssh_target = _validate_ssh_target(str(item.get("ssh_target") or ""))
            control_url = normalize_control_url(str(item.get("control_url") or ""))
        except RuntimeError:
            continue
        result.append({
            "id": str(item.get("id") or _target_id(ssh_target, control_url)),
            "name": str(item.get("name") or ssh_target)[:80],
            "ssh_target": ssh_target,
            "control_url": control_url,
            "added_at": str(item.get("added_at") or ""),
        })
    return result


def _save_targets(targets: Iterable[Dict[str, str]]) -> None:
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = TARGETS_PATH.with_name(f".{TARGETS_PATH.name}.tmp-{os.getpid()}")
    payload = {"schema": 1, "targets": list(targets)}
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(TARGETS_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()


REMOTE_PROBE = r'''
import json, pathlib, platform, subprocess
home = pathlib.Path.home()
version_file = home / ".local" / "share" / "pcl-codex-bridge" / "VERSION"
try:
    version = version_file.read_text(encoding="utf-8").strip()
except Exception:
    version = ""
print(json.dumps({
    "system": platform.system(),
    "architecture": platform.machine(),
    "python": platform.python_version(),
    "version": version,
    "installed": bool(version),
}))
'''


def _remote_python_command(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    launcher = f"import base64;exec(compile(base64.b64decode({encoded!r}),'<pcl-relay-bootstrap>','exec'))"
    return "python3 -c " + shlex.quote(launcher)


def _run_ssh(
    target: str,
    source: str,
    timeout: int,
    stdin_path: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    command = ["ssh", *SSH_OPTIONS, _validate_ssh_target(target), _remote_python_command(source)]
    if stdin_path is None:
        return subprocess.run(command, input=b"", capture_output=True, timeout=timeout, check=False)
    with stdin_path.open("rb") as handle:
        return subprocess.run(command, stdin=handle, capture_output=True, timeout=timeout, check=False)


def probe_target(target: Dict[str, str], timeout: int = 20) -> Dict[str, Any]:
    record: Dict[str, Any] = {**target, "ssh": False, "receiver_online": False, "error": ""}
    try:
        relay = heartbeat(target["control_url"], timeout=min(timeout, 8))
        record.update({
            "receiver_online": True,
            "relay_version": str(relay.get("version") or ""),
            "relay_node_id": str(relay.get("node_id") or ""),
            "latency_ms": relay.get("latency_ms"),
        })
    except Exception as exc:
        record["receiver_error"] = f"{type(exc).__name__}: {exc}"
    try:
        completed = _run_ssh(target["ssh_target"], REMOTE_PROBE, timeout)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", "replace").strip() or "SSH probe failed")
        payload = json.loads(completed.stdout.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("SSH probe returned an invalid document")
        record.update(payload)
        record["ssh"] = True
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def list_targets(probe: bool = False, timeout: int = 20) -> Dict[str, Any]:
    targets: List[Dict[str, Any]] = _load_targets()
    if probe and targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(targets))) as executor:
            targets = list(executor.map(lambda item: probe_target(item, timeout), targets))
    return {
        "schema": 1,
        "targets": targets,
        "count": len(targets),
        "discovery": False,
        "credentials_synchronized": False,
        "network_managed": False,
    }


def add_target(ssh_target: str, control_url: str = "", name: str = "") -> Dict[str, Any]:
    target = _validate_ssh_target(ssh_target)
    url = normalize_control_url(control_url) if control_url.strip() else _control_url_from_ssh(target)
    record = {
        "id": _target_id(target, url),
        "name": name.strip()[:80] or target,
        "ssh_target": target,
        "control_url": url,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    targets = [item for item in _load_targets() if item["id"] != record["id"]]
    targets.append(record)
    _save_targets(targets)
    return {"added": True, "target": record, "probe": probe_target(record)}


def remove_target(target_id: str) -> Dict[str, Any]:
    targets = _load_targets()
    removed = next((item for item in targets if item["id"] == target_id), None)
    if removed is None:
        raise RuntimeError("Unknown deployment target")
    _save_targets(item for item in targets if item["id"] != target_id)
    return {"removed": True, "target": removed}


REMOTE_BOOTSTRAP = r'''
import hashlib, io, json, os, pathlib, platform, plistlib, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request

manifest = json.loads(__PCL_MANIFEST__)
peer_fallback = __PCL_PEER_FALLBACK__
system = platform.system()
machine = platform.machine().lower()
if system == "Darwin":
    asset_name = "PCL-Relay-macOS.zip"
elif system == "Linux" and machine in {"x86_64", "amd64"}:
    asset_name = "PCL-Relay-linux-x86_64.tar.gz"
elif system == "Linux" and machine in {"aarch64", "arm64"}:
    asset_name = "PCL-Relay-linux-aarch64.tar.gz"
else:
    raise SystemExit("unsupported target platform: " + system + "/" + machine)

asset = manifest["assets"][asset_name]
expected_size = int(asset["asset_size"])
expected_hash = str(asset["asset_sha256"]).lower()
if peer_fallback:
    archive_data = sys.stdin.buffer.read(expected_size + 1)
    source = "registered-node-ssh-fallback"
else:
    try:
        request = urllib.request.Request(asset["asset_url"], headers={"User-Agent": "PCL-Relay-bootstrap"})
        deadline = time.monotonic() + 120
        with urllib.request.urlopen(request, timeout=30) as response:
            chunks = []
            received = 0
            while received <= expected_size:
                if time.monotonic() >= deadline:
                    raise TimeoutError("GitHub download exceeded the 120-second total budget")
                chunk = response.read(min(65536, expected_size + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            archive_data = b"".join(chunks)
        source = "github"
    except Exception as exc:
        print(json.dumps({"needs_peer_fallback": True, "error": type(exc).__name__ + ": " + str(exc)}))
        raise SystemExit(75)
if len(archive_data) != expected_size or hashlib.sha256(archive_data).hexdigest() != expected_hash:
    raise SystemExit("release asset size or SHA-256 verification failed")

version = str(manifest["latest_version"])
with tempfile.TemporaryDirectory(prefix="pcl-relay-bootstrap-") as temporary:
    root = pathlib.Path(temporary)
    archive = root / asset_name
    archive.write_bytes(archive_data)
    expanded = root / "expanded"
    expanded.mkdir()
    if system == "Darwin":
        unpacked = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(expanded)], capture_output=True, text=True)
        if unpacked.returncode != 0:
            raise SystemExit(unpacked.stderr or "could not extract macOS release")
        apps = list(expanded.glob("*.app"))
        if len(apps) != 1:
            raise SystemExit("macOS release must contain exactly one app")
        app = apps[0]
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            actual_version = str(plistlib.load(handle).get("CFBundleShortVersionString") or "")
        if actual_version != version:
            raise SystemExit("macOS app version mismatch")
        verified = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        if verified.returncode != 0:
            raise SystemExit(verified.stderr or "macOS app signature verification failed")
        destination = pathlib.Path("/Applications/PCL Relay.app")
        if not os.access(destination.parent, os.W_OK):
            destination = pathlib.Path.home() / "Applications" / "PCL Relay.app"
            destination.parent.mkdir(parents=True, exist_ok=True)
        stamp = str(time.time_ns())
        staging = destination.parent / ("." + destination.name + ".bootstrap-" + stamp)
        backup = destination.parent / ("." + destination.name + ".previous-" + stamp)
        shutil.copytree(app, staging, symlinks=True)
        if destination.exists(): destination.replace(backup)
        try:
            staging.replace(destination)
        except Exception:
            if backup.exists(): backup.replace(destination)
            raise
        cli = destination / "Contents" / "Resources" / "bridge" / "pcl-codex"
        installed_path = str(destination)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                target = (expanded / member.name).resolve()
                if expanded.resolve() != target and expanded.resolve() not in target.parents:
                    raise SystemExit("Linux release contains an unsafe path")
                if member.issym() or member.islnk():
                    raise SystemExit("Linux bootstrap does not accept archive links")
            bundle.extractall(expanded)
        roots = [item for item in expanded.iterdir() if item.is_dir()]
        if len(roots) != 1:
            raise SystemExit("Linux release must contain exactly one bundle directory")
        bundled_version = (roots[0] / "pcl_codex_bridge" / "VERSION").read_text(encoding="utf-8").strip()
        if bundled_version != version:
            raise SystemExit("Linux bundle version mismatch")
        installed = subprocess.run([str(roots[0] / "install.sh")], cwd=roots[0], capture_output=True, text=True)
        if installed.returncode != 0:
            raise SystemExit(installed.stderr or installed.stdout or "Linux staging failed")
        cli = pathlib.Path.home() / ".local" / "bin" / "pcl-codex"
        installed_path = str(cli)

staged = subprocess.run([str(cli), "sidecar", "stage"], capture_output=True, text=True)
if staged.returncode != 0:
    raise SystemExit(staged.stderr or staged.stdout or "PCL Relay sidecar staging failed")
service = subprocess.run([str(cli), "sync", "service", "install", "--host", "0.0.0.0", "--port", "15726"], capture_output=True, text=True)
if service.returncode != 0:
    raise SystemExit(service.stderr or service.stdout or "Relay sync receiver installation failed")
print(json.dumps({
    "installed": True,
    "version": version,
    "system": system,
    "architecture": machine,
    "installed_path": installed_path,
    "artifact_source": source,
    "sync_receiver": True,
    "codex_config_changed": False,
    "model_data_plane_restarted": False,
}))
'''


def _bootstrap_source(manifest: Dict[str, Any], peer_fallback: bool) -> str:
    return REMOTE_BOOTSTRAP.replace(
        "__PCL_MANIFEST__", repr(json.dumps(manifest, separators=(",", ":")))
    ).replace("__PCL_PEER_FALLBACK__", "True" if peer_fallback else "False")


def _asset_status(manifest: Dict[str, Any], asset_name: str) -> Dict[str, Any]:
    asset = manifest["assets"][asset_name]
    return {
        "latest_version": manifest["latest_version"],
        "asset_name": asset_name,
        "asset_url": asset["asset_url"],
        "asset_size": asset["asset_size"],
        "asset_digest": asset["asset_sha256"],
        "checksum_url": "",
    }


def _asset_for_probe(probe: Dict[str, Any]) -> str:
    system = str(probe.get("system") or "")
    machine = str(probe.get("architecture") or "").lower()
    if system == "Darwin":
        return "PCL-Relay-macOS.zip"
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        return "PCL-Relay-linux-x86_64.tar.gz"
    if system == "Linux" and machine in {"aarch64", "arm64"}:
        return "PCL-Relay-linux-aarch64.tar.gz"
    raise RuntimeError(f"Unsupported deployment target: {system or 'unknown'}/{machine or 'unknown'}")


def deploy_target(target_id: str, manifest: Optional[Dict[str, Any]] = None, timeout: int = 900) -> Dict[str, Any]:
    target = next((item for item in _load_targets() if item["id"] == target_id), None)
    if target is None:
        raise RuntimeError("Unknown deployment target")
    release = manifest or latest_release_manifest()
    if _version_tuple(str(release.get("latest_version") or "")) < _version_tuple(__version__):
        raise RuntimeError(
            f"Published release {release.get('latest_version') or 'unknown'} is older than this node {__version__}; publish matching macOS/Linux assets first"
        )
    probe = probe_target(target)
    if probe.get("receiver_online") is True:
        # Registered receivers use the normal signed release campaign. The
        # caller may subsequently push once to all peers; no SSH is needed.
        add_peer(target["control_url"], target["name"])
        return {
            **target,
            "ok": True,
            "action": "receiver-ready",
            "version": probe.get("relay_version") or probe.get("version") or "",
            "receiver_online": True,
        }
    if probe.get("ssh") is not True:
        raise RuntimeError(str(probe.get("error") or "SSH target is unreachable"))
    asset_name = _asset_for_probe(probe)
    source = _bootstrap_source(release, False)
    completed = _run_ssh(target["ssh_target"], source, timeout)
    github_error = ""
    if completed.returncode == 75:
        try:
            github_error = str(json.loads(completed.stdout.decode("utf-8")).get("error") or "")
        except Exception:
            github_error = completed.stderr.decode("utf-8", "replace").strip()
        cached = cache_release_asset(_asset_status(release, asset_name))
        completed = _run_ssh(
            target["ssh_target"],
            _bootstrap_source(release, True),
            timeout,
            Path(str(cached["path"])),
        )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or completed.stdout.decode("utf-8", "replace").strip() or "Remote installation failed")
    result = json.loads(completed.stdout.decode("utf-8"))
    last_error = ""
    for _ in range(12):
        try:
            add_peer(target["control_url"], target["name"])
            sync_once(timeout=20)
            return {**target, **result, "ok": True, "action": "bootstrapped", "github_error": github_error}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1)
    raise RuntimeError(f"Installation completed but the declared control endpoint did not become ready: {last_error}")


def deploy_all(timeout: int = 900) -> Dict[str, Any]:
    targets = _load_targets()
    if not targets:
        return {"targets": [], "count": 0, "ok_count": 0, "reason": "no_registered_targets"}
    manifest = latest_release_manifest()
    if _version_tuple(str(manifest.get("latest_version") or "")) < _version_tuple(__version__):
        raise RuntimeError(
            f"Published release {manifest.get('latest_version') or 'unknown'} is older than this node {__version__}; publish matching macOS/Linux assets first"
        )
    reports: List[Dict[str, Any]] = []
    # Registration writes the local Relay peer catalog, so commits are kept
    # sequential and deterministic even if several machines are selected.
    for item in targets:
        try:
            reports.append(deploy_target(item["id"], manifest, timeout))
        except Exception as exc:
            reports.append({**item, "ok": False, "action": "failed", "error": f"{type(exc).__name__}: {exc}"})
    campaign: Dict[str, Any] = {}
    if any(item.get("ok") is True for item in reports):
        try:
            from .topology_sync import push_latest_release

            campaign = push_latest_release(timeout)
        except Exception as exc:
            campaign = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "version": manifest["latest_version"],
        "targets": reports,
        "count": len(reports),
        "ok_count": sum(1 for item in reports if item.get("ok") is True),
        "update_campaign": campaign,
        "discovery": False,
        "credentials_synchronized": False,
        "network_managed": False,
    }
