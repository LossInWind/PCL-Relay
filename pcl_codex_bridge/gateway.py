#!/usr/bin/env python3
"""Tailnet-only Responses compatibility gateway for the PCL LLM API."""

from __future__ import annotations

import ipaddress
import base64
import binascii
import json
import os
import re
import select
import socket
import subprocess
import sys
import time
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__


UPSTREAM_BASE = os.environ.get("PCL_LLM_BASE_URL", "https://llmapi.pcl.ac.cn/v1").rstrip("/")
PORT = int(os.environ.get("PCL_CODEX_GATEWAY_PORT", "15722"))
KEY_PATH = Path(
    os.environ.get(
        "PCL_LLM_API_KEY_FILE",
        Path.home() / ".config" / "pcl-codex-bridge" / "api-key",
    )
).expanduser()
LOG_PATH = Path(
    os.environ.get(
        "PCL_CODEX_GATEWAY_LOG",
        Path.home() / ".local" / "state" / "pcl-codex-bridge" / "gateway.log",
    )
).expanduser()
STARTED_AT = time.time()
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TOPOLOGY_REPORT_TTL = int(os.environ.get("PCL_RELAY_TOPOLOGY_TTL", "120"))
TOPOLOGY_REPORTS: Dict[Tuple[int, str], Dict[str, Any]] = {}
TOPOLOGY_LOCK = threading.Lock()
PORTAL_URL = "https://llmapi.pcl.ac.cn"
PORTAL_DOMAIN = "pcl.ac.cn"
OCX_COMPACTION_PREFIX = "ocx1:"
COMPACTION_ITEM_TYPES = {"compaction", "compaction_summary", "context_compaction"}
COMPACT_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work."""
SUMMARY_PREFIX = (
    "Another language model started to solve this problem and produced a summary of its "
    "thinking process. You also have access to the state of the tools that were used by that "
    "language model. Use this to build on the work that has already been done and avoid "
    "duplicating work. Here is the summary produced by the other language model, use the "
    "information in this summary to assist with your own analysis:"
)
OPAQUE_COMPACTION_NOTE = (
    "[earlier conversation was compacted; the summary is stored in a format this model "
    "cannot read]"
)
COMPACT_V1_RETAINED_CHAR_BUDGET = 64_000 * 4


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {safe}\n")


def discover_tailscale_ip() -> str:
    explicit = os.environ.get("PCL_CODEX_GATEWAY_HOST")
    if explicit:
        return explicit
    result = subprocess.run(
        ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10, check=False
    )
    addresses = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not addresses:
        raise RuntimeError("No Tailscale IPv4 address; refusing to bind to a non-Tailnet interface")
    return addresses[0]


def read_api_key() -> str:
    try:
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read PCL API key file: {KEY_PATH}") from exc
    if not key:
        raise RuntimeError(f"PCL API key file is empty: {KEY_PATH}")
    return key


def tailnet_node() -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--self", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        self_node = payload.get("Self") if isinstance(payload, dict) else {}
        return self_node if isinstance(self_node, dict) else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def gateway_status() -> Dict[str, Any]:
    node = tailnet_node()
    addresses = node.get("TailscaleIPs") if isinstance(node.get("TailscaleIPs"), list) else []
    return {
        "status": "active",
        "service": "pcl-codex-gateway",
        "node_name": str(node.get("HostName") or "unknown"),
        "magic_dns": str(node.get("DNSName") or "").rstrip("."),
        "tailscale_ip": next((str(item) for item in addresses if ":" not in str(item)), discover_tailscale_ip()),
        "port": PORT,
        "pid": os.getpid(),
        "uptime_seconds": max(0, int(time.time() - STARTED_AT)),
        "upstream": UPSTREAM_BASE,
        "admin_scope": ["status", "logs", "restart_self", "portal_proxy", "topology_consensus"],
    }


def topology_snapshot() -> Dict[str, Any]:
    now = time.time()
    with TOPOLOGY_LOCK:
        expired = [
            report_key
            for report_key, report in TOPOLOGY_REPORTS.items()
            if now - float(report.get("received_at_epoch") or 0) > TOPOLOGY_REPORT_TTL
        ]
        for report_key in expired:
            TOPOLOGY_REPORTS.pop(report_key, None)
        reports = [dict(report) for report in TOPOLOGY_REPORTS.values()]
    reports.sort(
        key=lambda item: (
            int(item.get("round_id") or 0),
            str(item.get("node_name") or item.get("node_id") or ""),
        )
    )
    return {
        "status": "ok",
        "service": "pcl-relay-topology-consensus",
        "version": __version__,
        "generated_at_epoch": now,
        "ttl_seconds": TOPOLOGY_REPORT_TTL,
        "reports": reports,
    }


def record_topology_heartbeat(payload: Dict[str, Any], source_ip: str) -> Dict[str, Any]:
    node_id = str(payload.get("node_id") or "").strip()
    node_name = str(payload.get("node_name") or "").strip()
    if not node_id or len(node_id) > 255 or len(node_name) > 255:
        raise ValueError("invalid node identity")
    allowed = {
        "node_id",
        "node_name",
        "system",
        "client_version",
        "gateway",
        "coordinator",
        "pcl_direct",
        "pcl_latency_ms",
        "configured_gateway_reachable",
        "configured_gateway_latency_ms",
        "coordinator_reachable",
        "coordinator_latency_ms",
        "relay_reachable",
        "relay_latency_ms",
        "client_ready",
        "config_managed",
        "native_v2",
        "native_roles",
        "can_bridge",
        "reported_at_epoch",
        "round_id",
    }
    report = {key: payload.get(key) for key in allowed if key in payload}
    report["node_id"] = node_id
    report["node_name"] = node_name or node_id
    try:
        report["round_id"] = int(report.get("round_id") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid topology round") from exc
    if report["round_id"] <= 0:
        raise ValueError("invalid topology round")
    report["source_ip"] = source_ip
    report["received_at_epoch"] = time.time()
    with TOPOLOGY_LOCK:
        # Retain several fixed heartbeat rounds rather than only the newest
        # report.  Readers can then publish the newest *complete* round and
        # never render a half-old, half-new network during a round boundary.
        TOPOLOGY_REPORTS[(report["round_id"], node_id)] = report
    return report


def portal_target_allowed(host: str, port: int) -> bool:
    normalized = host.lower().rstrip(".")
    return port == 443 and (normalized == PORTAL_DOMAIN or normalized.endswith("." + PORTAL_DOMAIN))


def portal_pac(proxy_host: str, proxy_port: int = PORT) -> str:
    safe_host = proxy_host.replace("\\", "").replace('"', "")
    return (
        "function FindProxyForURL(url, host) {\n"
        f'  if (dnsDomainIs(host, ".{PORTAL_DOMAIN}") || host === "{PORTAL_DOMAIN}") '
        f'return "PROXY {safe_host}:{int(proxy_port)}";\n'
        '  return "DIRECT";\n'
        "}\n"
    )


def recent_logs(limit: int = 100) -> List[str]:
    safe_limit = max(1, min(int(limit), 200))
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [re.sub(r"key=[^\s]+", "key=[KEY_FILE]", line) for line in lines[-safe_limit:]]


def flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    parts: List[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {"input_text", "output_text", "text"}:
            parts.append(str(item.get("text", "")))
        elif isinstance(item, dict) and "text" in item:
            parts.append(str(item["text"]))
    return "\n".join(part for part in parts if part)


def encode_compaction_summary(summary: str) -> str:
    encoded = base64.b64encode(summary.encode("utf-8")).decode("ascii")
    return OCX_COMPACTION_PREFIX + encoded


def decode_compaction_summary(encrypted_content: Any) -> Optional[str]:
    if not isinstance(encrypted_content, str) or not encrypted_content.startswith(
        OCX_COMPACTION_PREFIX
    ):
        return None
    try:
        raw = base64.b64decode(
            encrypted_content[len(OCX_COMPACTION_PREFIX) :], validate=True
        )
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def compaction_item_to_text(item: Dict[str, Any]) -> str:
    summary = decode_compaction_summary(item.get("encrypted_content"))
    if summary:
        return f"{SUMMARY_PREFIX}\n\n{summary}"
    return OPAQUE_COMPACTION_NOTE


def is_v2_compaction_request(body: Dict[str, Any]) -> bool:
    inputs = body.get("input")
    return isinstance(inputs, list) and any(
        isinstance(item, dict) and item.get("type") == "compaction_trigger"
        for item in inputs
    )


def responses_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": str(instructions)})

    pending: List[Dict[str, Any]] = []

    def flush() -> None:
        if pending:
            messages.append({"role": "assistant", "content": "", "tool_calls": list(pending)})
            pending.clear()

    for item in body.get("input") or []:
        if not isinstance(item, dict):
            flush()
            messages.append({"role": "user", "content": str(item)})
            continue
        kind = item.get("type")
        if kind == "message":
            flush()
            role = item.get("role", "user")
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            messages.append({"role": role, "content": flatten_content(item.get("content"))})
        elif kind in {"function_call", "custom_tool_call"}:
            arguments = item.get("arguments")
            if arguments is None and kind == "custom_tool_call":
                arguments = {"input": item.get("input", "")}
            arguments = arguments or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            pending.append(
                {
                    "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {"name": item.get("name", "unknown"), "arguments": arguments},
                }
            )
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            flush()
            output = item.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id"),
                    "content": output,
                }
            )
        elif kind in COMPACTION_ITEM_TYPES:
            flush()
            messages.append({"role": "system", "content": compaction_item_to_text(item)})
        elif kind in {"compaction_trigger", "additional_tools"}:
            continue
        elif kind != "reasoning":
            flush()
            messages.append({"role": "user", "content": flatten_content(item)})
    flush()
    return normalize_messages(messages)


def normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    systems = [str(message.get("content", "")) for message in messages if message.get("role") == "system"]
    normal = [message for message in messages if message.get("role") != "system"]
    if systems:
        normal.insert(0, {"role": "system", "content": "\n\n".join(part for part in systems if part)})
    return normal


def responses_tools(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "custom" and tool.get("name"):
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", "")
                        + " Supply the freeform custom-tool input in the JSON field named input.",
                        "parameters": {
                            "type": "object",
                            "properties": {"input": {"type": "string"}},
                            "required": ["input"],
                        },
                    },
                }
            )
            continue
        if tool.get("type") != "function":
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return result


def fallback_tool_instruction(tools: List[Dict[str, Any]]) -> str:
    specs = []
    for tool in tools:
        function = tool.get("function", {})
        specs.append(
            {
                "name": function.get("name"),
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return (
        "If native function calling is unavailable and a tool is required, output ONLY one JSON "
        "object in this exact form: {\"tool_calls\":[{\"name\":\"tool_name\","
        "\"arguments\":{}}]}. Do not wrap it in Markdown. Available tools: "
        + json.dumps(specs, ensure_ascii=False)
    )


def build_chat_request(body: Dict[str, Any]) -> Dict[str, Any]:
    tools = responses_tools(body)
    messages = responses_messages(body)
    if tools:
        instruction = fallback_tool_instruction(tools)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content", "")) + "\n\n" + instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    request: Dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": True,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = body.get("tool_choice", "auto")
    request["max_tokens"] = body.get("max_output_tokens") or int(
        os.environ.get("PCL_CODEX_DEFAULT_MAX_TOKENS", "4096")
    )
    return request


def build_compaction_chat_request(body: Dict[str, Any]) -> Dict[str, Any]:
    compact_body = dict(body)
    compact_body["tools"] = []
    compact_body["input"] = [
        item
        for item in body.get("input") or []
        if not (
            isinstance(item, dict)
            and item.get("type") in {"compaction_trigger", "additional_tools"}
        )
    ]
    messages = responses_messages(compact_body)
    messages.append({"role": "user", "content": COMPACT_PROMPT})
    return {
        "model": body.get("model"),
        "messages": messages,
        "stream": True,
        "max_tokens": int(os.environ.get("PCL_CODEX_COMPACT_MAX_TOKENS", "4096")),
    }


def collect_chat_completion(
    chat: Dict[str, Any],
) -> Tuple[str, Dict[int, Dict[str, Any]], str, Optional[str]]:
    request = upstream_request(
        "/chat/completions", json.dumps(chat, ensure_ascii=False).encode("utf-8"), "POST"
    )
    content = ""
    tool_states: Dict[int, Dict[str, Any]] = {}
    reasoning = ""
    finish_reason: Optional[str] = None
    with urllib.request.urlopen(request, timeout=900) as upstream:
        for raw_line in upstream:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            chunk = json.loads(data)
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content += str(delta.get("content") or "")
            reasoning += str(delta.get("reasoning_content") or delta.get("reasoning") or "")
            if choice.get("finish_reason"):
                finish_reason = str(choice.get("finish_reason"))
            for tool_delta in delta.get("tool_calls") or []:
                index = int(tool_delta.get("index", 0))
                state = tool_states.setdefault(
                    index,
                    {
                        "id": f"fc_{uuid.uuid4().hex}",
                        "call_id": tool_delta.get("id") or f"call_{uuid.uuid4().hex}",
                        "name": "",
                        "arguments": "",
                    },
                )
                function = tool_delta.get("function") or {}
                state["name"] += str(function.get("name") or "")
                state["arguments"] += str(function.get("arguments") or "")
    return content, tool_states, reasoning, finish_reason


def generate_compaction_summary(body: Dict[str, Any]) -> str:
    content, tool_states, _, finish_reason = collect_chat_completion(
        build_compaction_chat_request(body)
    )
    summary = content.strip()
    if tool_states:
        raise RuntimeError("PCL compaction unexpectedly returned tool calls")
    if finish_reason in {"length", "max_tokens"}:
        raise RuntimeError("PCL compaction summary was truncated")
    if not summary:
        raise RuntimeError("PCL compaction returned an empty summary")
    return summary


def retained_compact_messages(inputs: Any) -> List[Dict[str, Any]]:
    if not isinstance(inputs, list):
        return []
    selected: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(inputs):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role") not in {"user", "developer"}:
            continue
        size = len(flatten_content(item.get("content")))
        if selected and used + size > COMPACT_V1_RETAINED_CHAR_BUDGET:
            break
        if not selected and size > COMPACT_V1_RETAINED_CHAR_BUDGET:
            continue
        selected.append(dict(item))
        used += size
    selected.reverse()
    return selected


def upstream_request(path: str, body: Optional[bytes] = None, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(
        f"{UPSTREAM_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {read_api_key()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "pcl-codex-bridge/0.1",
        },
    )


def parse_fallback_calls(text: str, allowed: Iterable[str]) -> List[Dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S).strip()
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match and match.group(0) != cleaned:
        candidates.append(match.group(0))
    payload: Any = None
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(payload, dict):
        return []
    raw_calls = payload.get("tool_calls")
    if raw_calls is None and isinstance(payload.get("tool_call"), dict):
        raw_calls = [payload["tool_call"]]
    if not isinstance(raw_calls, list):
        return []
    allowed_names = set(allowed)
    result = []
    for call in raw_calls:
        if not isinstance(call, dict) or call.get("name") not in allowed_names:
            continue
        arguments = call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                json.loads(arguments)
            except ValueError:
                arguments = json.dumps({"input": arguments}, ensure_ascii=False)
        else:
            arguments = json.dumps(arguments, ensure_ascii=False)
        result.append(
            {
                "id": f"fc_{uuid.uuid4().hex}",
                "call_id": f"call_{uuid.uuid4().hex}",
                "name": call["name"],
                "arguments": arguments,
            }
        )
    return result


def base_response(body: Dict[str, Any], response_id: str, status: str, output: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "id": response_id,
        "object": "response",
        "created_at": now,
        "completed_at": now if status == "completed" else None,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "max_output_tokens": body.get("max_output_tokens"),
        "model": body.get("model"),
        "output": output,
        "parallel_tool_calls": body.get("parallel_tool_calls", False),
        "previous_response_id": body.get("previous_response_id"),
        "reasoning": body.get("reasoning"),
        "store": body.get("store", False),
        "temperature": body.get("temperature"),
        "text": body.get("text", {"format": {"type": "text"}}),
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools", []),
        "top_p": body.get("top_p"),
        "truncation": body.get("truncation"),
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 0,
        },
        "metadata": body.get("metadata") or {},
    }


class SseWriter:
    def __init__(self, handler: BaseHTTPRequestHandler):
        self.handler = handler
        self.sequence = 1

    def send(self, event: str, payload: Dict[str, Any]) -> None:
        data = dict(payload)
        data.setdefault("type", event)
        data.setdefault("sequence_number", self.sequence)
        self.sequence += 1
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.handler.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.handler.wfile.write(b"data: " + raw + b"\n\n")
        self.handler.wfile.flush()


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _raw(self, status: int, raw: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _admin_allowed(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]) in TAILNET_V4
        except ValueError:
            return False

    def _topology_allowed(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
            return address.is_loopback or address in TAILNET_V4
        except ValueError:
            return False

    def do_GET(self) -> None:
        if self.path.startswith("http://"):
            self._proxy_portal_http()
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in {"/health", "/healthz", "/v1/healthz"}:
            self._json(
                200,
                {
                    "status": "ok",
                    "service": "pcl-codex-gateway",
                    "version": __version__,
                    "upstream": UPSTREAM_BASE,
                    "compaction": "responses_compact_v1+trigger_v2_ocx1",
                },
            )
            return
        if path == "/admin/status":
            if not self._admin_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            self._json(200, gateway_status())
            return
        if path == "/admin/topology":
            if not self._topology_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            self._json(200, topology_snapshot())
            return
        if path == "/admin/logs":
            if not self._admin_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            self._json(200, {"service": "pcl-codex-gateway", "lines": recent_logs(100)})
            return
        if path == "/admin/portal.pac":
            if not self._admin_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            proxy_host = str(self.server.server_address[0])
            self._raw(
                200,
                portal_pac(proxy_host).encode("utf-8"),
                "application/x-ns-proxy-autoconfig; charset=utf-8",
            )
            return
        if path == "/v1/models":
            self._proxy_simple("/models")
            return
        self._json(404, {"error": "not_found"})

    def do_CONNECT(self) -> None:
        if not self._admin_allowed():
            self._json(403, {"error": "tailnet_only"})
            return
        host, separator, raw_port = self.path.rpartition(":")
        try:
            port = int(raw_port) if separator else 443
        except ValueError:
            port = 0
        if not host or not portal_target_allowed(host, port):
            self._json(403, {"error": "portal_domain_only"})
            return
        upstream: Optional[socket.socket] = None
        try:
            upstream = socket.create_connection((host, port), timeout=15)
            self.send_response(200, "Connection Established")
            self.send_header("Proxy-Agent", "pcl-codex-gateway")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            sockets = [self.connection, upstream]
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                if exceptional or not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is self.connection else self.connection
                    destination.sendall(data)
        except OSError as exc:
            log(f"portal proxy failure host={host} error={type(exc).__name__}: {exc}")
            if upstream is None:
                self._json(502, {"error": "portal_unreachable"})
        finally:
            if upstream is not None:
                upstream.close()

    def _proxy_portal_http(self) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if (
                parsed.scheme != "http"
                or parsed.port not in {None, 80}
                or not portal_target_allowed(parsed.hostname or "", 443)
            ):
                self._json(403, {"error": "portal_domain_only"})
                return
            request = urllib.request.Request(
                self.path,
                headers={"User-Agent": self.headers.get("User-Agent", "PCL-Relay/1.2")},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=30) as response:
                raw = response.read()
                self._raw(response.status, raw, response.headers.get("Content-Type", "application/octet-stream"))
        except urllib.error.HTTPError as exc:
            self._json(exc.code, {"error": "portal_upstream_error"})
        except Exception as exc:
            self._json(502, {"error": "portal_gateway_error", "detail": str(exc)})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/admin/topology/heartbeat":
            if not self._topology_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            try:
                raw = self._body()
                if not raw or len(raw) > 32 * 1024:
                    raise ValueError("invalid heartbeat size")
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("heartbeat must be an object")
                report = record_topology_heartbeat(payload, self.client_address[0])
                self._json(202, {"status": "accepted", "node_id": report["node_id"]})
            except (UnicodeDecodeError, ValueError) as exc:
                self._json(400, {"error": "invalid_heartbeat", "detail": str(exc)})
            return
        if path == "/v1/chat/completions":
            self._proxy_chat()
            return
        if path == "/v1/responses":
            self._responses()
            return
        if path == "/v1/responses/compact":
            self._responses_compact()
            return
        if path == "/admin/restart":
            if not self._admin_allowed():
                self._json(403, {"error": "tailnet_only"})
                return
            self._json(202, {"status": "restarting", "service": "pcl-codex-gateway"})
            log(f"self restart requested by {self.client_address[0]}")
            threading.Thread(target=self._exit_for_restart, daemon=True).start()
            return
        self._json(404, {"error": "not_found"})

    @staticmethod
    def _exit_for_restart() -> None:
        time.sleep(0.25)
        os._exit(75)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length)

    def _proxy_simple(self, path: str) -> None:
        try:
            with urllib.request.urlopen(upstream_request(path), timeout=30) as response:
                raw = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as exc:
            self._json(exc.code, {"error": "upstream_error", "detail": exc.read().decode("utf-8", "replace")})
        except Exception as exc:
            self._json(502, {"error": "gateway_error", "detail": str(exc)})

    def _proxy_chat(self) -> None:
        raw_body = self._body()
        try:
            with urllib.request.urlopen(upstream_request("/chat/completions", raw_body, "POST"), timeout=900) as response:
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            self._json(exc.code, {"error": "upstream_error", "detail": exc.read().decode("utf-8", "replace")})
        except Exception as exc:
            self._json(502, {"error": "gateway_error", "detail": str(exc)})

    def _responses(self) -> None:
        try:
            body = json.loads(self._body().decode("utf-8"))
            if is_v2_compaction_request(body):
                self._responses_compaction_v2(body)
                return
            chat = build_chat_request(body)
        except Exception as exc:
            self._json(400, {"error": "invalid_request", "detail": str(exc)})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        sse = SseWriter(self)
        response_id = f"resp_{uuid.uuid4().hex}"
        output: List[Dict[str, Any]] = []
        sse.send(
            "response.created",
            {"response": base_response(body, response_id, "in_progress", [])},
        )
        sse.send(
            "response.in_progress",
            {"response": base_response(body, response_id, "in_progress", [])},
        )

        try:
            content, tool_states, reasoning, finish_reason = collect_chat_completion(chat)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            failed = base_response(body, response_id, "failed", output)
            failed["error"] = {"code": "upstream_error", "message": f"HTTP {exc.code}: {detail}"}
            sse.send("response.failed", {"response": failed})
            return
        except Exception as exc:
            failed = base_response(body, response_id, "failed", output)
            failed["error"] = {"code": "gateway_error", "message": str(exc)}
            sse.send("response.failed", {"response": failed})
            log(f"responses failure: {exc}\n{traceback.format_exc()}")
            return

        allowed = [tool["function"]["name"] for tool in chat.get("tools", [])]
        if not tool_states and allowed:
            for index, state in enumerate(parse_fallback_calls(content, allowed)):
                tool_states[index] = state
            if tool_states:
                content = ""

        if content:
            self._emit_text(sse, output, content)
        custom_names = {
            str(tool.get("name"))
            for tool in body.get("tools") or []
            if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name")
        }
        for _, state in sorted(tool_states.items()):
            self._emit_tool(sse, output, state, state.get("name") in custom_names)
        completed = base_response(body, response_id, "completed", output)
        if reasoning:
            completed["metadata"]["pcl_reasoning_chars"] = len(reasoning)
        log(
            "responses completed "
            f"model={body.get('model')} finish={finish_reason} content={len(content)} "
            f"reasoning={len(reasoning)} tools={len(tool_states)}"
        )
        sse.send("response.completed", {"response": completed})

    def _responses_compact(self) -> None:
        try:
            body = json.loads(self._body().decode("utf-8"))
            summary = generate_compaction_summary(body)
            output = retained_compact_messages(body.get("input"))
            output.append(
                {
                    "type": "compaction",
                    "encrypted_content": encode_compaction_summary(summary),
                }
            )
            self._json(200, {"output": output})
            log(
                "responses compact v1 completed "
                f"model={body.get('model')} retained={len(output) - 1} summary={len(summary)}"
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            self._json(
                502,
                {"error": "upstream_error", "detail": f"HTTP {exc.code}: {detail}"},
            )
        except Exception as exc:
            log(f"responses compact v1 failure: {exc}\n{traceback.format_exc()}")
            self._json(502, {"error": "compaction_failed", "detail": str(exc)})

    def _responses_compaction_v2(self, body: Dict[str, Any]) -> None:
        try:
            summary = generate_compaction_summary(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            self._json(
                502,
                {"error": "upstream_error", "detail": f"HTTP {exc.code}: {detail}"},
            )
            return
        except Exception as exc:
            log(f"responses compact v2 failure: {exc}\n{traceback.format_exc()}")
            self._json(502, {"error": "compaction_failed", "detail": str(exc)})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        sse = SseWriter(self)
        response_id = f"resp_{uuid.uuid4().hex}"
        item = {
            "type": "compaction",
            "encrypted_content": encode_compaction_summary(summary),
        }
        sse.send("response.created", {"response": base_response(body, response_id, "in_progress", [])})
        sse.send("response.in_progress", {"response": base_response(body, response_id, "in_progress", [])})
        sse.send("response.output_item.done", {"output_index": 0, "item": item})
        sse.send(
            "response.completed",
            {"response": base_response(body, response_id, "completed", [item])},
        )
        log(
            "responses compact v2 completed "
            f"model={body.get('model')} summary={len(summary)}"
        )

    @staticmethod
    def _emit_text(sse: SseWriter, output: List[Dict[str, Any]], text: str) -> None:
        item_id = f"msg_{uuid.uuid4().hex}"
        index = len(output)
        started = {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}
        sse.send("response.output_item.added", {"output_index": index, "item": started})
        sse.send(
            "response.content_part.added",
            {
                "item_id": item_id,
                "output_index": index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )
        sse.send(
            "response.output_text.delta",
            {"item_id": item_id, "output_index": index, "content_index": 0, "delta": text},
        )
        part = {"type": "output_text", "text": text, "annotations": []}
        item = {"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": [part]}
        output.append(item)
        sse.send(
            "response.output_text.done",
            {"item_id": item_id, "output_index": index, "content_index": 0, "text": text},
        )
        sse.send(
            "response.content_part.done",
            {"item_id": item_id, "output_index": index, "content_index": 0, "part": part},
        )
        sse.send("response.output_item.done", {"output_index": index, "item": item})

    @staticmethod
    def _emit_tool(
        sse: SseWriter,
        output: List[Dict[str, Any]],
        state: Dict[str, Any],
        custom: bool = False,
    ) -> None:
        index = len(output)
        if custom:
            try:
                parsed = json.loads(state["arguments"] or "{}")
                custom_input = parsed.get("input", "") if isinstance(parsed, dict) else str(parsed)
            except ValueError:
                custom_input = state["arguments"] or ""
            started = {
                "id": state["id"],
                "type": "custom_tool_call",
                "status": "in_progress",
                "call_id": state["call_id"],
                "name": state["name"],
                "input": "",
            }
            sse.send("response.output_item.added", {"output_index": index, "item": started})
            sse.send(
                "response.custom_tool_call_input.delta",
                {"item_id": state["id"], "output_index": index, "delta": custom_input},
            )
            item = dict(started)
            item["status"] = "completed"
            item["input"] = custom_input
            output.append(item)
            sse.send(
                "response.custom_tool_call_input.done",
                {"item_id": state["id"], "output_index": index, "input": custom_input},
            )
            sse.send("response.output_item.done", {"output_index": index, "item": item})
            return
        started = {
            "id": state["id"],
            "type": "function_call",
            "status": "in_progress",
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": "",
        }
        sse.send("response.output_item.added", {"output_index": index, "item": started})
        sse.send(
            "response.function_call_arguments.delta",
            {"item_id": state["id"], "output_index": index, "delta": state["arguments"] or "{}"},
        )
        item = dict(started)
        item["status"] = "completed"
        item["arguments"] = state["arguments"] or "{}"
        output.append(item)
        sse.send(
            "response.function_call_arguments.done",
            {
                "item_id": state["id"],
                "name": state["name"],
                "output_index": index,
                "arguments": item["arguments"],
            },
        )
        sse.send("response.output_item.done", {"output_index": index, "item": item})

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"{self.client_address[0]} {fmt % args}")


def main() -> None:
    host = discover_tailscale_ip()
    read_api_key()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, PORT), GatewayHandler)
    log(f"listening http://{host}:{PORT}; upstream={UPSTREAM_BASE}; key=[KEY_FILE]")
    print(f"PCL Codex gateway listening on http://{host}:{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
