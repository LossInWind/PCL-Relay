#!/usr/bin/env python3
"""Tailnet-only Responses compatibility gateway for the PCL LLM API."""

from __future__ import annotations

import ipaddress
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
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .responses_protocol import (
    UPSTREAM_BASE,
    base_response,
    build_chat_request,
    encode_compaction_summary,
    generate_compaction_summary,
    is_v2_compaction_request,
    iter_chat_completion,
    read_api_key,
    reasoning_effort_policy,
    retained_compact_messages,
    upstream_request,
)
from .responses_stream import ResponsesStreamBridge, SseWriter


PORT = int(os.environ.get("PCL_CODEX_GATEWAY_PORT", "15722"))
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
                if "text/event-stream" in response.headers.get("Content-Type", "").lower():
                    while True:
                        chunk = response.readline()
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    reader = getattr(response, "read1", None) or response.read
                    while True:
                        chunk = reader(65536)
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
        sse.send(
            "response.created",
            {"response": base_response(body, response_id, "in_progress", [])},
        )
        sse.send(
            "response.in_progress",
            {"response": base_response(body, response_id, "in_progress", [])},
        )

        bridge = ResponsesStreamBridge(sse, body, chat)
        try:
            for event in iter_chat_completion(chat):
                bridge.process(event)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            failed = base_response(body, response_id, "failed", bridge.completed_output())
            failed["error"] = {"code": "upstream_error", "message": f"HTTP {exc.code}: {detail}"}
            sse.send("response.failed", {"response": failed})
            return
        except Exception as exc:
            failed = base_response(body, response_id, "failed", bridge.completed_output())
            failed["error"] = {"code": "gateway_error", "message": str(exc)}
            sse.send("response.failed", {"response": failed})
            log(f"responses failure: {exc}\n{traceback.format_exc()}")
            return

        output = bridge.finalize()
        completed = base_response(body, response_id, "completed", output)
        effort = reasoning_effort_policy(body)
        completed["metadata"].update(
            {
                "pcl_reasoning_chars": str(bridge.reasoning_chars),
                "pcl_reasoning_transport": "upstream_reasoning_content_as_summary",
                "pcl_reasoning_effort_requested": effort["requested"],
                "pcl_reasoning_effort_effective": effort["effective"],
                "pcl_reasoning_effort_mode": effort["mode"],
                "pcl_reasoning_effort_native_supported": effort["native_supported"],
                "pcl_tool_stream": (
                    "strict_json_fallback"
                    if bridge.used_fallback_tool
                    else ("native_delta" if bridge.tool_states else "none")
                ),
                "pcl_tool_fallback": "true" if bridge.used_fallback_tool else "false",
            }
        )
        if bridge.usage:
            prompt_tokens = int(bridge.usage.get("prompt_tokens") or 0)
            completion_tokens = int(bridge.usage.get("completion_tokens") or 0)
            details = bridge.usage.get("completion_tokens_details") or {}
            reasoning_tokens = int(details.get("reasoning_tokens") or 0) if isinstance(details, dict) else 0
            completed["usage"] = {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
                "total_tokens": int(bridge.usage.get("total_tokens") or prompt_tokens + completion_tokens),
            }
        log(
            "responses completed "
            f"model={body.get('model')} finish={bridge.finish_reason} content={bridge.content_chars} "
            f"reasoning={bridge.reasoning_chars} tools={len(bridge.tool_states)} "
            f"effort={effort['requested']}->{effort['effective']}:{effort['mode']} "
            f"fallback={bridge.used_fallback_tool}"
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
