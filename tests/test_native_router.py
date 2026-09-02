import json
import base64
import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from pcl_codex_bridge import native_router


class NativeRouterTests(unittest.TestCase):
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
