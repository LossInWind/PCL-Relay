from __future__ import annotations

import concurrent.futures
import gzip
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, Tuple

from . import __version__
from .models import DEFAULT_GATEWAY_URL, load_registry
from .zstd_codec import decompress as zstd_decompress


SERVICE_NAME = "pcl-relay-native-router"
SERVICE_VERSION = __version__
DEFAULT_PORT = 15724
PCL_MODEL_PREFIX = "pcl/"
TOPOLOGY_HEARTBEAT_INTERVAL = int(os.environ.get("PCL_RELAY_HEARTBEAT_INTERVAL", "30"))
OPENAI_CODEX_BASE_URL = os.environ.get(
    "PCL_RELAY_OPENAI_BASE_URL",
    "https://chatgpt.com/backend-api/codex",
).rstrip("/")
MAX_REQUEST_BYTES = 96 * 1024 * 1024

# Forward only the caller identity and Codex request metadata needed by the
# official backend. This list follows the MIT-licensed OpenCodex forward-mode
# contract; arbitrary inbound headers never cross the trust boundary.
OFFICIAL_FORWARD_HEADERS = {
    "authorization",
    "chatgpt-account-id",
    "openai-beta",
    "originator",
    "session_id",
    "session-id",
    "thread-id",
    "x-client-request-id",
    "x-codex-beta-features",
    "x-codex-installation-id",
    "x-codex-parent-thread-id",
    "x-codex-turn-metadata",
    "x-codex-turn-state",
    "x-codex-window-id",
    "x-oai-attestation",
    "x-openai-subagent",
    "x-responsesapi-include-timing-metrics",
}


def selected_gateway() -> str:
    registry = load_registry()
    return str(registry.get("gateway") or DEFAULT_GATEWAY_URL).rstrip("/")


def _gateway_root(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized.rsplit("/v1", 1)[0] if normalized.endswith("/v1") else normalized


def _tailnet_identity(registry: Dict[str, Any]) -> Tuple[str, str]:
    node_id = str(registry.get("topology_node_id") or "")
    node_name = str(registry.get("topology_node_name") or "")
    candidates = [
        shutil.which("tailscale") or "",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/usr/bin/tailscale",
    ]
    for executable in dict.fromkeys(item for item in candidates if item):
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            continue
        try:
            result = subprocess.run(
                [executable, "status", "--self", "--json"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            payload = json.loads(result.stdout) if result.returncode == 0 else {}
            current = payload.get("Self") if isinstance(payload, dict) else {}
            addresses = current.get("TailscaleIPs") if isinstance(current, dict) else []
            node_id = node_id or next((str(value) for value in addresses or [] if ":" not in str(value)), "")
            node_name = node_name or str(current.get("HostName") or "")
            if node_id:
                break
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return node_id, node_name or socket.gethostname()


def _probe_health(url: str, timeout: int = 6) -> Tuple[bool, int]:
    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(_gateway_root(url) + "/healthz", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(isinstance(payload, dict) and payload.get("status") == "ok"), int((time.monotonic() - started) * 1000)
    except Exception:
        return False, int((time.monotonic() - started) * 1000)


def _probe_pcl_direct(timeout: int = 6) -> Tuple[bool, int]:
    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request("https://llmapi.pcl.ac.cn/v1/models")
        with opener.open(request, timeout=timeout):
            pass
        return True, int((time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as exc:
        return exc.code in {401, 403}, int((time.monotonic() - started) * 1000)
    except Exception:
        return False, int((time.monotonic() - started) * 1000)


def topology_heartbeat_payload() -> Dict[str, Any]:
    registry = load_registry()
    node_id, node_name = _tailnet_identity(registry)
    gateway = selected_gateway()
    coordinator = str(registry.get("topology_coordinator") or gateway).rstrip("/")
    # All endpoint measurements belong to the same round. Keep the configured
    # local gateway separate from the shared coordinator: a Pod may use a
    # healthy 127.0.0.1 adapter without being able to reach the selected relay.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        gateway_future = pool.submit(_probe_health, gateway)
        coordinator_future = (
            gateway_future if coordinator == gateway else pool.submit(_probe_health, coordinator)
        )
        pcl_future = pool.submit(_probe_pcl_direct)
        gateway_reachable, gateway_latency = gateway_future.result()
        coordinator_reachable, coordinator_latency = coordinator_future.result()
        pcl_direct, pcl_latency = pcl_future.result()
    config = os.path.expanduser("~/.codex/config.toml")
    try:
        config_text = open(config, encoding="utf-8", errors="replace").read()
    except OSError:
        config_text = ""
    roles_dir = os.path.expanduser("~/.codex/agents")
    native_roles = os.path.isdir(roles_dir) and any(name.startswith("pcl-") and name.endswith(".toml") for name in os.listdir(roles_dir))
    config_managed = "# >>> pcl-codex-bridge managed block >>>" in config_text and "# >>> pcl-relay native router root >>>" in config_text
    native_v2 = "[features.multi_agent_v2]" in config_text
    now = time.time()
    interval = max(10, TOPOLOGY_HEARTBEAT_INTERVAL)
    return {
        "node_id": node_id,
        "node_name": node_name,
        "system": platform.system(),
        "client_version": SERVICE_VERSION,
        "gateway": gateway,
        "coordinator": coordinator,
        "pcl_direct": pcl_direct,
        "pcl_latency_ms": pcl_latency,
        "configured_gateway_reachable": gateway_reachable,
        "configured_gateway_latency_ms": gateway_latency,
        "coordinator_reachable": coordinator_reachable,
        "coordinator_latency_ms": coordinator_latency,
        # Backward-compatible names mean the selected shared relay, never a
        # private loopback adapter.
        "relay_reachable": coordinator_reachable,
        "relay_latency_ms": coordinator_latency,
        "client_ready": bool(node_id and gateway_reachable and config_managed and native_v2 and native_roles),
        "config_managed": config_managed,
        "native_v2": native_v2,
        "native_roles": native_roles,
        "can_bridge": bool(node_id and platform.system() in {"Darwin", "Linux"}),
        "reported_at_epoch": now,
        "round_id": int(now // interval),
    }


def send_topology_heartbeat() -> None:
    payload = topology_heartbeat_payload()
    if not payload.get("node_id"):
        return
    registry = load_registry()
    targets = {
        selected_gateway(),
        str(registry.get("topology_coordinator") or "").rstrip("/"),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for target in (item for item in targets if item):
        try:
            request = urllib.request.Request(
                _gateway_root(target) + "/admin/topology/heartbeat",
                data=raw,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=8):
                pass
        except Exception:
            continue


def _topology_heartbeat_loop(stop: threading.Event) -> None:
    interval = max(10, TOPOLOGY_HEARTBEAT_INTERVAL)
    while not stop.is_set():
        send_topology_heartbeat()
        next_round = (int(time.time() // interval) + 1) * interval
        stop.wait(max(1.0, next_round - time.time()))


def decode_request_body(raw: bytes, content_encoding: str = "") -> Tuple[Dict[str, Any], bytes]:
    codings = [item.strip().lower() for item in content_encoding.split(",") if item.strip().lower() not in {"", "identity"}]
    decoded = raw
    for encoding in reversed(codings):
        if encoding in {"gzip", "x-gzip"}:
            decoded = gzip.decompress(decoded)
        elif encoding == "deflate":
            try:
                decoded = zlib.decompress(decoded)
            except zlib.error:
                decoded = zlib.decompress(decoded, -zlib.MAX_WBITS)
        elif encoding in {"zstd", "zst"}:
            decoded = zstd_decompress(decoded, MAX_REQUEST_BYTES)
        else:
            raise ValueError(f"Unsupported request content encoding: {encoding}")
        if len(decoded) > MAX_REQUEST_BYTES:
            raise ValueError("Decompressed request body exceeds the safety limit")
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Responses request must be a JSON object")
    return payload, decoded


def route_request(payload: Dict[str, Any]) -> Tuple[str, str]:
    model = str(payload.get("model") or "")
    if model.startswith(PCL_MODEL_PREFIX):
        upstream_model = model[len(PCL_MODEL_PREFIX) :]
        if not upstream_model:
            raise ValueError("PCL model route is missing its upstream model id")
        return "pcl", upstream_model
    return "openai", model


def rewrite_pcl_body(payload: Dict[str, Any], upstream_model: str) -> bytes:
    rewritten = dict(payload)
    rewritten["model"] = upstream_model
    # These fields are meaningful only to ChatGPT's native service and can make
    # strict OpenAI-compatible gateways reject an otherwise valid child turn.
    rewritten.pop("service_tier", None)
    return json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_v2_agent_messages_plaintext(payload: Dict[str, Any]) -> int:
    """Remove only the cross-provider task encryption marker from agents.*.

    Codex V2 normally asks the official backend to encrypt collaboration
    ``message`` arguments. A PCL child cannot decrypt that OpenAI-owned
    envelope. The non-reserved ``agents`` namespace is selected by our managed
    config, so it is safe to request plaintext function arguments there.
    Reserved ``collaboration`` schemas and reasoning ``encrypted_content`` are
    deliberately untouched.
    """

    def rewrite_tools(tools: Any, inside_agents: bool = False) -> int:
        if not isinstance(tools, list):
            return 0
        changed = 0
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "namespace":
                if str(tool.get("name") or "").lower() != "agents":
                    continue
                children = tool.get("tools")
                if not isinstance(children, list):
                    children = tool.get("children")
                changed += rewrite_tools(children, True)
                continue
            if tool.get("type") != "function":
                continue
            explicit_agents = str(tool.get("namespace") or "").lower() == "agents"
            if not inside_agents and not explicit_agents:
                continue
            if tool.get("name") not in {"spawn_agent", "send_message", "followup_task"}:
                continue
            parameters = tool.get("parameters")
            properties = parameters.get("properties") if isinstance(parameters, dict) else None
            message = properties.get("message") if isinstance(properties, dict) else None
            if isinstance(message, dict) and "encrypted" in message:
                message.pop("encrypted", None)
                changed += 1
        return changed

    changed = rewrite_tools(payload.get("tools"))
    inputs = payload.get("input")
    if isinstance(inputs, list):
        for item in inputs:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                changed += rewrite_tools(item.get("tools"))
    return changed


def rewrite_official_body(payload: Dict[str, Any], decoded: bytes) -> bytes:
    """Prepare official requests without changing their bytes unnecessarily."""
    if make_v2_agent_messages_plaintext(payload) == 0:
        return decoded
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def official_proxy_url() -> str:
    return os.environ.get("PCL_RELAY_OFFICIAL_PROXY", "").strip()


def opener_for(route: str) -> urllib.request.OpenerDirector:
    if route == "pcl":
        # Tailnet traffic must never leak into the user's Internet proxy.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy = official_proxy_url()
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    # On macOS this also respects the system proxy configuration.
    return urllib.request.build_opener()


def upstream_url(route: str, request_path: str) -> str:
    path, separator, query = request_path.partition("?")
    if not path.startswith("/v1/"):
        raise ValueError(f"Unsupported data-plane path: {path}")
    suffix = path[len("/v1") :]
    if route == "pcl":
        if suffix not in {"/responses", "/responses/compact", "/chat/completions"}:
            raise ValueError(f"PCL child models do not support {suffix}")
        return selected_gateway() + suffix
    allowed = {
        "/responses",
        "/responses/compact",
        # Codex executes its built-in search client-side against the configured
        # base URL.  This endpoint is private to the authenticated ChatGPT
        # Codex backend, so it must always stay on the official trust route.
        "/alpha/search",
        "/images/generations",
        "/images/edits",
    }
    if suffix not in allowed:
        raise ValueError(f"Official Codex route does not support {suffix}")
    target = OPENAI_CODEX_BASE_URL + suffix
    return target + (separator + query if separator else "")


def route_for_path(payload: Dict[str, Any], request_path: str) -> Tuple[str, str]:
    """Choose a trust route before inspecting a possibly routed model id.

    Search and image endpoints are Codex-hosted sidecars.  They are not PCL
    inference requests even when their JSON body happens to mention a PCL
    model, and forwarding them to the Tailnet gateway would either reject the
    request or leak the caller's ChatGPT request shape into the wrong domain.
    """
    path = request_path.split("?", 1)[0]
    if path in {"/v1/alpha/search", "/v1/images/generations", "/v1/images/edits"}:
        return "openai", str(payload.get("model") or "")
    return route_request(payload)


def outbound_headers(
    route: str,
    inbound: Iterable[Tuple[str, str]],
    body_length: int,
) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
        "Accept-Encoding": "identity",
        "Content-Length": str(body_length),
        "User-Agent": f"PCL-Relay/{SERVICE_VERSION}",
    }
    if route == "openai":
        for name, value in inbound:
            if name.lower() in OFFICIAL_FORWARD_HEADERS:
                headers[name] = value
    return headers


def _public_response_headers(headers: Any) -> Dict[str, str]:
    allowed = {
        "content-type",
        "cache-control",
        "x-request-id",
        "openai-processing-ms",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
    }
    result: Dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in allowed:
            result[name] = value
    return result


def relay_upstream_body(response: Any, target: Any) -> None:
    """Copy an upstream response without buffering SSE events.

    ``HTTPResponse.read(size)`` is allowed to wait until ``size`` bytes have
    arrived.  That is useful for downloads but makes a token stream appear to
    complete all at once.  SSE is line framed, so forward each line and flush
    immediately.  For every other content type, ``read1`` preserves efficient
    chunking while returning currently available bytes when the implementation
    supports it.
    """

    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "text/event-stream" in content_type:
        while True:
            chunk = response.readline()
            if not chunk:
                break
            target.write(chunk)
            target.flush()
        return

    reader = getattr(response, "read1", None) or response.read
    while True:
        chunk = reader(64 * 1024)
        if not chunk:
            break
        target.write(chunk)
        target.flush()


class NativeRouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"PCLRelayNativeRouter/{SERVICE_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            "%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), fmt % args)
        )
        sys.stderr.flush()

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/v1/responses" and self.headers.get("Upgrade", "").lower() == "websocket":
            # Codex's built-in OpenAI provider may optimistically try WS even
            # when the routed catalog disables it.  OpenCodex established the
            # clean contract: 426 flips this session to HTTP immediately;
            # 404/500 instead trigger repeated reconnects.
            self._json(
                426,
                {
                    "error": {
                        "message": "Responses WebSocket is disabled; use HTTP/SSE",
                        "type": "upgrade_required",
                        "code": "responses_websocket_not_supported",
                    }
                },
            )
            return
        if path in {"/healthz", "/v1/healthz"}:
            gateway = selected_gateway()
            gateway_ok = False
            error = ""
            try:
                health_url = gateway.rsplit("/v1", 1)[0] + "/healthz"
                with opener_for("pcl").open(health_url, timeout=5) as response:
                    value = json.loads(response.read().decode("utf-8"))
                gateway_ok = isinstance(value, dict) and value.get("status") == "ok"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self._json(
                200,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "gateway": gateway,
                    "gateway_reachable": gateway_ok,
                    "gateway_error": error,
                    "official_route": "chatgpt-forward",
                    "multi_agent_surface": "v2_custom_roles",
                    "compaction": "responses_compact_v1+trigger_v2_ocx1",
                },
            )
            return
        if path == "/v1/models":
            try:
                from .client_config import combined_catalog

                models = combined_catalog().get("models", [])
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": item.get("slug"),
                                "object": "model",
                                "owned_by": "pcl" if str(item.get("slug", "")).startswith(PCL_MODEL_PREFIX) else "openai",
                            }
                            for item in models
                            if item.get("slug")
                        ],
                    },
                )
            except Exception as exc:
                self._json(500, {"error": {"message": str(exc), "type": "router_error"}})
            return
        self._json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Invalid or oversized request body")
            raw = self.rfile.read(length)
            payload, decoded = decode_request_body(raw, self.headers.get("Content-Encoding", ""))
            route, model = route_for_path(payload, self.path)
            body = rewrite_pcl_body(payload, model) if route == "pcl" else rewrite_official_body(payload, decoded)
            target = upstream_url(route, self.path)
            request = urllib.request.Request(
                target,
                data=body,
                method="POST",
                headers=outbound_headers(route, self.headers.items(), len(body)),
            )
            with opener_for(route).open(request, timeout=1800) as response:
                self.send_response(response.status)
                for name, value in _public_response_headers(response.headers).items():
                    self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                relay_upstream_body(response, self.wfile)
            self.close_connection = True
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            for name, value in _public_response_headers(exc.headers).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            self._json(
                400,
                {
                    "error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "type": "pcl_relay_router_error",
                    }
                },
            )


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), NativeRouterHandler)
    server.daemon_threads = True
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_topology_heartbeat_loop,
        args=(heartbeat_stop,),
        name="pcl-relay-topology-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    sys.stderr.write(
        f"{SERVICE_NAME} {SERVICE_VERSION} listening on {host}:{port}; gateway={selected_gateway()}\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        heartbeat_stop.set()
        server.server_close()


def main() -> None:
    port = int(os.environ.get("PCL_RELAY_NATIVE_PORT", str(DEFAULT_PORT)))
    serve("127.0.0.1", port)


if __name__ == "__main__":
    main()
