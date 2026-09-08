from __future__ import annotations

import os
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from .topology_sync import DEFAULT_SYNC_PORT, _token_optional_listen, heartbeat


SYNC_LABEL = "cn.haichen.pcl-relay-sync"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{SYNC_LABEL}.plist"
SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "pcl-relay-sync.service"
STATE_ROOT = Path.home() / ".local" / "state" / "pcl-codex-bridge"
SERVICE_CONFIG = Path.home() / ".config" / "pcl-codex-bridge" / "sync-service.json"


def _launcher() -> List[str]:
    installed = Path.home() / ".local" / "bin" / "pcl-codex"
    if installed.is_file() and os.access(installed, os.X_OK):
        return [str(installed)]
    return [sys.executable, "-m", "pcl_codex_bridge.cli"]


def _arguments(host: str, port: int, token_file: str, interval: int) -> List[str]:
    arguments = [*_launcher(), "sync", "serve", "--host", host, "--port", str(int(port)), "--interval", str(int(interval))]
    if token_file:
        arguments += ["--token-file", str(Path(token_file).expanduser())]
    return arguments


def install_sync_service(host: str = "0.0.0.0", port: int = DEFAULT_SYNC_PORT, token_file: str = "", interval: int = 15) -> Dict[str, Any]:
    if not _token_optional_listen(host) and not token_file:
        raise RuntimeError("A token file is required outside loopback or the Tailscale address space")
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    arguments = _arguments(host, port, token_file, interval)
    manager = ""
    if sys.platform == "darwin":
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": SYNC_LABEL,
            "ProgramArguments": arguments,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "StandardOutPath": str(STATE_ROOT / "sync.log"),
            "StandardErrorPath": str(STATE_ROOT / "sync.log"),
            "ProcessType": "Background",
        }
        temporary = LAUNCH_AGENT.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            plistlib.dump(payload, handle)
        os.chmod(temporary, 0o600)
        temporary.replace(LAUNCH_AGENT)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(LAUNCH_AGENT)], capture_output=True, text=True, timeout=15, check=False)
        result = subprocess.run(["launchctl", "bootstrap", domain, str(LAUNCH_AGENT)], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "launchctl could not start Relay sync")
        manager = "launchd"
    elif sys.platform.startswith("linux"):
        SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(_systemd_quote(value) for value in arguments)
        unit = "\n".join([
            "[Unit]",
            "Description=PCL Relay topology heartbeat and synchronization",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={command}",
            "Restart=on-failure",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ])
        temporary = SYSTEMD_UNIT.with_suffix(".tmp")
        temporary.write_text(unit, encoding="utf-8")
        temporary.replace(SYSTEMD_UNIT)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True, timeout=15, check=False)
        result = subprocess.run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT.name], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "systemd could not start Relay sync")
        manager = "systemd-user"
    else:
        raise RuntimeError("Relay sync background service supports macOS and Linux")
    SERVICE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary_config = SERVICE_CONFIG.with_suffix(".tmp")
    temporary_config.write_text(
        json.dumps({"host": host, "port": int(port), "token_file": str(Path(token_file).expanduser()) if token_file else "", "interval": int(interval)}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary_config, 0o600)
    temporary_config.replace(SERVICE_CONFIG)
    return {"installed": True, "manager": manager, "host": host, "port": int(port), "interval_seconds": int(interval), "model_data_plane_restarted": False}


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sync_service_status() -> Dict[str, Any]:
    installed = LAUNCH_AGENT.exists() if sys.platform == "darwin" else SYSTEMD_UNIT.exists()
    try:
        config = json.loads(SERVICE_CONFIG.read_text(encoding="utf-8"))
        config = config if isinstance(config, dict) else {}
    except (OSError, ValueError, TypeError):
        config = {}
    host = str(config.get("host") or "0.0.0.0")
    port = int(config.get("port") or DEFAULT_SYNC_PORT)
    token_file = str(config.get("token_file") or "")
    active = False
    error = ""
    if installed:
        try:
            probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", "localhost"} else host
            heartbeat(f"http://{probe_host}:{port}", token_file=token_file, timeout=2)
            active = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {"installed": installed, "active": active, "host": host, "port": port, "error": error, "model_data_plane_restarted": False}


def uninstall_sync_service() -> Dict[str, Any]:
    removed: List[str] = []
    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(LAUNCH_AGENT)], capture_output=True, text=True, timeout=15, check=False)
        if LAUNCH_AGENT.exists():
            LAUNCH_AGENT.unlink()
            removed.append(str(LAUNCH_AGENT))
    elif sys.platform.startswith("linux"):
        subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT.name], capture_output=True, text=True, timeout=30, check=False)
        if SYSTEMD_UNIT.exists():
            SYSTEMD_UNIT.unlink()
            removed.append(str(SYSTEMD_UNIT))
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True, timeout=15, check=False)
    if SERVICE_CONFIG.exists():
        SERVICE_CONFIG.unlink()
        removed.append(str(SERVICE_CONFIG))
    return {"installed": False, "active": False, "removed": removed, "model_data_plane_restarted": False}
