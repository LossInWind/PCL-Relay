from __future__ import annotations

import json
import struct
import urllib.parse
from typing import Any, Dict

from . import __version__
from .models import DEFAULT_GATEWAY_URL, load_registry
from .release_updater import CLIENT_ASSET_NAME, REPOSITORY
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
import hashlib, io, json, os, pathlib, shutil, struct, subprocess, sys, tarfile, time, urllib.request

header = sys.stdin.buffer.read(8)
if len(header) != 8:
    raise SystemExit("invalid direct-install payload")
key_length = struct.unpack("!Q", header)[0]
key = sys.stdin.buffer.read(key_length)
archive_data = sys.stdin.buffer.read()
if len(key) < 16:
    raise SystemExit("incomplete direct-install payload")

expected_version = os.environ["PCL_REMOTE_EXPECTED_VERSION"]
force_mac_fallback = os.environ.get("PCL_REMOTE_FORCE_MAC_FALLBACK") == "1"
update_source = "current_mac_fallback" if force_mac_fallback else "github_release"
github_error = ""
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers = {"User-Agent": "PCL-Relay-Remote-Updater/" + expected_version}

def download(url, limit):
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("GitHub Release client asset exceeds the safety limit")
    return data

if force_mac_fallback:
    if not archive_data:
        raise SystemExit("current Mac fallback archive is missing")
else:
    try:
        checksum = download(os.environ["PCL_REMOTE_RELEASE_CHECKSUM"], 4096).decode("utf-8", "replace").strip().split()[0].lower()
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise RuntimeError("GitHub Release client checksum is invalid")
        github_archive = download(os.environ["PCL_REMOTE_RELEASE_ARCHIVE"], 64 * 1024 * 1024)
        if hashlib.sha256(github_archive).hexdigest() != checksum:
            raise RuntimeError("GitHub Release client checksum verification failed")
        archive_data = github_archive
    except Exception as exc:
        raise SystemExit(f"PCL_GITHUB_UNAVAILABLE: {type(exc).__name__}: {exc}")

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
    for member in archive.getmembers():
        destination = (package_root / member.name).resolve()
        if os.path.commonpath((str(package_root.resolve()), str(destination))) != str(package_root.resolve()):
            raise SystemExit("client archive contains an unsafe path")
        if member.issym() or member.islnk():
            raise SystemExit("client archive contains an unsupported link")
    archive.extractall(package_root)
packaged_version = (package_root / "pcl_codex_bridge" / "VERSION").read_text(encoding="utf-8").strip()
if packaged_version != expected_version:
    raise SystemExit(
        "client version mismatch: expected " + expected_version + ", got " + packaged_version
    )
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
    "client_version": packaged_version,
    "update_source": update_source,
    "github_error": github_error if update_source == "current_mac_fallback" else "",
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
    key_payload = struct.pack("!Q", len(key)) + key

    def install_source(force_mac_fallback: bool) -> str:
        return (
            "import os\n"
            + "os.environ['PCL_RELAY_COORDINATOR_URL'] = " + repr(selected_gateway.rstrip("/")) + "\n"
            + "os.environ['PCL_RELAY_NODE_ID'] = " + repr(node_id) + "\n"
            + "os.environ['PCL_RELAY_NODE_NAME'] = " + repr(node_name) + "\n"
            + "os.environ['PCL_REMOTE_EXPECTED_VERSION'] = " + repr(__version__) + "\n"
            + "os.environ['PCL_REMOTE_RELEASE_ARCHIVE'] = "
            + repr(
                f"https://github.com/{REPOSITORY}/releases/download/v{__version__}/{CLIENT_ASSET_NAME}"
            )
            + "\n"
            + "os.environ['PCL_REMOTE_RELEASE_CHECKSUM'] = "
            + repr(
                f"https://github.com/{REPOSITORY}/releases/download/v{__version__}/{CLIENT_ASSET_NAME}.sha256"
            )
            + "\n"
            + "os.environ['PCL_REMOTE_FORCE_MAC_FALLBACK'] = " + repr("1" if force_mac_fallback else "0") + "\n"
            + REMOTE_DIRECT_INSTALL
        )

    result = _run_remote_python(
        target,
        install_source(False),
        stdin=key_payload,
        timeout=120,
    )
    github_error = ""
    payload = b""
    if result.returncode != 0:
        github_error = result.stderr.decode("utf-8", "replace").strip()
        if "PCL_GITHUB_UNAVAILABLE:" not in github_error:
            raise RuntimeError(github_error or "Local direct installation failed")
        archive = _source_archive()
        payload = key_payload + archive
        result = _run_remote_python(
            target,
            install_source(True),
            stdin=payload,
            timeout=120,
        )
    # Drop the only local reference as soon as the transfer completes.
    key = b""
    key_payload = b""
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
        "update_source": installed.get("update_source", "current_mac_fallback"),
        "github_error": github_error if installed.get("update_source") == "current_mac_fallback" else "",
    }
