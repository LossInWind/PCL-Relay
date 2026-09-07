"""Streaming conversion from Chat Completions deltas to Responses events."""

from __future__ import annotations

import json
import re
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional

from .responses_protocol import parse_fallback_calls


class ToolArgumentsError(ValueError):
    """A model emitted arguments that cannot safely be passed to Codex."""

    def __init__(self, tool_name: str, detail: str):
        super().__init__(f"Invalid arguments for tool {tool_name}: {detail}")
        self.tool_name = tool_name


def _single_string_property(schema: Dict[str, Any]) -> Optional[str]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or len(properties) != 1:
        return None
    name, definition = next(iter(properties.items()))
    if not isinstance(definition, dict) or definition.get("type") != "string":
        return None
    return str(name)


def _recover_single_string_argument(
    arguments: str,
    schema: Dict[str, Any],
    *,
    allow_bare: bool,
) -> Optional[tuple[str, str]]:
    """Recover only the unambiguous one-string-parameter tool shape."""
    property_name = _single_string_property(schema)
    if property_name is None:
        return None
    raw = arguments
    pattern = rf'^\s*\{{\s*[\"\']{re.escape(property_name)}[\"\']\s*:\s*(.*)$'
    match = re.match(pattern, arguments, re.S)
    if match:
        value = match.group(1)
        # Unified-diff custom tools have an explicit terminator, so anything
        # after it is certainly a broken JSON wrapper rather than patch data.
        marker = "*** End Patch"
        marker_at = value.rfind(marker)
        if marker_at >= 0:
            value = value[: marker_at + len(marker)]
        return property_name, value
    if allow_bare:
        return property_name, raw
    return None


def _validate_function_arguments(
    tool_name: str,
    parsed: Dict[str, Any],
    schema: Dict[str, Any],
) -> None:
    required = schema.get("required") if isinstance(schema, dict) else None
    if isinstance(required, list):
        missing = [str(name) for name in required if name not in parsed]
        if missing:
            raise ToolArgumentsError(tool_name, "missing required fields: " + ", ".join(missing))
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return
    type_checks = {
        "string": lambda value: isinstance(value, str),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "boolean": lambda value: isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    for name, value in parsed.items():
        definition = properties.get(name)
        expected = definition.get("type") if isinstance(definition, dict) else None
        check = type_checks.get(str(expected))
        if check is not None and not check(value):
            raise ToolArgumentsError(tool_name, f"field {name} must be {expected}")


def normalize_function_arguments(
    tool_name: str,
    arguments: str,
    schema: Optional[Dict[str, Any]] = None,
) -> tuple[str, bool]:
    """Return valid, schema-checked JSON arguments and repair metadata."""
    raw = arguments or "{}"
    parameters = schema or {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        recovered = _recover_single_string_argument(raw, parameters, allow_bare=False)
        if recovered is None:
            raise ToolArgumentsError(tool_name or "unknown", str(exc)) from exc
        key, value = recovered
        parsed = {key: value}
        _validate_function_arguments(tool_name, parsed, parameters)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")), True
    if not isinstance(parsed, dict):
        raise ToolArgumentsError(tool_name or "unknown", "arguments must be a JSON object")
    _validate_function_arguments(tool_name, parsed, parameters)
    return raw, False


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


class ResponsesStreamBridge:
    """Translate Chat Completions deltas into ordered Responses API events."""

    def __init__(self, sse: SseWriter, body: Dict[str, Any], chat: Dict[str, Any]):
        self.sse = sse
        self.body = body
        self.chat = chat
        self.next_output_index = 0
        self.output_slots: Dict[int, Dict[str, Any]] = {}
        self.reasoning_state: Optional[Dict[str, Any]] = None
        self.text_state: Optional[Dict[str, Any]] = None
        self.tool_states: Dict[int, Dict[str, Any]] = {}
        self.custom_names = {
            str(tool.get("name"))
            for tool in body.get("tools") or []
            if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name")
        }
        self.allowed_tools = [
            str(tool["function"]["name"])
            for tool in chat.get("tools") or []
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        ]
        self.tool_schemas = {
            str(tool["function"]["name"]): tool["function"].get("parameters") or {}
            for tool in chat.get("tools") or []
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("name")
        }
        self.pending_content = ""
        self.content_mode = "undecided" if self.allowed_tools else "text"
        self.content_chars = 0
        self.reasoning_chars = 0
        self.finish_reason: Optional[str] = None
        self.usage: Optional[Dict[str, Any]] = None
        self.used_fallback_tool = False
        self.repaired_tool_arguments: List[str] = []

    def _allocate_index(self) -> int:
        index = self.next_output_index
        self.next_output_index += 1
        return index

    def _start_reasoning(self) -> None:
        self._finish_text()
        item_id = f"rs_{uuid.uuid4().hex}"
        index = self._allocate_index()
        self.reasoning_state = {"id": item_id, "output_index": index, "text": ""}
        self.sse.send(
            "response.output_item.added",
            {"output_index": index, "item": {"id": item_id, "type": "reasoning", "summary": []}},
        )
        self.sse.send(
            "response.reasoning_summary_part.added",
            {
                "item_id": item_id,
                "output_index": index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": ""},
            },
        )

    def append_reasoning(self, delta: str) -> None:
        if not delta:
            return
        if self.reasoning_state is None:
            self._start_reasoning()
        state = self.reasoning_state
        assert state is not None
        state["text"] += delta
        self.reasoning_chars += len(delta)
        self.sse.send(
            "response.reasoning_summary_text.delta",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "summary_index": 0,
                "delta": delta,
            },
        )

    def _finish_reasoning(self) -> None:
        state = self.reasoning_state
        if state is None:
            return
        part = {"type": "summary_text", "text": state["text"]}
        self.sse.send(
            "response.reasoning_summary_text.done",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "summary_index": 0,
                "text": state["text"],
            },
        )
        self.sse.send(
            "response.reasoning_summary_part.done",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "summary_index": 0,
                "part": part,
            },
        )
        item = {"id": state["id"], "type": "reasoning", "summary": [part]}
        self.output_slots[state["output_index"]] = item
        self.sse.send(
            "response.output_item.done",
            {"output_index": state["output_index"], "item": item},
        )
        self.reasoning_state = None

    def _start_text(self) -> None:
        self._finish_reasoning()
        item_id = f"msg_{uuid.uuid4().hex}"
        index = self._allocate_index()
        self.text_state = {"id": item_id, "output_index": index, "text": ""}
        started = {
            "id": item_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        self.sse.send("response.output_item.added", {"output_index": index, "item": started})
        self.sse.send(
            "response.content_part.added",
            {
                "item_id": item_id,
                "output_index": index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )

    def _append_text(self, delta: str) -> None:
        if not delta:
            return
        if self.text_state is None:
            self._start_text()
        state = self.text_state
        assert state is not None
        state["text"] += delta
        self.sse.send(
            "response.output_text.delta",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "content_index": 0,
                "delta": delta,
            },
        )

    def _finish_text(self) -> None:
        state = self.text_state
        if state is None:
            return
        part = {"type": "output_text", "text": state["text"], "annotations": []}
        item = {
            "id": state["id"],
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [part],
        }
        self.sse.send(
            "response.output_text.done",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "content_index": 0,
                "text": state["text"],
            },
        )
        self.sse.send(
            "response.content_part.done",
            {
                "item_id": state["id"],
                "output_index": state["output_index"],
                "content_index": 0,
                "part": part,
            },
        )
        self.output_slots[state["output_index"]] = item
        self.sse.send(
            "response.output_item.done",
            {"output_index": state["output_index"], "item": item},
        )
        self.text_state = None

    @staticmethod
    def _could_be_fallback_prefix(text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return True
        lowered = stripped.lower()
        if lowered.startswith("{") or lowered.startswith("```"):
            return True
        return any(prefix.startswith(lowered) for prefix in ("```", "```json"))

    def append_content(self, delta: str) -> None:
        if not delta:
            return
        self.content_chars += len(delta)
        if self.content_mode == "text":
            self._append_text(delta)
            return
        self.pending_content += delta
        if not self._could_be_fallback_prefix(self.pending_content):
            self.content_mode = "text"
            pending = self.pending_content
            self.pending_content = ""
            self._append_text(pending)

    def _start_function_tool(self, state: Dict[str, Any]) -> None:
        self._finish_reasoning()
        self._finish_text()
        index = self._allocate_index()
        state["output_index"] = index
        state["started"] = True
        started = {
            "id": state["id"],
            "type": "function_call",
            "status": "in_progress",
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": "",
        }
        self.sse.send("response.output_item.added", {"output_index": index, "item": started})

    def append_tool(self, event: Dict[str, Any]) -> None:
        index = int(event.get("index") or 0)
        state = self.tool_states.setdefault(
            index,
            {
                "id": f"fc_{uuid.uuid4().hex}",
                "call_id": event.get("call_id") or f"call_{uuid.uuid4().hex}",
                "name": "",
                "arguments": "",
                "started": False,
            },
        )
        if event.get("call_id") and not state["started"]:
            state["call_id"] = event["call_id"]
        state["name"] += str(event.get("name_delta") or "")
        arguments_delta = str(event.get("arguments_delta") or "")
        state["arguments"] += arguments_delta
        if not state["started"] and arguments_delta and state["name"] not in self.custom_names:
            self._start_function_tool(state)
        # Buffer native arguments until the complete JSON value can be
        # validated.  Emitting an invalid prefix poisons the next Responses
        # turn even if the final event is repaired.

    def _custom_input(self, name: str, arguments: str) -> tuple[str, bool]:
        schema = self.tool_schemas.get(name, {})
        try:
            parsed = json.loads(arguments or "{}")
            if isinstance(parsed, dict):
                key = _single_string_property(schema) or "input"
                return str(parsed.get(key, "")), False
            return str(parsed), False
        except ValueError:
            recovered = _recover_single_string_argument(arguments, schema, allow_bare=True)
            if recovered is None:
                return arguments or "", False
            return recovered[1], True

    def _finish_tool(self, state: Dict[str, Any]) -> None:
        custom = state["name"] in self.custom_names
        if custom:
            self._finish_reasoning()
            self._finish_text()
            index = self._allocate_index()
            custom_input, repaired = self._custom_input(state["name"], state["arguments"])
            if repaired:
                self.repaired_tool_arguments.append(state["name"])
            started = {
                "id": state["id"],
                "type": "custom_tool_call",
                "status": "in_progress",
                "call_id": state["call_id"],
                "name": state["name"],
                "input": "",
            }
            self.sse.send("response.output_item.added", {"output_index": index, "item": started})
            if custom_input:
                self.sse.send(
                    "response.custom_tool_call_input.delta",
                    {
                        "item_id": state["id"],
                        "call_id": state["call_id"],
                        "output_index": index,
                        "delta": custom_input,
                    },
                )
            item = dict(started)
            item["status"] = "completed"
            item["input"] = custom_input
            self.sse.send(
                "response.custom_tool_call_input.done",
                {"item_id": state["id"], "output_index": index, "input": custom_input},
            )
        else:
            if not state["started"]:
                self._start_function_tool(state)
            index = int(state["output_index"])
            arguments, repaired = normalize_function_arguments(
                state["name"],
                state["arguments"],
                self.tool_schemas.get(state["name"]),
            )
            if repaired:
                self.repaired_tool_arguments.append(state["name"])
            self.sse.send(
                "response.function_call_arguments.delta",
                {
                    "item_id": state["id"],
                    "output_index": index,
                    "delta": arguments,
                },
            )
            item = {
                "id": state["id"],
                "type": "function_call",
                "status": "completed",
                "call_id": state["call_id"],
                "name": state["name"],
                "arguments": arguments,
            }
            self.sse.send(
                "response.function_call_arguments.done",
                {
                    "item_id": state["id"],
                    "name": state["name"],
                    "output_index": index,
                    "arguments": arguments,
                },
            )
        self.output_slots[index] = item
        self.sse.send("response.output_item.done", {"output_index": index, "item": item})

    def process(self, event: Dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "reasoning":
            self.append_reasoning(str(event.get("delta") or ""))
        elif kind == "content":
            self.append_content(str(event.get("delta") or ""))
        elif kind == "tool":
            self.append_tool(event)
        elif kind == "finish":
            self.finish_reason = str(event.get("reason") or "") or self.finish_reason
        elif kind == "usage" and isinstance(event.get("usage"), dict):
            self.usage = dict(event["usage"])

    def finalize(self) -> List[Dict[str, Any]]:
        if self.content_mode == "undecided" and self.pending_content:
            calls = [] if self.tool_states else parse_fallback_calls(self.pending_content, self.allowed_tools)
            if calls:
                self.used_fallback_tool = True
                for index, state in enumerate(calls):
                    state["started"] = False
                    self.tool_states[index] = state
                self.pending_content = ""
            else:
                pending = self.pending_content
                self.pending_content = ""
                self.content_mode = "text"
                self._append_text(pending)
        self._finish_reasoning()
        self._finish_text()
        for _, state in sorted(self.tool_states.items()):
            self._finish_tool(state)
        return self.completed_output()

    def completed_output(self) -> List[Dict[str, Any]]:
        return [self.output_slots[index] for index in sorted(self.output_slots)]
