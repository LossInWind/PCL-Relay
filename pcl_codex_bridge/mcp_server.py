#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Dict

from .client_config import detect_models, discover_models
from .models import DEFAULT_GATEWAY_URL, configured_agents, load_registry
from .runner import delegate


def tool_schema(name: str, description: str) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Concrete task for the PCL execution agent."},
                "workspace": {
                    "type": "string",
                    "description": "Absolute current Codex/VS Code workspace directory. Pass the main session's active workspace automatically.",
                },
                "timeout": {"type": "integer", "minimum": 30, "maximum": 3600, "default": 1800},
                "execution_mode": {
                    "type": "string",
                    "enum": ["read-only", "workspace-write"],
                    "default": "workspace-write",
                },
            },
            "required": ["task", "workspace"],
            "additionalProperties": False,
        },
    }


def tools() -> list:
    agents = configured_agents()
    result = [
        {
            "name": "pcl_models",
            "description": "Show the selected PCL agent catalog and capability status; optionally check the gateway for newly added models.",
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
            "name": "pcl_delegate",
            "description": "Delegate a full repository task to a named PCL execution agent while the main GPT remains the orchestrator.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": list(agents)},
                    "task": {"type": "string"},
                    "workspace": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 30, "maximum": 3600, "default": 1800},
                    "execution_mode": {
                        "type": "string",
                        "enum": ["read-only", "workspace-write"],
                        "default": "workspace-write",
                    },
                },
                "required": ["agent", "task", "workspace"],
                "additionalProperties": False,
            },
        },
    ]
    for agent, info in agents.items():
        result.append(tool_schema(agent, info["description"] + " The official GPT main agent reviews and integrates its result."))
    return result


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    if name == "pcl_models":
        if arguments.get("detect"):
            return detect_models(DEFAULT_GATEWAY_URL)
        if arguments.get("refresh"):
            return discover_models(DEFAULT_GATEWAY_URL)
        return load_registry()
    agents = configured_agents()
    if name == "pcl_delegate":
        agent = arguments.get("agent")
    elif name in agents:
        agent = name
    else:
        raise ValueError(f"Unknown tool: {name}")
    return delegate(
        str(agent),
        str(arguments.get("task", "")),
        str(arguments.get("workspace", "")),
        int(arguments.get("timeout", 1800)),
        str(arguments.get("execution_mode", "workspace-write")),
    )


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
                "serverInfo": {"name": "pcl-codex-agents", "version": "0.1.0"},
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
