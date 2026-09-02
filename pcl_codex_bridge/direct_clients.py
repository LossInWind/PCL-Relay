from __future__ import annotations

import json
import struct
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict

from .models import DEFAULT_GATEWAY_URL, load_registry
from .remote_clients import (
    _node_ssh_target,
    _run_remote_python,
    _source_archive,
    discover_relays,
    remote_client_status,
    ssh_inventory,
)


def _selected_relay_ssh_target(gateway_url: str) -> str:
    host = urllib.parse.urlparse(gateway_url).hostname
    report = discover_relays(timeout=2.0)
    node = next(
        (
            item
            for item in report.get("nodes", [])
            if item.get("gateway") and host in {item.get("magic_dns"), item.get("tailscale_ip")}
        ),
        None,
    )
    if not node:
        raise RuntimeError("Could not identify the selected relay node")
    target = _node_ssh_target(node, ssh_inventory())
    if not target:
        raise RuntimeError("The selected relay has no matching SSH host alias")
    return target


def _read_relay_key(relay_target: str) -> bytes:
    source = r'''
import pathlib, sys
path = pathlib.Path.home() / ".config" / "pcl-codex-bridge" / "api-key"
data = path.read_bytes().strip()
if not data:
    raise SystemExit("relay API key file is empty")
sys.stdout.buffer.write(data)
'''
    result = _run_remote_python(relay_target, source, timeout=20)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "Could not read relay API key")
    key = result.stdout.strip()
    if len(key) < 16:
        raise RuntimeError("Relay API key failed validation")
    return key


REMOTE_DIRECT_INSTALL = r'''
import io, json, os, pathlib, shutil, struct, subprocess, sys, tarfile, time, urllib.request

header = sys.stdin.buffer.read(8)
if len(header) != 8:
    raise SystemExit("invalid direct-install payload")
key_length = struct.unpack("!Q", header)[0]
key = sys.stdin.buffer.read(key_length)
archive_data = sys.stdin.buffer.read()
if len(key) < 16 or not archive_data:
    raise SystemExit("incomplete direct-install payload")

persistent_parent = pathlib.Path("/home/zhc")
if not persistent_parent.is_dir() or not os.access(persistent_parent, os.W_OK):
    persistent_parent = pathlib.Path.home() / ".local" / "share"
root = persistent_parent / ".pcl-codex-direct"
package_root = root / "app"
config_root = root / "config"
state_root = root / "state"
for directory in (package_root, config_root, state_root):
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
    archive.extractall(package_root)
key_path = config_root / "api-key"
key_path.write_bytes(key + b"\n")
os.chmod(key_path, 0o600)

session = "pcl-codex-local"
subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
service_env = os.environ.copy()
for name in list(service_env):
    if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
        service_env.pop(name, None)
service_env.update({
    "PYTHONPATH": str(package_root),
    "PCL_CODEX_GATEWAY_HOST": "127.0.0.1",
    "PCL_CODEX_GATEWAY_PORT": "15722",
    "PCL_LLM_API_KEY_FILE": str(key_path),
    "PCL_CODEX_GATEWAY_LOG": str(state_root / "gateway.log"),
})
command = [
    "tmux", "new-session", "-d", "-s", session,
    sys.executable, "-m", "pcl_codex_bridge.gateway",
]
started = subprocess.run(command, capture_output=True, text=True, env=service_env)
if started.returncode != 0:
    raise SystemExit(started.stderr or "could not start local adapter")

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
models = []
last_error = ""
for _ in range(30):
    try:
        with opener.open("http://127.0.0.1:15722/v1/models", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("data", [])
        if isinstance(models, list):
            break
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
else:
    raise SystemExit("local adapter validation failed: " + last_error)

client_env = os.environ.copy()
client_env["PYTHONPATH"] = str(package_root)
installed = subprocess.run(
    [sys.executable, "-m", "pcl_codex_bridge.cli", "--gateway-url", "http://127.0.0.1:15722/v1", "install", "client"],
    capture_output=True,
    text=True,
    env=client_env,
)
if installed.returncode != 0:
    raise SystemExit(installed.stderr or installed.stdout or "Codex client install failed")
print(json.dumps({
    "mode": "local_pcl_direct",
    "gateway": "http://127.0.0.1:15722/v1",
    "model_count": len(models),
    "tmux_session": session,
    "persistent_root": str(root),
    "key_mode": oct(key_path.stat().st_mode & 0o777),
    "system": __import__("platform").system(),
}))
'''


def install_local_direct(target: str) -> Dict[str, Any]:
    registry = load_registry()
    selected_gateway = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    relay_report = discover_relays(timeout=2.0)
    inventory = ssh_inventory()
    target_node = next(
        (
            item
            for item in relay_report.get("nodes", [])
            if _node_ssh_target(item, inventory) == target
        ),
        {},
    )
    node_id = str(target_node.get("tailscale_ip") or "")
    node_name = str(target_node.get("node_name") or target)
    relay_target = _selected_relay_ssh_target(selected_gateway)
    key = _read_relay_key(relay_target)
    archive = _source_archive()
    payload = struct.pack("!Q", len(key)) + key + archive
    source = (
        "import os\n"
        + "os.environ['PCL_RELAY_COORDINATOR_URL'] = " + repr(selected_gateway.rstrip("/")) + "\n"
        + "os.environ['PCL_RELAY_NODE_ID'] = " + repr(node_id) + "\n"
        + "os.environ['PCL_RELAY_NODE_NAME'] = " + repr(node_name) + "\n"
        + REMOTE_DIRECT_INSTALL
    )
    result = _run_remote_python(target, source, stdin=payload, timeout=120)
    # Drop the only local reference as soon as the transfer completes.
    key = b""
    payload = b""
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "Local direct installation failed")
    installed = json.loads(result.stdout.decode("utf-8"))
    status = remote_client_status(target, "http://127.0.0.1:15722/v1")
    if not status.get("ready"):
        raise RuntimeError(f"Local direct adapter started but Codex verification failed: {status}")
    return {
        "ssh_target": target,
        "source_relay": relay_target,
        "installed": installed,
        "status": status,
        "pcl_key_location": "remote_only_mode_0600",
        "mac_disk_key_copy": False,
        "vscode_reload_required": True,
    }
