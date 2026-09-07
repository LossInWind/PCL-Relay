import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import official_network


class OfficialNetworkTests(unittest.TestCase):
    def test_secure_haichen_services_state_is_authoritative(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "official-proxy.json"
            state.write_text(
                json.dumps(
                    {
                        "provider": "haichen-services",
                        "available": True,
                        "official_proxy_url": "http://127.0.0.1:12450",
                    }
                ),
                encoding="utf-8",
            )
            state.chmod(0o600)
            with (
                mock.patch.object(official_network, "HAICHEN_SERVICES_PROXY_STATE", state),
                mock.patch.dict(
                    os.environ,
                    {"PCL_RELAY_OFFICIAL_PROXY": "http://127.0.0.1:7890"},
                    clear=False,
                ),
            ):
                self.assertEqual(
                    official_network.resolve_official_proxy(),
                    ("http://127.0.0.1:12450", "haichen-services"),
                )

    def test_explicit_unavailable_state_does_not_fall_back_to_stale_proxy(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "official-proxy.json"
            state.write_text(
                json.dumps({"provider": "haichen-services", "available": False}),
                encoding="utf-8",
            )
            state.chmod(0o600)
            with (
                mock.patch.object(official_network, "HAICHEN_SERVICES_PROXY_STATE", state),
                mock.patch.dict(
                    os.environ,
                    {"PCL_RELAY_OFFICIAL_PROXY": "http://127.0.0.1:7890"},
                    clear=False,
                ),
            ):
                self.assertEqual(
                    official_network.resolve_official_proxy(),
                    ("", "haichen-services"),
                )

    def test_insecure_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "official-proxy.json"
            state.write_text(
                json.dumps(
                    {
                        "provider": "haichen-services",
                        "available": True,
                        "official_proxy_url": "http://127.0.0.1:12450",
                    }
                ),
                encoding="utf-8",
            )
            state.chmod(0o666)
            with (
                mock.patch.object(official_network, "HAICHEN_SERVICES_PROXY_STATE", state),
                mock.patch.dict(
                    os.environ,
                    {
                        "PCL_RELAY_OFFICIAL_PROXY": "http://127.0.0.1:17731",
                        "HTTPS_PROXY": "",
                        "https_proxy": "",
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(
                    official_network.resolve_official_proxy(),
                    ("http://127.0.0.1:17731", "PCL_RELAY_OFFICIAL_PROXY"),
                )


if __name__ == "__main__":
    unittest.main()
