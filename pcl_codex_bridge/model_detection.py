from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, List

from .http_client import gateway_root, request_json
from .models import (
    AGENTS,
    DEFAULT_GATEWAY_URL,
    available_model_records,
    configured_agents,
    load_registry,
    save_registry,
)


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
        except Exception:
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
