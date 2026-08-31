from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

from .models import DEFAULT_GATEWAY_URL, load_registry
from .remote_clients import (
    _run_remote_python,
    effective_remote_gateway,
    install_remote_client,
    remote_client_status,
)


BRIDGE_REGISTRY = Path.home() / ".config" / "pcl-codex-bridge" / "bridges.json"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
STATE_DIR = Path.home() / ".local" / "state" / "pcl-codex-bridge"


def _load_bridges() -> Dict[str, Any]:
    try:
        value = json.loads(BRIDGE_REGISTRY.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_bridges(value: Dict[str, Any]) -> None:
    BRIDGE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    temp = BRIDGE_REGISTRY.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(BRIDGE_REGISTRY)


def _bridge_id(target: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-")[:32] or "node"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def _label(target: str) -> str:
    return f"cn.haichen.pcl-codex-bridge.{_bridge_id(target)}"


def _plist_path(target: str) -> Path:
    return LAUNCH_AGENTS / f"{_label(target)}.plist"


def _free_remote_port(target: str, start: int = 15723, end: int = 15739) -> int:
    source = f'''
import json, socket
for port in range({start}, {end + 1}):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        continue
    finally:
        sock.close()
    print(json.dumps({{"port": port}}))
    break
else:
    raise SystemExit("no free loopback bridge port")
'''
    result = _run_remote_python(target, source, timeout=20)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "Could not inspect remote ports")
    return int(json.loads(result.stdout.decode("utf-8"))["port"])


def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["launchctl", *arguments], capture_output=True, text=True, timeout=20, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "launchctl failed")
    return result


def _write_launch_agent(target: str, gateway_url: str, remote_port: int) -> Path:
    if os.uname().sysname != "Darwin":
        raise RuntimeError("Mac bridge installation is only available on macOS")
    parsed = urllib.parse.urlparse(gateway_url)
    if not parsed.hostname or not parsed.port:
        raise RuntimeError("Invalid relay address for Mac bridge")
    label = _label(target)
    path = _plist_path(target)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    arguments = [
        "/usr/bin/ssh",
        "-NT",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-R",
        f"127.0.0.1:{remote_port}:{parsed.hostname}:{parsed.port}",
        target,
    ]
    payload = {
        "Label": label,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "StandardOutPath": str(STATE_DIR / f"{_bridge_id(target)}.out.log"),
        "StandardErrorPath": str(STATE_DIR / f"{_bridge_id(target)}.err.log"),
    }
    path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
    os.chmod(path, 0o600)
    return path


def install_bridge(target: str, remote_port: int = 0) -> Dict[str, Any]:
    registry = load_registry()
    selected_gateway = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    relay_gateway = effective_remote_gateway(selected_gateway)
    bridges = _load_bridges()
    existing = bridges.get(target) if isinstance(bridges.get(target), dict) else {}
    if remote_port <= 0:
        existing_port = int(existing.get("remote_port") or 0)
        remote_port = existing_port if existing_port else _free_remote_port(target)
    loopback_gateway = f"http://127.0.0.1:{remote_port}/v1"

    domain = f"gui/{os.getuid()}"
    path = _plist_path(target)
    if path.exists():
        _launchctl("bootout", domain, str(path), check=False)
    path = _write_launch_agent(target, relay_gateway, remote_port)
    _launchctl("bootstrap", domain, str(path))
    _launchctl("kickstart", "-k", f"{domain}/{_label(target)}", check=False)

    deadline = time.monotonic() + 25
    status: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = remote_client_status(target, loopback_gateway)
        if status.get("ssh") and status.get("gateway_reachable"):
            break
        time.sleep(0.75)
    if not status.get("gateway_reachable"):
        _launchctl("bootout", domain, str(path), check=False)
        raise RuntimeError(f"Mac bridge did not become reachable: {status.get('error') or status}")

    client = install_remote_client(target, loopback_gateway)
    record = {
        "ssh_target": target,
        "relay_gateway": relay_gateway,
        "loopback_gateway": loopback_gateway,
        "remote_port": remote_port,
        "launch_agent": str(path),
        "label": _label(target),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "active": True,
    }
    bridges[target] = record
    _save_bridges(bridges)
    return {
        "bridge": record,
        "client": client,
        "scope": "mac_user_launch_agent_and_remote_user_codex_only",
        "existing_proxy_untouched": True,
    }


def bridge_status() -> Dict[str, Any]:
    bridges = _load_bridges()
    result: List[Dict[str, Any]] = []
    for target, raw in bridges.items():
        record = dict(raw) if isinstance(raw, dict) else {}
        gateway = str(record.get("loopback_gateway") or "")
        status = remote_client_status(target, gateway) if gateway else {"ready": False}
        record["status"] = status
        record["active"] = bool(status.get("ready"))
        result.append(record)
    return {"bridges": result, "count": len(result)}


def remove_bridge(target: str) -> Dict[str, Any]:
    bridges = _load_bridges()
    record = bridges.pop(target, None)
    path = _plist_path(target)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(path), check=False)
    if path.exists():
        path.unlink()
    _save_bridges(bridges)
    return {"removed": bool(record or path.exists()), "ssh_target": target}
