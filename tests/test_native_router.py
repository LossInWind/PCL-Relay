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
            native_router.upstream_url("openai", "/v1/responses"),
            native_router.OPENAI_CODEX_BASE_URL + "/responses",
        )

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
