from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from .http_client import request_json
from .models import DEFAULT_GATEWAY_URL, load_registry, save_registry


TAILSCALE_CANDIDATES = (
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    Path.home() / "Applications" / "Tailscale.app" / "Contents" / "MacOS" / "Tailscale",
    Path("/opt/homebrew/bin/tailscale"),
    Path("/usr/local/bin/tailscale"),
    Path("/usr/bin/tailscale"),
)


def find_tailscale() -> Optional[str]:
    """Locate the Tailscale CLI even when a macOS app has a minimal PATH."""
    candidates: List[Path] = []
    override = os.environ.get("PCL_TAILSCALE_BIN", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    found = shutil.which("tailscale")
    if found:
        candidates.append(Path(found))
    candidates.extend(TAILSCALE_CANDIDATES)

    seen = set()
    for candidate in candidates:
        path = str(candidate)
        if path in seen:
            continue
        seen.add(path)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return path
    return None


def _tailscale_status() -> Dict[str, Any]:
    executable = find_tailscale()
    if not executable:
        raise RuntimeError(
            "Tailscale CLI is not installed or could not be found; "
            "install Tailscale.app or set PCL_TAILSCALE_BIN"
        )
    result = subprocess.run(
        [executable, "status", "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Tailscale is not connected")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("Tailscale returned an invalid status document")
    return payload


def _tailnet_nodes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    raw_nodes: List[tuple[Dict[str, Any], bool]] = []
    self_node = payload.get("Self")
    if isinstance(self_node, dict):
        raw_nodes.append((self_node, True))
    peers = payload.get("Peer")
    if isinstance(peers, dict):
        raw_nodes.extend((value, False) for value in peers.values() if isinstance(value, dict))
    elif isinstance(peers, list):
        raw_nodes.extend((value, False) for value in peers if isinstance(value, dict))

    seen = set()
    for node, is_self in raw_nodes:
        addresses = node.get("TailscaleIPs") if isinstance(node.get("TailscaleIPs"), list) else []
        ipv4 = next((str(value) for value in addresses if ":" not in str(value)), "")
        if not ipv4 or ipv4 in seen:
            continue
        seen.add(ipv4)
        dns_name = str(node.get("DNSName") or "").rstrip(".")
        nodes.append(
            {
                "node_name": str(node.get("HostName") or dns_name or ipv4),
                "magic_dns": dns_name,
                "tailscale_ip": ipv4,
                "online": True if is_self else bool(node.get("Online")),
                "self": is_self,
            }
        )
    return nodes


def _probe_relay(node: Dict[str, Any], port: int, timeout: float, selected_url: str) -> Dict[str, Any]:
    record = dict(node)
    host = record.get("magic_dns") or record["tailscale_ip"]
    gateway_url = f"http://{host}:{port}/v1"
    record.update(
        {
            "gateway_url": gateway_url,
            "gateway": False,
            "pcl_auth": "not_checked",
            "model_count": 0,
            "latency_ms": None,
            "selected": urllib.parse.urlparse(selected_url).hostname in {
                record.get("magic_dns"),
                record.get("tailscale_ip"),
            },
            "error": "",
        }
    )
    if not record["online"]:
        record["error"] = "tailnet_offline"
        return record
    started = time.monotonic()
    try:
        health = request_json(gateway_url.rsplit("/v1", 1)[0] + "/healthz", timeout=timeout)
        if not isinstance(health, dict) or health.get("status") != "ok":
            raise RuntimeError("not a PCL relay")
        upstream = str(health.get("upstream") or "")
        service = str(health.get("service") or "")
        if service and service != "pcl-codex-gateway":
            raise RuntimeError("unexpected service identity")
        if upstream and "llmapi.pcl.ac.cn" not in upstream:
            raise RuntimeError("unexpected upstream")
        record["gateway"] = True
        record["service"] = service or "pcl-codex-gateway"
        record["version"] = str(health.get("version") or "legacy")
        models = request_json(gateway_url + "/models", timeout=max(timeout, 8))
        entries = models.get("data") if isinstance(models, dict) else None
        record["model_count"] = len(entries) if isinstance(entries, list) else 0
        record["pcl_auth"] = "valid"
    except urllib.error.HTTPError as exc:
        record["pcl_auth"] = "invalid" if exc.code in {401, 403} else "upstream_error"
        record["error"] = f"http_{exc.code}"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["latency_ms"] = int((time.monotonic() - started) * 1000)
    return record


def discover_relays(port: int = 15722, timeout: float = 2.0) -> Dict[str, Any]:
    payload = _tailscale_status()
    nodes = _tailnet_nodes(payload)
    registry = load_registry()
    selected_url = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    online = [node for node in nodes if node["online"]]
    results: Dict[str, Dict[str, Any]] = {}
    if online:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(online))) as pool:
            futures = {
                pool.submit(_probe_relay, node, port, timeout, selected_url): node["tailscale_ip"]
                for node in online
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()
    for node in nodes:
        if node["tailscale_ip"] not in results:
            results[node["tailscale_ip"]] = _probe_relay(node, port, timeout, selected_url)
    ordered = sorted(
        results.values(),
        key=lambda item: (
            not bool(item.get("selected")),
            not bool(item.get("gateway")),
            not bool(item.get("online")),
            str(item.get("node_name", "")).lower(),
        ),
    )
    report = {
        "tailnet_connected": True,
        "selected_gateway": selected_url,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ready_count": sum(
            1 for item in ordered if item.get("gateway") and item.get("pcl_auth") == "valid"
        ),
        "nodes": ordered,
    }
    registry["relay_discovery"] = report
    save_registry(registry)
    return report
