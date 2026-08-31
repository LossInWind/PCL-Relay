import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.client_config import discover_relays, managed_block, select_relay


class RelayDiscoveryTests(unittest.TestCase):
    def test_discovers_gateway_and_validates_upstream_credentials(self):
        tailscale = {
            "Self": {"HostName": "mac", "DNSName": "mac.tail.test.", "TailscaleIPs": ["100.64.0.1"]},
            "Peer": {
                "relay": {
                    "HostName": "relay",
                    "DNSName": "relay.tail.test.",
                    "TailscaleIPs": ["100.64.0.2"],
                    "Online": True,
                }
            },
        }

        def request(url, timeout=0):
            if "relay.tail.test" in url and url.endswith("/healthz"):
                return {"status": "ok", "service": "pcl-codex-gateway", "upstream": "https://llmapi.pcl.ac.cn/v1"}
            if "relay.tail.test" in url and url.endswith("/models"):
                return {"data": [{"id": "GLM-5.2"}]}
            raise urllib.error.URLError("not a relay")

        with (
            mock.patch("pcl_codex_bridge.client_config._tailscale_status", return_value=tailscale),
            mock.patch("pcl_codex_bridge.client_config.request_json", side_effect=request),
            mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={"gateway": "http://relay.tail.test:15722/v1"}),
            mock.patch("pcl_codex_bridge.client_config.save_registry"),
        ):
            result = discover_relays(timeout=0.1)
        relay = next(item for item in result["nodes"] if item["node_name"] == "relay")
        self.assertTrue(relay["gateway"])
        self.assertEqual(relay["pcl_auth"], "valid")
        self.assertEqual(relay["model_count"], 1)
        self.assertTrue(relay["selected"])
        self.assertEqual(result["ready_count"], 1)

    def test_select_relay_updates_only_managed_codex_block(self):
        registry = {"gateway": "http://old.tail.test:15722/v1", "models": {}}
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text('model = "gpt-5.6-sol"\nmodel_provider = "openai"\n', encoding="utf-8")
            with (
                mock.patch.dict("os.environ", {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value=registry),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
                mock.patch("pcl_codex_bridge.client_config.request_json", side_effect=[
                    {"status": "ok", "service": "pcl-codex-gateway"},
                    {"data": [{"id": "GLM-5.2"}]},
                ]),
            ):
                result = select_relay("http://new.tail.test:15722/v1")
            updated = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5.6-sol"', updated)
        self.assertIn('model_provider = "openai"', updated)
        self.assertIn('base_url = "http://new.tail.test:15722/v1"', updated)
        self.assertTrue(result["main_provider_preserved"])

    def test_no_proxy_tracks_selected_gateway(self):
        block = managed_block("http://another.tail.test:15722/v1", "/usr/bin/python3")
        self.assertIn("another.tail.test", block)
        self.assertNotIn("haichen-pcl-linux-3070ti", block)


if __name__ == "__main__":
    unittest.main()
