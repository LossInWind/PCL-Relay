from __future__ import annotations

import base64
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__
from .client_config import discover_relays, request_json
from .models import DEFAULT_GATEWAY_URL, load_registry


SSH_OPTIONS = [
    "-o",
    "ClearAllForwardings=yes",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=6",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=1",
]
SAFE_TARGET = re.compile(r"^[A-Za-z0-9_.@:-]+$")


def _literal_ssh_aliases(config: Optional[Path] = None) -> List[str]:
    path = config or Path.home() / ".ssh" / "config"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    aliases: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if not any(char in alias for char in "*?!") and SAFE_TARGET.fullmatch(alias):
                aliases.append(alias)
    return list(dict.fromkeys(aliases))


def _ssh_effective(alias: str) -> Optional[Dict[str, Any]]:
    result = subprocess.run(
        ["ssh", "-G", alias], capture_output=True, text=True, timeout=8, check=False
    )
    if result.returncode != 0:
        return None
    values: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key in {"hostname", "user", "port"} and value:
            values[key] = value.strip()
    if not values.get("hostname"):
        return None
    return {
        "target": alias,
        "hostname": values["hostname"].rstrip("."),
        "user": values.get("user", ""),
        "port": int(values.get("port", "22")),
    }


def ssh_inventory() -> List[Dict[str, Any]]:
    records = []
    for alias in _literal_ssh_aliases():
        try:
            effective = _ssh_effective(alias)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            effective = None
        if effective:
            records.append(effective)
    return records


def _node_ssh_target(node: Dict[str, Any], inventory: Iterable[Dict[str, Any]]) -> str:
    names = {
        str(node.get("node_name") or "").lower().rstrip("."),
        str(node.get("magic_dns") or "").lower().rstrip("."),
        str(node.get("tailscale_ip") or "").lower(),
    }
    short_names = {value.split(".", 1)[0] for value in names if value}
    for record in inventory:
        values = {
            str(record.get("target") or "").lower().rstrip("."),
            str(record.get("hostname") or "").lower().rstrip("."),
        }
        if names.intersection(values) or short_names.intersection(
            value.split(".", 1)[0] for value in values if value
        ):
            return str(record["target"])
    return ""


def _run_remote_python(target: str, source: str, stdin: bytes = b"", timeout: int = 30) -> subprocess.CompletedProcess:
    if not SAFE_TARGET.fullmatch(target):
        raise RuntimeError("Unsafe SSH target; use a literal host alias or user@host")
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    launcher = (
        "import base64;"
        f"exec(compile(base64.b64decode({encoded!r}),'<pcl-codex-remote>','exec'))"
    )
    return subprocess.run(
        ["ssh", *SSH_OPTIONS, target, "python3 -c " + shlex.quote(launcher)],
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


REMOTE_STATUS_SOURCE = r'''
import json, os, pathlib, platform, shutil, subprocess, sys, time, urllib.error, urllib.request

home = pathlib.Path.home()
config = home / ".codex" / "config.toml"
registry_path = home / ".config" / "pcl-codex-bridge" / "models.json"
version_path = home / ".local" / "share" / "pcl-codex-bridge" / "VERSION"
text = config.read_text(encoding="utf-8", errors="replace") if config.exists() else ""
try:
    client_version = version_path.read_text(encoding="utf-8").strip()
except Exception:
    client_version = "legacy" if (home / ".local" / "bin" / "pcl-codex").exists() else ""
expected_client_version = os.environ.get("PCL_REMOTE_EXPECTED_VERSION") or ""
agents_dir = home / ".codex" / "agents"
managed_roles = []
if agents_dir.exists():
    for role_path in agents_dir.glob("*.toml"):
        try:
            if role_path.read_text(encoding="utf-8", errors="replace").startswith("# >>> pcl-relay managed native agent role v2 >>>\n"):
                managed_roles.append(role_path.name)
        except Exception:
            pass
try:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
except Exception:
    registry = {}
configured_gateway = registry.get("gateway") or ""
native_router_port = int(registry.get("native_router_port") or 15724)
native_router_reachable = False
native_router_gateway_reachable = False
try:
    native_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with native_opener.open(f"http://127.0.0.1:{native_router_port}/healthz", timeout=5) as response:
        native_payload = json.loads(response.read().decode("utf-8"))
    native_router_reachable = native_payload.get("service") == "pcl-relay-native-router"
    native_router_gateway_reachable = bool(native_payload.get("gateway_reachable"))
except Exception:
    pass
gateway = os.environ.get("PCL_REMOTE_GATEWAY") or configured_gateway
reachable = False
error = ""
gateway_latency_ms = None
gateway_model_count = None
gateway_models_reachable = False
deep = os.environ.get("PCL_REMOTE_DEEP") == "1"
if gateway:
    started = time.monotonic()
    try:
        url = gateway.rstrip("/").rsplit("/v1", 1)[0] + "/healthz"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        reachable = payload.get("status") == "ok"
        if deep and reachable:
            with opener.open(gateway.rstrip("/") + "/models", timeout=12) as response:
                models_payload = json.loads(response.read().decode("utf-8"))
            models = models_payload.get("data") if isinstance(models_payload, dict) else None
            gateway_model_count = len(models) if isinstance(models, list) else 0
            gateway_models_reachable = isinstance(models, list)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    gateway_latency_ms = int((time.monotonic() - started) * 1000)
configured_gateway_reachable = reachable if configured_gateway == gateway else False
configured_gateway_latency_ms = gateway_latency_ms if configured_gateway == gateway else None
configured_gateway_model_count = gateway_model_count if configured_gateway == gateway else None
configured_gateway_models_reachable = gateway_models_reachable if configured_gateway == gateway else False
if configured_gateway and configured_gateway != gateway:
    configured_started = time.monotonic()
    try:
        configured_url = configured_gateway.rstrip("/").rsplit("/v1", 1)[0] + "/healthz"
        with opener.open(configured_url, timeout=8) as response:
            configured_payload = json.loads(response.read().decode("utf-8"))
        configured_gateway_reachable = configured_payload.get("status") == "ok"
        if deep and configured_gateway_reachable:
            with opener.open(configured_gateway.rstrip("/") + "/models", timeout=12) as response:
                configured_models_payload = json.loads(response.read().decode("utf-8"))
            configured_models = configured_models_payload.get("data") if isinstance(configured_models_payload, dict) else None
            configured_gateway_model_count = len(configured_models) if isinstance(configured_models, list) else 0
            configured_gateway_models_reachable = isinstance(configured_models, list)
    except Exception as exc:
        if not error:
            error = f"{type(exc).__name__}: {exc}"
    configured_gateway_latency_ms = int((time.monotonic() - configured_started) * 1000)
tailscale_path = shutil.which("tailscale")
workspace_tailscale_ip = ""
if tailscale_path:
    try:
        probe = subprocess.run([tailscale_path, "ip", "-4"], capture_output=True, text=True, timeout=5)
        if probe.returncode == 0:
            workspace_tailscale_ip = next((line.strip() for line in probe.stdout.splitlines() if line.strip()), "")
    except Exception:
        pass
pcl_network_reachable = False
try:
    request = urllib.request.Request("https://llmapi.pcl.ac.cn/v1/models")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=8):
        pcl_network_reachable = True
except urllib.error.HTTPError as exc:
    pcl_network_reachable = exc.code in {401, 403}
except Exception:
    pass
print(json.dumps({
    "home": str(home),
    "system": platform.system(),
    "architecture": platform.machine(),
    "python_version": ".".join(map(str, sys.version_info[:3])),
    "supported_system": platform.system() in {"Darwin", "Linux"} and sys.version_info >= (3, 9),
    "workspace_tailscale": bool(workspace_tailscale_ip),
    "workspace_tailscale_ip": workspace_tailscale_ip,
    "pcl_network_reachable": pcl_network_reachable,
    "relay_capable": bool(workspace_tailscale_ip) and pcl_network_reachable and platform.system() == "Linux",
    "config_managed": all(marker in text for marker in (
        "# >>> pcl-codex-bridge managed block >>>",
        "# <<< pcl-codex-bridge managed block <<<",
        "# >>> pcl-relay native router root >>>",
        "# <<< pcl-relay native router root <<<",
    )),
    "native_v1": False,
    "native_v2": all(marker in text for marker in ('[features.multi_agent_v2]', 'hide_spawn_agent_metadata = true', 'tool_namespace = "agents"')) and ('"multi_agent_version": "v2"' in (home / ".codex" / "pcl-native-models.json").read_text(encoding="utf-8", errors="replace") if (home / ".codex" / "pcl-native-models.json").exists() else False),
    "native_roles": bool(managed_roles),
    "native_role_names": sorted(managed_roles),
    "native_router_port": native_router_port,
    "native_router_reachable": native_router_reachable,
    "native_router_gateway_reachable": native_router_gateway_reachable,
    "client_installed": (home / ".local" / "bin" / "pcl-codex").exists(),
    "client_version": client_version,
    "expected_client_version": expected_client_version,
    "update_available": bool(expected_client_version and client_version != expected_client_version),
    "gateway": gateway,
    "gateway_reachable": reachable,
    "gateway_latency_ms": gateway_latency_ms,
    "gateway_model_count": gateway_model_count,
    "gateway_models_reachable": gateway_models_reachable,
    "configured_gateway": configured_gateway,
    "configured_gateway_reachable": configured_gateway_reachable,
    "configured_gateway_latency_ms": configured_gateway_latency_ms,
    "configured_gateway_model_count": configured_gateway_model_count,
    "configured_gateway_models_reachable": configured_gateway_models_reachable,
    "error": error,
}))
'''


def remote_client_status(target: str, gateway_url: str, deep: bool = False) -> Dict[str, Any]:
    source = (
        "import os\n"
        + "os.environ['PCL_REMOTE_GATEWAY'] = " + repr(gateway_url) + "\n"
        + "os.environ['PCL_REMOTE_DEEP'] = " + repr("1" if deep else "0") + "\n"
        + "os.environ['PCL_REMOTE_EXPECTED_VERSION'] = " + repr(__version__) + "\n"
        + REMOTE_STATUS_SOURCE
    )
    try:
        result = _run_remote_python(target, source, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ssh": False, "ssh_target": target, "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        return {"ssh": False, "ssh_target": target, "error": detail or "ssh_failed"}
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {"ssh": False, "ssh_target": target, "error": f"invalid_status: {exc}"}
    payload["ssh"] = True
    payload["ssh_target"] = target
    payload["ready"] = bool(
        payload.get("supported_system")
        and payload.get("client_installed")
        and payload.get("config_managed")
        and payload.get("native_v2")
        and payload.get("native_roles")
        and payload.get("native_router_reachable")
        and payload.get("native_router_gateway_reachable")
        and payload.get("gateway_reachable")
        and str(payload.get("gateway") or "").rstrip("/") == gateway_url.rstrip("/")
        and not payload.get("update_available")
    )
    return payload


def _tailnet_node_snapshot(node_id: str = "", target: str = "") -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=8, check=False
        )
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {"found": False, "online": False, "error": f"{type(exc).__name__}: {exc}"}

    wanted = {value.lower().rstrip(".") for value in (node_id, target) if value}
    if target and target != "local":
        try:
            effective = _ssh_effective(target) or {}
            wanted.add(str(effective.get("hostname") or "").lower().rstrip("."))
        except Exception:
            pass

    entries = [payload.get("Self") or {}] + list((payload.get("Peer") or {}).values())
    for entry in entries:
        names = {
            str(entry.get("HostName") or "").lower().rstrip("."),
            str(entry.get("DNSName") or "").lower().rstrip("."),
            *(str(value).lower() for value in (entry.get("TailscaleIPs") or [])),
        }
        if target == "local" and entry is entries[0] or wanted.intersection(names):
            return {
                "found": True,
                "node_name": entry.get("HostName") or "",
                "online": bool(entry.get("Online", target == "local")),
                "last_seen": entry.get("LastSeen") or "",
                "relay": entry.get("Relay") or "",
                "tailscale_ips": entry.get("TailscaleIPs") or [],
            }
    return {"found": False, "online": False, "last_seen": "", "relay": "", "tailscale_ips": []}


def check_client_connectivity(
    target: str,
    gateway_url: str,
    node_id: str = "",
    deep: bool = True,
) -> Dict[str, Any]:
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tailnet = _tailnet_node_snapshot(node_id, target)
    checks: List[Dict[str, Any]] = [
        {
            "name": "Tailnet",
            "passed": bool(tailnet.get("online")),
            "detail": "设备在线" if tailnet.get("online") else "设备离线",
        }
    ]

    if target == "local":
        started = time.monotonic()
        gateway_ok = False
        catalog_ok = False
        model_count = 0
        error = ""
        try:
            health = request_json(gateway_url.rstrip("/").rsplit("/v1", 1)[0] + "/healthz", timeout=8)
            gateway_ok = isinstance(health, dict) and health.get("status") == "ok"
            if deep and gateway_ok:
                models = request_json(gateway_url.rstrip("/") + "/models", timeout=12)
                entries = models.get("data") if isinstance(models, dict) else None
                catalog_ok = isinstance(entries, list)
                model_count = len(entries) if isinstance(entries, list) else 0
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency = int((time.monotonic() - started) * 1000)
        checks.extend(
            [
                {"name": "本机", "passed": True, "detail": "当前 Mac"},
                {"name": "中转站", "passed": gateway_ok, "detail": f"{latency} ms" if gateway_ok else error or "不可达"},
                {"name": "模型目录", "passed": catalog_ok if deep else gateway_ok, "detail": f"{model_count} 个模型" if catalog_ok else ("快速检查未请求目录" if not deep else "不可用")},
            ]
        )
        status = "ready" if gateway_ok and (catalog_ok or not deep) else "degraded"
        return {
            "target": target,
            "node_id": node_id,
            "checked_at": checked_at,
            "status": status,
            "summary": "当前 Mac 可以使用所选中转站" if status == "ready" else "当前 Mac 无法完整访问所选中转站",
            "route": "direct",
            "tailnet_online": bool(tailnet.get("online")),
            "tailnet_last_seen": tailnet.get("last_seen") or "",
            "ssh": True,
            "gateway_reachable": gateway_ok,
            "catalog_reachable": catalog_ok if deep else None,
            "model_count": model_count,
            "latency_ms": latency,
            "error": error,
            "checks": checks,
        }

    remote = remote_client_status(target, gateway_url, deep=deep)
    ssh_ok = bool(remote.get("ssh"))
    ssh_detail = "登录成功" if ssh_ok else (
        "设备离线，未建立连接" if not tailnet.get("online") else "连接失败"
    )
    checks.append({"name": "SSH", "passed": ssh_ok, "detail": ssh_detail})
    configured = str(remote.get("configured_gateway") or "")
    use_configured = bool(configured and remote.get("configured_gateway_reachable"))
    gateway_ok = bool(remote.get("configured_gateway_reachable") if use_configured else remote.get("gateway_reachable"))
    catalog_ok = bool(remote.get("configured_gateway_models_reachable") if use_configured else remote.get("gateway_models_reachable"))
    model_count = int((remote.get("configured_gateway_model_count") if use_configured else remote.get("gateway_model_count")) or 0)
    latency = remote.get("configured_gateway_latency_ms") if use_configured else remote.get("gateway_latency_ms")
    route = "local_pcl_direct" if configured.startswith("http://127.0.0.1:") and use_configured else "direct"
    checks.extend(
        [
            {"name": "接入路径", "passed": gateway_ok, "detail": (f"{latency} ms" if gateway_ok and latency is not None else ("等待设备上线" if not tailnet.get("online") else "不可达"))},
            {"name": "模型目录", "passed": catalog_ok if deep else gateway_ok, "detail": (f"{model_count} 个模型" if catalog_ok else ("快速检查未请求目录" if not deep else "不可用"))},
        ]
    )
    if not tailnet.get("online"):
        status = "offline"
        summary = "Tailnet 设备离线；需要先让该服务器上的 Tailscale 恢复在线"
    elif not ssh_ok:
        status = "unreachable"
        summary = "设备在线，但当前 SSH 凭据或 SSH 路径不可用"
    elif not remote.get("supported_system", True):
        status = "unsupported"
        summary = "远端系统不受支持"
    elif gateway_ok and (catalog_ok or not deep):
        status = "ready"
        summary = "本机 PCL 直连可用" if route == "local_pcl_direct" else "所选中转站连接可用"
    else:
        status = "degraded"
        summary = "SSH 正常，但 PCL 接入路径未通过完整检查"
    return {
        "target": target,
        "node_id": node_id,
        "checked_at": checked_at,
        "status": status,
        "summary": summary,
        "route": route,
        "tailnet_online": bool(tailnet.get("online")),
        "tailnet_last_seen": tailnet.get("last_seen") or "",
        "ssh": ssh_ok,
        "gateway_reachable": gateway_ok,
        "catalog_reachable": catalog_ok if deep else None,
        "model_count": model_count,
        "latency_ms": latency,
        "error": str(remote.get("error") or ""),
        "checks": checks,
    }


def effective_remote_gateway(gateway_url: str, relay_report: Optional[Dict[str, Any]] = None) -> str:
    report = relay_report or discover_relays(timeout=2.0)
    selected_host = urllib.parse.urlparse(gateway_url).hostname
    selected_relay = next(
        (
            node
            for node in report.get("nodes", [])
            if node.get("gateway")
            and selected_host in {node.get("magic_dns"), node.get("tailscale_ip")}
        ),
        None,
    )
    if not selected_relay:
        return gateway_url
    parsed = urllib.parse.urlparse(gateway_url)
    return f"http://{selected_relay['tailscale_ip']}:{parsed.port or 15722}/v1"


def _is_relay_candidate(node: Dict[str, Any]) -> bool:
    status = node.get("client_status")
    return bool(
        node.get("gateway")
        and node.get("pcl_auth") == "valid"
        and isinstance(status, dict)
        and status.get("relay_capable")
    )


def discover_remote_clients(timeout: float = 2.0) -> Dict[str, Any]:
    registry = load_registry()
    gateway_url = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    relay_report = discover_relays(timeout=timeout)
    remote_gateway = effective_remote_gateway(gateway_url, relay_report)
    inventory = ssh_inventory()
    nodes = []
    for node in relay_report.get("nodes", []):
        record = dict(node)
        target = _node_ssh_target(record, inventory)
        record["ssh_target"] = target
        record["client_status"] = (
            remote_client_status(target, remote_gateway)
            if target and record.get("online") and not record.get("self")
            else {"ssh": False, "ready": False, "error": "ssh_target_not_configured"}
        )
        nodes.append(record)
    ready_relays = sorted(
        (
            node
            for node in nodes
            if _is_relay_candidate(node)
        ),
        key=lambda node: (int(node.get("latency_ms") or 999999), -int(node.get("model_count") or 0)),
    )
    recommended_relay = ready_relays[0] if ready_relays else None
    local_node = next((node for node in nodes if node.get("self")), None)
    local_bridge_available = bool(
        local_node and os.uname().sysname == "Darwin" and recommended_relay
    )
    edges: List[Dict[str, Any]] = []
    relay_ip = str(recommended_relay.get("tailscale_ip")) if recommended_relay else ""
    local_ip = str(local_node.get("tailscale_ip")) if local_node else ""
    if relay_ip:
        edges.append({"from": "pcl-api", "to": relay_ip, "type": "upstream", "verified": True})

    for record in nodes:
        status = record.get("client_status") if isinstance(record.get("client_status"), dict) else {}
        direct = bool(record.get("self") and recommended_relay) or bool(status.get("gateway_reachable"))
        configured_gateway = str(status.get("configured_gateway") or "")
        local_direct_active = bool(
            configured_gateway.startswith("http://127.0.0.1:")
            and status.get("configured_gateway_reachable")
            and status.get("config_managed")
            and status.get("client_installed")
        )
        pcl_direct = bool(status.get("pcl_network_reachable")) and not record.get("self")
        bridge = bool(
            local_bridge_available
            and not record.get("self")
            and record.get("online")
            and status.get("ssh")
            and status.get("supported_system") is not False
            and not direct
        )
        # A loopback adapter can be visible through a host/container port mapping
        # without the managed workspace itself owning a Tailnet address.  That is
        # useful for local PCL access, but it must not make the Pod a recommended
        # shared relay.  Relay capability therefore comes from the workspace probe.
        relay_capable = bool(status.get("relay_capable"))
        if direct:
            route = "direct"
            reason = "工作区可直接访问所选中转站，跳数最少"
            score = 100
        elif pcl_direct:
            route = "local_pcl_direct"
            reason = "工作区可直达 PCL API；使用仅监听回环地址的本地适配器比跨设备桥接更稳定"
            score = 90
        elif bridge:
            route = "bridge_via_local_mac"
            reason = "工作区不在 Tailnet；可通过当前 Mac 的 SSH 回环桥接"
            score = 65
        else:
            route = "unavailable"
            reason = "设备离线、SSH 不可用或当前没有可达路径"
            score = 0
        record["feasibility"] = {
            "relay_capable": relay_capable,
            "relay_installed": bool(record.get("gateway")),
            "workspace_tailscale": bool(status.get("workspace_tailscale")) if not record.get("self") else True,
            "pcl_network_reachable": bool(status.get("pcl_network_reachable")) if not record.get("self") else False,
            "direct": direct,
            "bridge_via_local_mac": bridge,
            "local_pcl_direct": pcl_direct,
            "local_pcl_direct_active": local_direct_active,
            "recommended_route": route,
            "recommendation_reason": reason,
            "stability_score": score,
        }
        node_ip = str(record.get("tailscale_ip") or "")
        if not recommended_relay or not node_ip or node_ip == relay_ip:
            continue
        if route == "direct":
            if not any(
                edge["from"] == relay_ip and edge["to"] == node_ip and edge["type"] == "direct"
                for edge in edges
            ):
                edges.append({"from": relay_ip, "to": node_ip, "type": "direct", "verified": True})
        elif route == "local_pcl_direct":
            edges.append(
                {
                    "from": "pcl-api",
                    "to": node_ip,
                    "type": "local_pcl_direct",
                    "verified": local_direct_active,
                }
            )
        elif route == "bridge_via_local_mac" and local_ip:
            if local_ip != relay_ip and not any(
                edge["from"] == relay_ip and edge["to"] == local_ip for edge in edges
            ):
                edges.append({"from": relay_ip, "to": local_ip, "type": "direct", "verified": True})
            edges.append(
                {
                    "from": local_ip,
                    "to": node_ip,
                    "type": "ssh_reverse_bridge",
                    "verified": False,
                    "remote_port": 15723,
                }
            )
    return {
        "selected_gateway": gateway_url,
        "remote_gateway": remote_gateway,
        "checked_at": relay_report.get("checked_at"),
        "nodes": nodes,
        "ready_count": sum(
            1
            for node in nodes
            if node.get("client_status", {}).get("ready")
            or node.get("feasibility", {}).get("local_pcl_direct_active")
        ),
        "recommendation": {
            "relay_id": relay_ip,
            "relay_name": recommended_relay.get("node_name") if recommended_relay else "",
            "reason": "选择 PCL 凭据有效、在线且探测延迟最低的 Linux 网关" if recommended_relay else "没有可用中转站",
            "edges": edges,
        },
    }


def _source_archive() -> bytes:
    # Package the exact code that is executing this command. This matters during
    # development and after an App update: ~/.local/share may still contain an
    # older installed copy until bootstrap completes.
    package = Path(__file__).resolve().parent
    root = package.parent
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        archive.add(package, arcname="pcl_codex_bridge", filter=lambda info: None if "__pycache__" in info.name or info.name.endswith(".pyc") else info)
        for name in ("LICENSE", "NOTICE"):
            path = root / name
            if path.exists():
                archive.add(path, arcname=name)
    return stream.getvalue()


REMOTE_INSTALL_SOURCE = r'''
import io, json, os, pathlib, platform, subprocess, sys, tarfile, tempfile

gateway = os.environ["PCL_REMOTE_GATEWAY"]
if platform.system() not in {"Darwin", "Linux"}:
    raise SystemExit("Only macOS and Linux remote clients are supported")
if sys.version_info < (3, 9):
    raise SystemExit("Python 3.9 or newer is required for remote installation")
payload = sys.stdin.buffer.read()
with tempfile.TemporaryDirectory(prefix="pcl-codex-install-") as temp:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        archive.extractall(temp)
    env = os.environ.copy()
    env["PYTHONPATH"] = temp
    command = [sys.executable, "-m", "pcl_codex_bridge.cli", "--gateway-url", gateway, "install", "client"]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    raise SystemExit(result.returncode)
'''


def install_remote_client(target: str, gateway_url: str) -> Dict[str, Any]:
    requested_host = urllib.parse.urlparse(gateway_url).hostname
    if requested_host not in {"127.0.0.1", "localhost", "::1"}:
        gateway_url = effective_remote_gateway(gateway_url)
    parsed = urllib.parse.urlparse(gateway_url)
    if parsed.scheme != "http" or not parsed.hostname or not gateway_url.rstrip("/").endswith("/v1"):
        raise RuntimeError("Invalid selected gateway URL")
    source = "import os\nos.environ['PCL_REMOTE_GATEWAY'] = " + repr(gateway_url) + "\n" + REMOTE_INSTALL_SOURCE
    try:
        result = _run_remote_python(target, source, stdin=_source_archive(), timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Remote client installation timed out: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or "Remote client installation failed")
    status = remote_client_status(target, gateway_url)
    if not status.get("ready"):
        raise RuntimeError(f"Remote install completed but verification failed: {status.get('error') or status}")
    return {
        "ssh_target": target,
        "gateway": gateway_url,
        "client_version": __version__,
        "status": status,
        "vscode_reload_required": True,
        "scope": ["~/.codex", "~/.local/share/pcl-codex-bridge", "~/.local/bin/pcl-codex", "user native-router service"],
        "main_provider_preserved": True,
        "delegation": "native_spawn_agent",
        "selection": "native_custom_roles",
    }
