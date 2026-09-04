import json
import base64
import http.client
import threading
import unittest
from io import BytesIO
from http.server import ThreadingHTTPServer
from unittest import mock

from pcl_codex_bridge import native_router


class NativeRouterTests(unittest.TestCase):
    def test_sse_relay_flushes_each_line_without_sized_read_buffering(self):
        class FakeResponse:
            headers = {"Content-Type": "text/event-stream; charset=utf-8"}

            def __init__(self):
                self.lines = iter(
                    [
                        b"event: response.reasoning_summary_text.delta\n",
                        b'data: {"delta":"think"}\n',
                        b"\n",
                    ]
                )

            def readline(self):
                return next(self.lines, b"")

            def read(self, _size=-1):
                raise AssertionError("SSE relay must not use sized read()")

        class FlushRecorder(BytesIO):
            def __init__(self):
                super().__init__()
                self.flush_snapshots = []

            def flush(self):
                self.flush_snapshots.append(self.getvalue())

        output = FlushRecorder()
        native_router.relay_upstream_body(FakeResponse(), output)
        self.assertEqual(len(output.flush_snapshots), 3)
        self.assertEqual(
            output.flush_snapshots[0],
            b"event: response.reasoning_summary_text.delta\n",
        )
        self.assertIn(b'"delta":"think"', output.getvalue())

    def test_topology_heartbeat_reports_endpoint_measurements(self):
        managed = "# >>> pcl-codex-bridge managed block >>>\n# >>> pcl-relay native router root >>>\n[features.multi_agent_v2]\n"
        with (
            mock.patch.object(native_router, "load_registry", return_value={"topology_coordinator": "http://relay.tail:15722/v1"}),
            mock.patch.object(native_router, "selected_gateway", return_value="http://relay.tail:15722/v1"),
            mock.patch.object(native_router, "_tailnet_identity", return_value=("100.64.0.11", "peer-mac")),
            mock.patch.object(native_router, "_probe_health", return_value=(True, 21)),
            mock.patch.object(native_router, "_probe_pcl_direct", return_value=(False, 34)),
            mock.patch("builtins.open", mock.mock_open(read_data=managed)),
            mock.patch.object(native_router.os.path, "isdir", return_value=True),
            mock.patch.object(native_router.os, "listdir", return_value=["pcl-deepseek-pro.toml"]),
            mock.patch.object(native_router.time, "time", return_value=1260.0),
        ):
            payload = native_router.topology_heartbeat_payload()
        self.assertEqual(payload["node_id"], "100.64.0.11")
        self.assertTrue(payload["relay_reachable"])
        self.assertTrue(payload["configured_gateway_reachable"])
        self.assertTrue(payload["coordinator_reachable"])
        self.assertFalse(payload["pcl_direct"])
        self.assertTrue(payload["client_ready"])
        self.assertEqual(payload["round_id"], 42)

    def test_local_adapter_health_does_not_imply_coordinator_reachability(self):
        managed = "# >>> pcl-codex-bridge managed block >>>\n# >>> pcl-relay native router root >>>\n[features.multi_agent_v2]\n"

        def health(url):
            return (url.startswith("http://127.0.0.1:"), 8 if "127.0.0.1" in url else 6000)

        with (
            mock.patch.object(
                native_router,
                "load_registry",
                return_value={"topology_coordinator": "http://relay.tail:15722/v1"},
            ),
            mock.patch.object(native_router, "selected_gateway", return_value="http://127.0.0.1:15722/v1"),
            mock.patch.object(native_router, "_tailnet_identity", return_value=("100.64.0.12", "pcl-pod")),
            mock.patch.object(native_router, "_probe_health", side_effect=health),
            mock.patch.object(native_router, "_probe_pcl_direct", return_value=(True, 12)),
            mock.patch("builtins.open", mock.mock_open(read_data=managed)),
            mock.patch.object(native_router.os.path, "isdir", return_value=True),
            mock.patch.object(native_router.os, "listdir", return_value=["pcl-deepseek-pro.toml"]),
        ):
            payload = native_router.topology_heartbeat_payload()
        self.assertTrue(payload["configured_gateway_reachable"])
        self.assertFalse(payload["coordinator_reachable"])
        self.assertFalse(payload["relay_reachable"])
        self.assertTrue(payload["client_ready"])

    def test_routes_prefixed_models_to_pcl(self):
        route, model = native_router.route_request({"model": "pcl/DeepSeek-V4-Pro"})
        self.assertEqual((route, model), ("pcl", "DeepSeek-V4-Pro"))

    def test_routes_official_models_without_rewriting_identity(self):
        route, model = native_router.route_request({"model": "gpt-5.6-sol"})
        self.assertEqual((route, model), ("openai", "gpt-5.6-sol"))

    def test_pcl_rewrite_strips_only_router_prefix_and_official_tier(self):
        body = native_router.rewrite_pcl_body(
            {"model": "pcl/DeepSeek-V4-Pro", "input": "hello", "service_tier": "priority"},
            "DeepSeek-V4-Pro",
        )
        payload = json.loads(body)
        self.assertEqual(payload["model"], "DeepSeek-V4-Pro")
        self.assertEqual(payload["input"], "hello")
        self.assertNotIn("service_tier", payload)

    def test_agents_v2_messages_are_plaintext_without_touching_reserved_or_private_fields(self):
        payload = {
            "tools": [
                {
                    "type": "namespace",
                    "name": "agents",
                    "tools": [
                        {
                            "type": "function",
                            "name": "spawn_agent",
                            "parameters": {
                                "properties": {
                                    "message": {"type": "string", "encrypted": True},
                                    "private_note": {"type": "string", "encrypted": True},
                                }
                            },
                        },
                        {
                            "type": "function",
                            "name": "unknown_tool",
                            "parameters": {"properties": {"message": {"encrypted": True}}},
                        },
                    ],
                },
                {
                    "type": "function",
                    "namespace": "collaboration",
                    "name": "spawn_agent",
                    "parameters": {"properties": {"message": {"encrypted": True}}},
                },
            ],
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [
                        {
                            "type": "function",
                            "namespace": "agents",
                            "name": "followup_task",
                            "parameters": {"properties": {"message": {"encrypted": True}}},
                        }
                    ],
                },
                {"type": "reasoning", "encrypted_content": "opaque"},
            ],
        }
        self.assertEqual(native_router.make_v2_agent_messages_plaintext(payload), 2)
        self.assertNotIn("encrypted", payload["tools"][0]["tools"][0]["parameters"]["properties"]["message"])
        self.assertTrue(payload["tools"][0]["tools"][0]["parameters"]["properties"]["private_note"]["encrypted"])
        self.assertTrue(payload["tools"][0]["tools"][1]["parameters"]["properties"]["message"]["encrypted"])
        self.assertTrue(payload["tools"][1]["parameters"]["properties"]["message"]["encrypted"])
        self.assertNotIn("encrypted", payload["input"][0]["tools"][0]["parameters"]["properties"]["message"])
        self.assertEqual(payload["input"][1]["encrypted_content"], "opaque")

    def test_decodes_codex_zstd_request_body(self):
        compressed = base64.b64decode(
            "KLUv/QRYQQEAeyJtb2RlbCI6ImdwdC01LjYtbHVuYSIsImlucHV0IjoiaGVsbG8ifYWauCI="
        )
        payload, raw = native_router.decode_request_body(compressed, "zstd")
        self.assertEqual(payload["model"], "gpt-5.6-luna")
        self.assertEqual(json.loads(raw)["input"], "hello")

    def test_official_headers_are_allowlisted_and_pcl_gets_no_credentials(self):
        inbound = [
            ("Authorization", "Bearer official"),
            ("ChatGPT-Account-Id", "acct"),
            ("Cookie", "secret"),
            ("X-Unknown", "nope"),
        ]
        official = native_router.outbound_headers("openai", inbound, 10)
        pcl = native_router.outbound_headers("pcl", inbound, 10)
        self.assertEqual(official["Authorization"], "Bearer official")
        self.assertEqual(official["ChatGPT-Account-Id"], "acct")
        self.assertNotIn("Cookie", official)
        self.assertNotIn("Authorization", pcl)

    def test_upstream_urls_keep_two_trust_domains_separate(self):
        with mock.patch.object(native_router, "selected_gateway", return_value="http://relay.tail:15722/v1"):
            self.assertEqual(
                native_router.upstream_url("pcl", "/v1/responses"),
                "http://relay.tail:15722/v1/responses",
            )
            self.assertEqual(
                native_router.upstream_url("pcl", "/v1/responses/compact"),
                "http://relay.tail:15722/v1/responses/compact",
            )
        self.assertEqual(
            native_router.upstream_url("openai", "/v1/responses"),
            native_router.OPENAI_CODEX_BASE_URL + "/responses",
        )

    def test_alpha_search_always_uses_official_route_and_preserves_query(self):
        route, model = native_router.route_for_path(
            {"model": "pcl/DeepSeek-V4-Pro", "query": "Codex docs"},
            "/v1/alpha/search?source=codex",
        )
        self.assertEqual(route, "openai")
        self.assertEqual(model, "pcl/DeepSeek-V4-Pro")
        self.assertEqual(
            native_router.upstream_url(route, "/v1/alpha/search?source=codex"),
            native_router.OPENAI_CODEX_BASE_URL + "/alpha/search?source=codex",
        )

    def test_pcl_cannot_receive_hosted_search(self):
        with self.assertRaisesRegex(ValueError, "do not support /alpha/search"):
            native_router.upstream_url("pcl", "/v1/alpha/search")

    def test_websocket_probe_gets_clean_http_fallback_signal(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), native_router.NativeRouterHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request(
                "GET",
                "/v1/responses",
                headers={"Upgrade": "websocket", "Connection": "Upgrade"},
            )
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 426)
            self.assertEqual(body["error"]["code"], "responses_websocket_not_supported")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
