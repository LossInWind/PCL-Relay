from __future__ import annotations

import json
import concurrent.futures
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    AGENTS,
    DEFAULT_GATEWAY_URL,
    available_model_records,
    codex_home,
    configured_agents,
    load_registry,
    model_catalog,
    save_registry,
)


BEGIN = "# >>> pcl-codex-bridge managed block >>>"
END = "# <<< pcl-codex-bridge managed block <<<"
INSTALL_ROOT = Path.home() / ".local" / "share" / "pcl-codex-bridge"
BIN_PATH = Path.home() / ".local" / "bin" / "pcl-codex"
UNSANDBOXED_MARKER = Path.home() / ".config" / "pcl-codex-bridge" / "allow-unsandboxed-fallback"
TAILSCALE_CANDIDATES = (
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    Path.home() / "Applications" / "Tailscale.app" / "Contents" / "MacOS" / "Tailscale",
    Path("/opt/homebrew/bin/tailscale"),
    Path("/usr/local/bin/tailscale"),
    Path("/usr/bin/tailscale"),
)


def gateway_root(url: str = DEFAULT_GATEWAY_URL) -> str:
    return url.rstrip("/")


def install_source_tree(source_root: Optional[Path] = None) -> Path:
    if getattr(sys, "frozen", False):
        BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        executable = Path(sys.executable).resolve()
        if executable != BIN_PATH.resolve():
            shutil.copy2(executable, BIN_PATH)
        os.chmod(BIN_PATH, 0o755)
        return INSTALL_ROOT
    if source_root is None:
        source_root = Path(__file__).resolve().parents[1]
    package_source = source_root / "pcl_codex_bridge"
    if not package_source.is_dir():
        raise RuntimeError(f"Package source not found: {package_source}")
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
    destination = INSTALL_ROOT / "pcl_codex_bridge"
    if package_source.resolve() != destination.resolve():
        shutil.copytree(package_source, destination, dirs_exist_ok=True)
    license_source = source_root / "LICENSE"
    notice_source = source_root / "NOTICE"
    if license_source.exists():
        shutil.copy2(license_source, INSTALL_ROOT / "LICENSE")
    if notice_source.exists():
        shutil.copy2(notice_source, INSTALL_ROOT / "NOTICE")
    BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    wrapper = (
        "#!/bin/sh\n"
        f"export PYTHONPATH={shell_quote(str(INSTALL_ROOT))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f"exec {shell_quote(sys.executable)} -m pcl_codex_bridge.cli \"$@\"\n"
    )
    BIN_PATH.write_text(wrapper, encoding="utf-8")
    os.chmod(BIN_PATH, 0o755)
    return INSTALL_ROOT


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def managed_block(gateway_url: str, executable: str, standalone: bool = False) -> str:
    mcp_module_root = str(INSTALL_ROOT)
    gateway_host = urllib.parse.urlparse(gateway_url).hostname or ""
    no_proxy = ["localhost", "127.0.0.1", "100.64.0.0/10", ".ts.net"]
    if gateway_host and gateway_host not in no_proxy:
        no_proxy.append(gateway_host)
    no_proxy_value = ",".join(no_proxy)
    args = '["mcp-server"]' if standalone else '["-m", "pcl_codex_bridge.mcp_server"]'
    lines = [
            BEGIN,
            "[model_providers.pcl_internal]",
            'name = "PCL Internal Models"',
            f"base_url = {json.dumps(gateway_url.rstrip('/'))}",
            'wire_api = "responses"',
            "requires_openai_auth = false",
            "stream_max_retries = 1",
            "stream_idle_timeout_ms = 900000",
            "",
            "[mcp_servers.pcl_agents]",
            f"command = {json.dumps(executable)}",
            f"args = {args}",
            "startup_timeout_sec = 15",
            "tool_timeout_sec = 3600",
            "enabled = true",
            'default_tools_approval_mode = "approve"',
            "",
            "[mcp_servers.pcl_agents.env]",
            'PYTHONDONTWRITEBYTECODE = "1"',
            f"PCL_CODEX_GATEWAY_URL = {json.dumps(gateway_url.rstrip('/'))}",
            f"NO_PROXY = {json.dumps(no_proxy_value)}",
            f"no_proxy = {json.dumps(no_proxy_value)}",
            END,
        ]
    if not standalone:
        lines.insert(lines.index('[mcp_servers.pcl_agents.env]') + 1, f"PYTHONPATH = {json.dumps(mcp_module_root)}")
    return "\n".join(lines)


def strip_managed_block(text: str) -> str:
    pattern = re.compile(r"\n?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    return pattern.sub("\n", text).rstrip() + ("\n" if text.strip() else "")


def backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, target)
    return target


def install_client_config(gateway_url: str = DEFAULT_GATEWAY_URL) -> Dict[str, str]:
    legacy_isolated_home = INSTALL_ROOT / "agent-codex-home"
    if legacy_isolated_home.exists():
        shutil.rmtree(legacy_isolated_home)
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    base = strip_managed_block(original)
    standalone = bool(getattr(sys, "frozen", False))
    block = managed_block(gateway_url, str(BIN_PATH) if standalone else sys.executable, standalone)
    updated = base.rstrip() + "\n\n" + block + "\n"
    backup_path = backup(config) if updated != original else None
    config.write_text(updated, encoding="utf-8")

    catalog = home / "pcl-models.json"
    registry = load_registry()
    catalog.write_text(json.dumps(model_catalog(configured_agents(registry)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    profile = home / "pcl-agent.config.toml"
    profile_text = "\n".join(
        [
            '# Managed by pcl-codex-bridge. The main Codex provider remains unchanged.',
            'model_provider = "pcl_internal"',
            'model = "DeepSeek-V4-Pro"',
            f"model_catalog_json = {json.dumps(str(catalog))}",
            'model_reasoning_effort = "medium"',
            'approval_policy = "never"',
            'sandbox_mode = "workspace-write"',
            "",
            "[mcp_servers.pcl_agents]",
            "enabled = false",
            "",
        ]
    )
    profile.write_text(profile_text, encoding="utf-8")

    return {
        "config": str(config),
        "profile": str(profile),
        "catalog": str(catalog),
        "backup": str(backup_path) if backup_path else "",
    }


def uninstall_client_config() -> Dict[str, Any]:
    home = codex_home()
    config = home / "config.toml"
    changed = False
    backup_path: Optional[Path] = None
    if config.exists():
        original = config.read_text(encoding="utf-8")
        updated = strip_managed_block(original)
        if updated != original:
            backup_path = backup(config)
            config.write_text(updated, encoding="utf-8")
            changed = True
    removed: List[str] = []
    for path in [home / "pcl-agent.config.toml", home / "pcl-models.json", UNSANDBOXED_MARKER]:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return {"config_changed": changed, "backup": str(backup_path or ""), "removed": removed}


def request_json(url: str, body: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Any:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def select_relay(gateway_url: str) -> Dict[str, Any]:
    normalized = gateway_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized += "/v1"
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise RuntimeError("Relay URL must look like http://<tailscale-node>:15722/v1")
    health = request_json(normalized.rsplit("/v1", 1)[0] + "/healthz", timeout=10)
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError("Selected node is not a healthy PCL relay")
    models = request_json(normalized + "/models", timeout=30)
    entries = models.get("data") if isinstance(models, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("Selected relay could not authenticate to the PCL model API")

    registry = load_registry()
    previous = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    registry["gateway"] = normalized
    registry["relay_selected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    registry["available_models"] = available_model_records(entries)
    registry.setdefault("models", {})
    save_registry(registry)
    config = install_client_config(normalized)
    return {
        "selected_gateway": normalized,
        "previous_gateway": previous,
        "model_count": len(entries),
        "pcl_auth": "valid",
        "codex_reload_required": True,
        "config": config,
        "main_provider_preserved": True,
    }


def request_stream_probe(url: str, body: Dict[str, Any], timeout: int = 120) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    saw_chunk = False
    saw_done = False
    with opener.open(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                continue
            if not data:
                continue
            payload = json.loads(data)
            if isinstance(payload, dict) and payload.get("choices"):
                saw_chunk = True
    return saw_chunk and saw_done


def request_responses_tool_probe(url: str, body: Dict[str, Any], timeout: int = 120) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            payload = json.loads(data)
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}:
                return item.get("name") == "pcl_probe"
    return False


def model_ids(payload: Any) -> List[str]:
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    result = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            result.append(str(entry["id"]))
    return result


def discover_models(gateway_url: str = DEFAULT_GATEWAY_URL) -> Dict[str, Any]:
    payload = request_json(gateway_root(gateway_url) + "/models", timeout=30)
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    records = available_model_records(entries)
    registry = load_registry()
    registry["gateway"] = gateway_url
    registry["catalog_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    registry["available_models"] = records
    if not isinstance(registry.get("selected_agents"), list):
        registry["selected_agents"] = list(AGENTS)
    if not isinstance(registry.get("agent_definitions"), dict):
        registry["agent_definitions"] = dict(AGENTS)
    registry.setdefault("models", {})
    save_registry(registry)
    return registry


def detect_models(gateway_url: str = DEFAULT_GATEWAY_URL, timeout: int = 120) -> Dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    previous = load_registry()
    definitions = configured_agents(previous)
    selected = previous.get("selected_agents") if isinstance(previous, dict) else None
    if not isinstance(selected, list):
        selected = list(definitions)
    report: Dict[str, Any] = {
        "gateway": gateway_url,
        "checked_at": now,
        "catalog_checked_at": previous.get("catalog_checked_at"),
        "selected_agents": [name for name in selected if name in definitions],
        "agent_definitions": definitions,
        "available_models": previous.get("available_models") or {},
        "models": dict(previous.get("models") or {}),
    }
    try:
        payload = request_json(gateway_root(gateway_url) + "/models", timeout=30)
        advertised = model_ids(payload)
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        report["available_models"] = available_model_records(entries)
        report["catalog_checked_at"] = now
    except Exception as exc:
        report["error"] = f"model discovery failed: {exc}"
        save_registry(report)
        return report

    for agent, info in definitions.items():
        model = info["model"]
        status: Dict[str, Any] = {
            "agent": agent,
            "model": model,
            "advertised": model in advertised,
            "chat": False,
            "stream": False,
            "tool_call": False,
            "tool_compatible": False,
            "tool_call_mode": "unavailable",
            "execution_ready": False,
            "error": "",
        }
        chat_body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply briefly with PCL_OK"}],
            "stream": False,
            "max_tokens": 64,
        }
        try:
            chat = request_json(
                gateway_root(gateway_url) + "/chat/completions",
                chat_body,
                timeout=min(timeout, 90),
            )
            status["chat"] = bool(isinstance(chat, dict) and chat.get("choices"))
        except Exception as exc:
            # Availability probes are read-only, so one bounded retry is safe.
            try:
                chat = request_json(
                    gateway_root(gateway_url) + "/chat/completions",
                    chat_body,
                    timeout=min(timeout, 90),
                )
                status["chat"] = bool(isinstance(chat, dict) and chat.get("choices"))
            except Exception as retry_exc:
                status["error"] = f"chat: {retry_exc}"
        if status["chat"]:
            try:
                stream_body = dict(chat_body)
                stream_body["stream"] = True
                status["stream"] = request_stream_probe(
                    gateway_root(gateway_url) + "/chat/completions",
                    stream_body,
                    timeout=min(timeout, 120),
                )
                if not status["stream"]:
                    status["error"] = (status["error"] + "; " if status["error"] else "") + "stream: incomplete SSE"
            except Exception as exc:
                status["error"] = (status["error"] + "; " if status["error"] else "") + f"stream: {exc}"
            native_tool_error = ""
            try:
                tool = request_json(
                    gateway_root(gateway_url) + "/chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": "Call the probe tool with value PCL_TOOL_OK. Do not answer normally.",
                            }
                        ],
                        "tools": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "pcl_probe",
                                    "description": "Capability probe",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"value": {"type": "string"}},
                                        "required": ["value"],
                                    },
                                },
                            }
                        ],
                        "tool_choice": "auto",
                        "stream": False,
                        "max_tokens": 512,
                    },
                    timeout=min(timeout, 120),
                )
                message = (((tool.get("choices") or [{}])[0]).get("message") or {}) if isinstance(tool, dict) else {}
                status["tool_call"] = bool(message.get("tool_calls"))
            except Exception as exc:
                native_tool_error = str(exc)

            if status["tool_call"]:
                status["tool_compatible"] = True
                status["tool_call_mode"] = "native"
            else:
                responses_probe = {
                    "model": model,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Call pcl_probe with value PCL_TOOL_OK. Do not answer normally.",
                                }
                            ],
                        }
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "name": "pcl_probe",
                            "description": "Capability probe",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        }
                    ],
                    "tool_choice": "auto",
                    "max_output_tokens": 512,
                }
                fallback_error = ""
                for _ in range(2):
                    try:
                        status["tool_compatible"] = request_responses_tool_probe(
                            gateway_root(gateway_url) + "/responses",
                            responses_probe,
                            timeout=min(timeout, 120),
                        )
                        if status["tool_compatible"]:
                            break
                    except Exception as exc:
                        fallback_error = str(exc)
                if status["tool_compatible"]:
                    status["tool_call_mode"] = "json_fallback"
                else:
                    details = fallback_error or native_tool_error or "no tool call returned"
                    status["error"] = (status["error"] + "; " if status["error"] else "") + f"tool: {details}"
            status["execution_ready"] = bool(
                status["chat"] and status["stream"] and status["tool_compatible"]
            )
        report["models"][agent] = status
    selected_statuses = [report["models"][name] for name in report["selected_agents"] if name in report["models"]]
    report["all_chat_ready"] = bool(selected_statuses) and all(item["chat"] for item in selected_statuses)
    report["all_stream_ready"] = bool(selected_statuses) and all(item["stream"] for item in selected_statuses)
    report["all_tool_compatible"] = bool(selected_statuses) and all(item["tool_compatible"] for item in selected_statuses)
    report["all_native_tools"] = bool(selected_statuses) and all(item["tool_call"] for item in selected_statuses)
    save_registry(report)
    return report


def doctor(gateway_url: str = DEFAULT_GATEWAY_URL) -> Dict[str, Any]:
    home = codex_home()
    config = home / "config.toml"
    result: Dict[str, Any] = {
        "gateway": False,
        "tailscale": bool(find_tailscale()),
        "codex": bool(find_codex()),
        "config_managed": False,
        "profile": (home / "pcl-agent.config.toml").exists(),
        "catalog": (home / "pcl-models.json").exists(),
        "registry": (Path.home() / ".config" / "pcl-codex-bridge" / "models.json").exists(),
        "unsandboxed_fallback": UNSANDBOXED_MARKER.exists(),
    }
    if config.exists():
        text = config.read_text(encoding="utf-8", errors="replace")
        result["config_managed"] = BEGIN in text and END in text
    try:
        health = request_json(gateway_root(gateway_url).rsplit("/v1", 1)[0] + "/healthz", timeout=10)
        result["gateway"] = isinstance(health, dict) and health.get("status") == "ok"
        if result["gateway"]:
            # The relay only listens on its Tailscale address, so a successful
            # health check is stronger evidence than a CLI-path check on macOS.
            result["tailscale"] = True
    except Exception as exc:
        result["gateway_error"] = str(exc)
    return result


def find_codex() -> Optional[str]:
    def usable(path: Path) -> bool:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        try:
            result = subprocess.run(
                [str(path), "--version"], capture_output=True, text=True, timeout=8, check=False
            )
            return result.returncode == 0 and "codex" in result.stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
            return False

    override = os.environ.get("PCL_CODEX_BIN")
    if override and usable(Path(override)):
        return override
    candidates = [Path("/Applications/ChatGPT.app/Contents/Resources/codex")]
    candidates.extend(Path.home().glob(".vscode-server/extensions/openai.chatgpt-*/bin/*/codex"))
    candidates.extend(Path.home().glob(".vscode/extensions/openai.chatgpt-*/bin/*/codex"))
    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if usable(candidate):
            return str(candidate)
    return None
