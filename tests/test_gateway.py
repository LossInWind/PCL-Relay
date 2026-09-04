import json
import http.client
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.gateway import (
    GatewayHandler,
    gateway_status,
    portal_pac,
    portal_target_allowed,
    recent_logs,
    record_topology_heartbeat,
    topology_snapshot,
)
from pcl_codex_bridge.responses_protocol import (
    COMPACT_PROMPT,
    OPAQUE_COMPACTION_NOTE,
    SUMMARY_PREFIX,
    build_compaction_chat_request,
    build_chat_request,
    decode_compaction_summary,
    encode_compaction_summary,
    generate_compaction_summary,
    is_v2_compaction_request,
    parse_fallback_calls,
    reasoning_effort_policy,
    retained_compact_messages,
    responses_messages,
)


class GatewayMappingTests(unittest.TestCase):
    def test_responses_string_input_is_one_user_message(self):
        messages = responses_messages(
            {"instructions": "Be concise.", "input": "请回答：7乘以8是多少？"}
        )
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "请回答：7乘以8是多少？"},
            ],
        )

    def test_compaction_envelope_round_trip_and_replay(self):
        encoded = encode_compaction_summary("keep decisions and next steps")
        self.assertTrue(encoded.startswith("ocx1:"))
        self.assertEqual(decode_compaction_summary(encoded), "keep decisions and next steps")
        messages = responses_messages(
            {
                "input": [
                    {"type": "compaction", "encrypted_content": encoded},
                    {"type": "message", "role": "user", "content": "continue"},
                ]
            }
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(SUMMARY_PREFIX, messages[0]["content"])
        self.assertIn("keep decisions", messages[0]["content"])

    def test_official_opaque_compaction_degrades_to_safe_note(self):
        messages = responses_messages(
            {"input": [{"type": "compaction", "encrypted_content": "official-opaque"}]}
        )
        self.assertEqual(messages, [{"role": "system", "content": OPAQUE_COMPACTION_NOTE}])

    def test_detects_v2_compaction_and_builds_tool_free_summary_turn(self):
        body = {
            "model": "DeepSeek-V4-Pro",
            "input": [
                {"type": "message", "role": "user", "content": "work"},
                {"type": "additional_tools", "tools": [{"type": "function"}]},
                {"type": "compaction_trigger"},
            ],
            "tools": [{"type": "function", "name": "shell"}],
        }
        self.assertTrue(is_v2_compaction_request(body))
        chat = build_compaction_chat_request(body)
        self.assertNotIn("tools", chat)
        self.assertEqual(chat["messages"][-1]["content"], COMPACT_PROMPT)
        self.assertNotIn("compaction_trigger", json.dumps(chat))
        self.assertNotIn("additional_tools", json.dumps(chat))

    def test_compaction_rejects_empty_truncated_or_tool_output(self):
        for result, error in [
            (("", {}, "", "stop"), "empty"),
            (("partial", {}, "", "length"), "truncated"),
            (("summary", {0: {"name": "shell"}}, "", "stop"), "tool calls"),
        ]:
            with (
                self.subTest(error=error),
                mock.patch("pcl_codex_bridge.responses_protocol.collect_chat_completion", return_value=result),
                self.assertRaisesRegex(RuntimeError, error),
            ):
                generate_compaction_summary({"model": "DeepSeek-V4-Pro", "input": []})

    def test_v1_retains_only_recent_user_and_developer_messages(self):
        inputs = [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "answer"},
            {"type": "function_call", "name": "shell"},
            {"type": "message", "role": "developer", "content": "rules"},
            {"type": "message", "role": "user", "content": "latest"},
        ]
        retained = retained_compact_messages(inputs)
        self.assertEqual([item["content"] for item in retained], ["first", "rules", "latest"])

    def test_compaction_http_contracts_match_codex_v1_and_v2(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                mock.patch(
                    "pcl_codex_bridge.gateway.generate_compaction_summary",
                    return_value="checkpoint summary",
                ),
                mock.patch("pcl_codex_bridge.gateway.log"),
            ):
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                v1_body = json.dumps(
                    {
                        "model": "DeepSeek-V4-Pro",
                        "input": [
                            {"type": "message", "role": "user", "content": "keep me"},
                            {"type": "message", "role": "assistant", "content": "drop me"},
                        ],
                    }
                )
                connection.request(
                    "POST",
                    "/v1/responses/compact",
                    body=v1_body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    [item["type"] for item in payload["output"]],
                    ["message", "compaction"],
                )
                self.assertEqual(
                    decode_compaction_summary(payload["output"][-1]["encrypted_content"]),
                    "checkpoint summary",
                )

                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                v2_body = json.dumps(
                    {
                        "model": "DeepSeek-V4-Pro",
                        "input": [
                            {"type": "message", "role": "user", "content": "work"},
                            {"type": "compaction_trigger"},
                        ],
                    }
                )
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=v2_body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                raw = response.read().decode("utf-8")
                done_items = []
                for line in raw.splitlines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event.get("type") == "response.output_item.done":
                        done_items.append(event["item"])
                self.assertEqual(response.status, 200)
                self.assertEqual(len(done_items), 1)
                self.assertEqual(done_items[0]["type"], "compaction")
                self.assertEqual(
                    decode_compaction_summary(done_items[0]["encrypted_content"]),
                    "checkpoint summary",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_responses_history_groups_tool_call_and_output(self):
        body = {
            "instructions": "system",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run"}]},
                {"type": "function_call", "call_id": "call_a", "name": "shell", "arguments": '{"cmd":"pwd"}'},
                {"type": "function_call_output", "call_id": "call_a", "output": "/tmp"},
            ],
        }
        messages = responses_messages(body)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call_a")
        self.assertEqual(messages[3]["role"], "tool")

    def test_builds_chat_tools_and_fallback_instruction(self):
        body = {
            "model": "DeepSeek-V4-Pro",
            "input": [{"type": "message", "role": "user", "content": "use tool"}],
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "description": "run",
                    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
        }
        chat = build_chat_request(body)
        self.assertEqual(chat["tools"][0]["function"]["name"], "shell")
        self.assertIn("tool_calls", chat["messages"][0]["content"])

    def test_reasoning_effort_is_honest_prompt_compatibility(self):
        body = {
            "model": "DeepSeek-V4-Pro",
            "reasoning": {"effort": "xhigh", "summary": "detailed"},
            "input": [{"type": "message", "role": "user", "content": "work"}],
        }
        policy = reasoning_effort_policy(body)
        chat = build_chat_request(body)
        self.assertEqual(policy["requested"], "xhigh")
        self.assertEqual(policy["effective"], "xhigh")
        self.assertEqual(policy["mode"], "prompt_compat")
        self.assertEqual(policy["native_supported"], "false")
        self.assertIn("Codex requested xhigh", chat["messages"][0]["content"])
        self.assertNotIn("reasoning_effort", chat)

    @staticmethod
    def _sse_events(raw: str):
        result = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                result.append(json.loads(line[6:]))
        return result

    def test_responses_streams_reasoning_before_upstream_finishes(self):
        first_delta_sent = threading.Event()
        allow_finish = threading.Event()

        def slow_stream(_chat):
            first_delta_sent.set()
            yield {"kind": "reasoning", "delta": "先检查"}
            if not allow_finish.wait(2):
                raise RuntimeError("test did not consume the first live delta")
            yield {"kind": "reasoning", "delta": "，再执行"}
            yield {"kind": "content", "delta": "完成"}
            yield {"kind": "finish", "reason": "stop"}

        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                mock.patch("pcl_codex_bridge.gateway.iter_chat_completion", side_effect=slow_stream),
                mock.patch("pcl_codex_bridge.gateway.log"),
            ):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=json.dumps({"model": "GLM-5.2", "input": []}),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                observed = None
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    line = response.readline().decode("utf-8")
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "response.reasoning_summary_text.delta":
                            observed = event
                            break
                self.assertTrue(first_delta_sent.is_set())
                self.assertIsNotNone(observed)
                self.assertEqual(observed["delta"], "先检查")
                self.assertFalse(allow_finish.is_set())
                allow_finish.set()
                remainder = response.read().decode("utf-8")
                events = self._sse_events(remainder)
                self.assertTrue(any(event.get("type") == "response.completed" for event in events))
        finally:
            allow_finish.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_responses_preserves_native_tool_argument_deltas(self):
        def tool_stream(_chat):
            yield {
                "kind": "tool",
                "index": 0,
                "call_id": "call_live",
                "name_delta": "shell",
                "arguments_delta": '{"cmd":',
            }
            yield {
                "kind": "tool",
                "index": 0,
                "name_delta": "",
                "arguments_delta": '"pwd"}',
            }
            yield {"kind": "finish", "reason": "tool_calls"}

        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                mock.patch("pcl_codex_bridge.gateway.iter_chat_completion", side_effect=tool_stream),
                mock.patch("pcl_codex_bridge.gateway.log"),
            ):
                body = {
                    "model": "DeepSeek-V4-Pro",
                    "input": [],
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                }
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                events = self._sse_events(response.read().decode("utf-8"))
            deltas = [
                event["delta"]
                for event in events
                if event.get("type") == "response.function_call_arguments.delta"
            ]
            self.assertEqual(deltas, ['{"cmd":', '"pwd"}'])
            done = next(
                event
                for event in events
                if event.get("type") == "response.output_item.done"
                and event.get("item", {}).get("type") == "function_call"
            )
            self.assertEqual(done["item"]["arguments"], '{"cmd":"pwd"}')
            completed = next(event for event in events if event.get("type") == "response.completed")
            self.assertEqual(completed["response"]["metadata"]["pcl_tool_stream"], "native_delta")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_responses_buffers_strict_json_tool_fallback(self):
        def fallback_stream(_chat):
            yield {"kind": "content", "delta": '{"tool_calls":['}
            yield {
                "kind": "content",
                "delta": '{"name":"shell","arguments":{"cmd":"pwd"}}]}',
            }
            yield {"kind": "finish", "reason": "stop"}

        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                mock.patch("pcl_codex_bridge.gateway.iter_chat_completion", side_effect=fallback_stream),
                mock.patch("pcl_codex_bridge.gateway.log"),
            ):
                body = {
                    "model": "Kimi-K3",
                    "input": [],
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                }
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                events = self._sse_events(response.read().decode("utf-8"))
            self.assertFalse(any(event.get("type") == "response.output_text.delta" for event in events))
            done = next(
                event
                for event in events
                if event.get("type") == "response.output_item.done"
                and event.get("item", {}).get("type") == "function_call"
            )
            self.assertEqual(done["item"]["name"], "shell")
            completed = next(event for event in events if event.get("type") == "response.completed")
            self.assertEqual(completed["response"]["metadata"]["pcl_tool_fallback"], "true")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_failed_stream_never_completes_partial_tool_call(self):
        def broken_stream(_chat):
            yield {
                "kind": "tool",
                "index": 0,
                "call_id": "call_partial",
                "name_delta": "shell",
                "arguments_delta": '{"cmd":',
            }
            raise RuntimeError("upstream disconnected")

        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                mock.patch("pcl_codex_bridge.gateway.iter_chat_completion", side_effect=broken_stream),
                mock.patch("pcl_codex_bridge.gateway.log"),
            ):
                body = {
                    "model": "DeepSeek-V4-Pro",
                    "input": [],
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                }
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                events = self._sse_events(response.read().decode("utf-8"))
            self.assertTrue(any(event.get("type") == "response.failed" for event in events))
            self.assertFalse(
                any(
                    event.get("type") == "response.output_item.done"
                    and event.get("item", {}).get("type") == "function_call"
                    for event in events
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_converts_freeform_custom_tool_to_chat_function(self):
        body = {
            "model": "DeepSeek-V4-Pro",
            "input": [{"type": "message", "role": "user", "content": "edit"}],
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch",
                    "format": {"type": "grammar"},
                }
            ],
        }
        chat = build_chat_request(body)
        function = chat["tools"][0]["function"]
        self.assertEqual(function["name"], "apply_patch")
        self.assertEqual(function["parameters"]["required"], ["input"])

    def test_parses_strict_text_tool_fallback(self):
        text = '```json\n{"tool_calls":[{"name":"shell","arguments":{"cmd":"pwd"}}]}\n```'
        calls = parse_fallback_calls(text, ["shell"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "shell")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"cmd": "pwd"})

    def test_rejects_unadvertised_fallback_tool(self):
        calls = parse_fallback_calls('{"tool_calls":[{"name":"danger","arguments":{}}]}', ["safe"])
        self.assertEqual(calls, [])

    def test_gateway_status_exposes_only_allowlisted_admin_scope(self):
        with (
            mock.patch("pcl_codex_bridge.gateway.tailnet_node", return_value={
                "HostName": "haichen-pcl-linux-3070ti",
                "DNSName": "haichen-pcl-linux-3070ti.example.ts.net.",
                "TailscaleIPs": ["100.113.234.58"],
            }),
            mock.patch("pcl_codex_bridge.gateway.os.getpid", return_value=42),
        ):
            status = gateway_status()
        self.assertEqual(status["node_name"], "haichen-pcl-linux-3070ti")
        self.assertEqual(status["pid"], 42)
        self.assertEqual(status["admin_scope"], ["status", "logs", "restart_self", "portal_proxy", "topology_consensus"])
        self.assertNotIn("key_file", status)

    def test_topology_heartbeat_is_sanitized_and_shared(self):
        from pcl_codex_bridge import gateway

        with gateway.TOPOLOGY_LOCK:
            gateway.TOPOLOGY_REPORTS.clear()
        report = record_topology_heartbeat(
            {
                "node_id": "100.64.0.11",
                "node_name": "peer-mac",
                "client_version": "2.3.3",
                "pcl_direct": False,
                "relay_reachable": True,
                "round_id": 42,
                "api_key": "must-not-be-stored",
            },
            "100.64.0.11",
        )
        self.assertNotIn("api_key", report)
        snapshot = topology_snapshot()
        self.assertEqual(snapshot["service"], "pcl-relay-topology-consensus")
        self.assertEqual(snapshot["reports"][0]["node_id"], "100.64.0.11")
        self.assertEqual(snapshot["reports"][0]["round_id"], 42)
        record_topology_heartbeat(
            {
                "node_id": "100.64.0.11",
                "node_name": "peer-mac",
                "round_id": 43,
            },
            "100.64.0.11",
        )
        snapshot = topology_snapshot()
        self.assertEqual([item["round_id"] for item in snapshot["reports"]], [42, 43])

    def test_portal_proxy_only_allows_pcl_https_domains(self):
        self.assertTrue(portal_target_allowed("llmapi.pcl.ac.cn", 443))
        self.assertTrue(portal_target_allowed("login.pcl.ac.cn", 443))
        self.assertFalse(portal_target_allowed("example.com", 443))
        self.assertFalse(portal_target_allowed("pcl.ac.cn.example.com", 443))
        self.assertFalse(portal_target_allowed("llmapi.pcl.ac.cn", 80))

    def test_portal_pac_routes_only_pcl_domains(self):
        pac = portal_pac("relay.tail.test", 15722)
        self.assertIn('PROXY relay.tail.test:15722', pac)
        self.assertIn('.pcl.ac.cn', pac)
        self.assertIn('return "DIRECT"', pac)

    def test_recent_logs_redacts_key_file_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gateway.log"
            path.write_text("listening key=/home/user/.config/api-key\n", encoding="utf-8")
            with mock.patch("pcl_codex_bridge.gateway.LOG_PATH", path):
                lines = recent_logs()
        self.assertEqual(lines, ["listening key=[KEY_FILE]"])


if __name__ == "__main__":
    unittest.main()
