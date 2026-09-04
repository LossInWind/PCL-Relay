"""OpenAI Responses to PCL Chat Completions protocol adapter."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


UPSTREAM_BASE = os.environ.get("PCL_LLM_BASE_URL", "https://llmapi.pcl.ac.cn/v1").rstrip("/")
KEY_PATH = Path(
    os.environ.get(
        "PCL_LLM_API_KEY_FILE",
        Path.home() / ".config" / "pcl-codex-bridge" / "api-key",
    )
).expanduser()
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
REASONING_EFFORT_GUIDANCE = {
    "minimal": "Use the shortest adequate analysis and act directly.",
    "low": "Use brief analysis, then act directly.",
    "medium": "Balance analysis, implementation, and verification.",
    "high": "Analyze thoroughly, check assumptions, and verify the result before concluding.",
    "xhigh": "Analyze very thoroughly, consider failure modes, and perform strong verification before concluding.",
}


def read_api_key() -> str:
    try:
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read PCL API key file: {KEY_PATH}") from exc
    if not key:
        raise RuntimeError(f"PCL API key file is empty: {KEY_PATH}")
    return key


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

    inputs = body.get("input")
    # The Responses API accepts either the full item array used by Codex or a
    # plain input string used by small SDK clients.  A string is iterable in
    # Python, so normalize it before the item loop instead of accidentally
    # turning every character into a separate chat message.
    if isinstance(inputs, str):
        messages.append({"role": "user", "content": inputs})
        return normalize_messages(messages)
    if isinstance(inputs, dict):
        inputs = [inputs]

    pending: List[Dict[str, Any]] = []

    def flush() -> None:
        if pending:
            messages.append({"role": "assistant", "content": "", "tool_calls": list(pending)})
            pending.clear()

    for item in inputs or []:
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


def reasoning_effort_policy(body: Dict[str, Any]) -> Dict[str, str]:
    """Map Codex reasoning effort without claiming an unsupported native control.

    The PCL gateway documents Chat Completions sampling and tool parameters, but
    currently does not advertise an OpenAI-compatible ``reasoning_effort``
    parameter.  Preserve the user's intent through explicit prompt steering and
    report that compatibility mode honestly in response metadata.
    """
    reasoning = body.get("reasoning")
    requested = str(reasoning.get("effort") or "medium") if isinstance(reasoning, dict) else "medium"
    normalized = requested.lower().strip()
    if normalized not in REASONING_EFFORT_GUIDANCE:
        normalized = "medium"
    return {
        "requested": requested,
        "effective": normalized,
        "mode": "prompt_compat",
        "native_supported": "false",
        "guidance": REASONING_EFFORT_GUIDANCE[normalized],
    }

def append_system_instruction(messages: List[Dict[str, Any]], instruction: str) -> None:
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = str(messages[0].get("content", "")) + "\n\n" + instruction
    else:
        messages.insert(0, {"role": "system", "content": instruction})


def build_chat_request(body: Dict[str, Any]) -> Dict[str, Any]:
    tools = responses_tools(body)
    messages = responses_messages(body)
    effort = reasoning_effort_policy(body)
    append_system_instruction(
        messages,
        "PCL Relay reasoning guidance "
        f"(Codex requested {effort['requested']}; compatibility mode): {effort['guidance']}",
    )
    if tools:
        append_system_instruction(messages, fallback_tool_instruction(tools))
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
    inputs = body.get("input")
    if isinstance(inputs, list):
        compact_body["input"] = [
            item
            for item in inputs
            if not (
                isinstance(item, dict)
                and item.get("type") in {"compaction_trigger", "additional_tools"}
            )
        ]
    else:
        compact_body["input"] = inputs
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
    content = ""
    tool_states: Dict[int, Dict[str, Any]] = {}
    reasoning = ""
    finish_reason: Optional[str] = None
    for event in iter_chat_completion(chat):
        kind = event.get("kind")
        if kind == "content":
            content += str(event.get("delta") or "")
        elif kind == "reasoning":
            reasoning += str(event.get("delta") or "")
        elif kind == "finish":
            finish_reason = str(event.get("reason") or "") or finish_reason
        elif kind == "tool":
            index = int(event.get("index") or 0)
            state = tool_states.setdefault(
                index,
                {
                    "id": f"fc_{uuid.uuid4().hex}",
                    "call_id": event.get("call_id") or f"call_{uuid.uuid4().hex}",
                    "name": "",
                    "arguments": "",
                },
            )
            if event.get("call_id"):
                state["call_id"] = event["call_id"]
            state["name"] += str(event.get("name_delta") or "")
            state["arguments"] += str(event.get("arguments_delta") or "")
    return content, tool_states, reasoning, finish_reason


def _completion_chunk_events(chunk: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta") or choice.get("message") or {}
    content = delta.get("content")
    if content:
        yield {"kind": "content", "delta": str(content)}
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        yield {"kind": "reasoning", "delta": str(reasoning)}
    for fallback_index, tool_delta in enumerate(delta.get("tool_calls") or []):
        function = tool_delta.get("function") or {}
        yield {
            "kind": "tool",
            "index": int(tool_delta.get("index", fallback_index)),
            "call_id": tool_delta.get("id"),
            "name_delta": str(function.get("name") or ""),
            "arguments_delta": str(function.get("arguments") or ""),
        }
    if choice.get("finish_reason"):
        yield {"kind": "finish", "reason": str(choice["finish_reason"])}
    if isinstance(chunk.get("usage"), dict):
        yield {"kind": "usage", "usage": dict(chunk["usage"])}


def iter_chat_completion(chat: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Yield normalized deltas as soon as the upstream SSE produces them."""
    request = upstream_request(
        "/chat/completions", json.dumps(chat, ensure_ascii=False).encode("utf-8"), "POST"
    )
    with urllib.request.urlopen(request, timeout=900) as upstream:
        saw_sse = False
        plain_lines: List[str] = []
        for raw_line in upstream:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                if line:
                    plain_lines.append(line)
                continue
            saw_sse = True
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            yield from _completion_chunk_events(json.loads(data))
        if not saw_sse and plain_lines:
            yield from _completion_chunk_events(json.loads("\n".join(plain_lines)))


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
