import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.gateway import (
    build_chat_request,
    gateway_status,
    parse_fallback_calls,
    recent_logs,
    responses_messages,
)


class GatewayMappingTests(unittest.TestCase):
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
        self.assertEqual(status["admin_scope"], ["status", "logs", "restart_self"])
        self.assertNotIn("key_file", status)

    def test_recent_logs_redacts_key_file_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gateway.log"
            path.write_text("listening key=/home/user/.config/api-key\n", encoding="utf-8")
            with mock.patch("pcl_codex_bridge.gateway.LOG_PATH", path):
                lines = recent_logs()
        self.assertEqual(lines, ["listening key=[KEY_FILE]"])


if __name__ == "__main__":
    unittest.main()
