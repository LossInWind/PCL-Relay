#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict

from .client_config import detect_models, discover_models, doctor, native_router_health
from .models import DEFAULT_GATEWAY_URL, configured_agents, load_registry


def gateway_url() -> str:
    return os.environ.get("PCL_CODEX_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")


def native_agents() -> Dict[str, str]:
    return {alias: "pcl/" + info["model"] for alias, info in configured_agents().items()}


def alias_contract() -> str:
    return ", ".join(f"{alias}={model}" for alias, model in native_agents().items())


def role_contract() -> str:
    return ", ".join(
        f"{alias.replace('_', '-')}={model}"
        for alias, model in native_agents().items()
    )


def tools() -> list:
    return [
        {
            "name": "pcl_models",
            "description": "Show or refresh the PCL model catalog. Execution uses Codex native custom roles through spawn_agent, never MCP or pcl_delegate. Exact roles: " + role_contract(),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "refresh": {"type": "boolean", "default": False, "description": "Refresh the model catalog without running expensive inference probes."},
                    "detect": {"type": "boolean", "default": False, "description": "Test chat, streaming, and tool compatibility for selected agents."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "pcl_native_status",
            "description": "Check whether PCL Relay's loopback router and Codex native custom-role sub-agent integration are ready.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    if name == "pcl_models":
        if arguments.get("detect"):
            value = detect_models(gateway_url())
        elif arguments.get("refresh"):
            value = discover_models(gateway_url())
        else:
            value = load_registry()
        return {
            "registry": value,
            "native_agents": native_agents(),
            "native_roles": role_contract(),
            "delegation": "Use Codex native spawn_agent/custom agent roles. Never use pcl_delegate.",
        }
    if name == "pcl_native_status":
        return {
            "router": native_router_health(),
            "codex": doctor(gateway_url()),
            "native_agents": native_agents(),
        }
    raise ValueError(f"Unknown tool: {name}")


def respond(request_id: Any, result: Any = None, error: Any = None) -> None:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(message: Dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        respond(
            request_id,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "pcl-relay-management", "version": "2.2.0"},
                "instructions": "PCL execution models are Codex native custom-role sub-agents. MCP is management-only and pcl_delegate does not exist. Select one of these native roles when delegating: " + role_contract(),
            },
        )
    elif method in {"notifications/initialized", "notifications/cancelled"}:
        return
    elif method == "ping":
        respond(request_id, {})
    elif method == "tools/list":
        respond(request_id, {"tools": tools()})
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            value = call_tool(str(params.get("name", "")), params.get("arguments") or {})
            respond(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(value, ensure_ascii=False, indent=2),
                        }
                    ],
                    "structuredContent": value if isinstance(value, dict) else {"result": value},
                    "isError": False,
                },
            )
        except Exception as exc:
            respond(
                request_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )
    elif request_id is not None:
        respond(request_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if isinstance(message, dict):
                handle(message)
        except Exception:
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
