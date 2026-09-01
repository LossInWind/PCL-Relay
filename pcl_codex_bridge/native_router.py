from __future__ import annotations

import gzip
import json
import os
import sys
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, Optional, Tuple

from .models import DEFAULT_GATEWAY_URL, load_registry
from .zstd_codec import decompress as zstd_decompress


SERVICE_NAME = "pcl-relay-native-router"
SERVICE_VERSION = "2.0.0"
DEFAULT_PORT = 15724
PCL_MODEL_PREFIX = "pcl/"
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
    path = request_path.split("?", 1)[0]
    if not path.startswith("/v1/"):
        raise ValueError(f"Unsupported data-plane path: {path}")
    suffix = path[len("/v1") :]
    if route == "pcl":
        if suffix not in {"/responses", "/chat/completions"}:
            raise ValueError(f"PCL child models do not support {suffix}")
        return selected_gateway() + suffix
    allowed = {
        "/responses",
        "/responses/compact",
        "/images/generations",
        "/images/edits",
    }
    if suffix not in allowed:
        raise ValueError(f"Official Codex route does not support {suffix}")
    return OPENAI_CODEX_BASE_URL + suffix


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


class NativeRouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PCLRelayNativeRouter/0.2"

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
                    "multi_agent_surface": "v1",
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
            route, model = route_request(payload)
            body = rewrite_pcl_body(payload, model) if route == "pcl" else decoded
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
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
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
    sys.stderr.write(
        f"{SERVICE_NAME} {SERVICE_VERSION} listening on {host}:{port}; gateway={selected_gateway()}\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def main() -> None:
    port = int(os.environ.get("PCL_RELAY_NATIVE_PORT", str(DEFAULT_PORT)))
    serve("127.0.0.1", port)


if __name__ == "__main__":
    main()
