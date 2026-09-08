import os
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import official_network


class OfficialNetworkTests(unittest.TestCase):
    def test_explicit_relay_environment_is_used_without_app_state(self):
        with mock.patch.dict(
            os.environ,
            {
                "PCL_RELAY_OFFICIAL_PROXY": "http://127.0.0.1:17731",
                "HTTPS_PROXY": "http://127.0.0.1:17732",
            },
            clear=True,
        ):
            self.assertEqual(
                official_network.resolve_official_proxy(),
                ("http://127.0.0.1:17731", "PCL_RELAY_OFFICIAL_PROXY"),
            )

    def test_standard_process_environment_is_supported(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:17732"}, clear=True):
            self.assertEqual(
                official_network.resolve_official_proxy(),
                ("http://127.0.0.1:17732", "HTTPS_PROXY"),
            )

    def test_saved_explicit_value_is_last_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                official_network.resolve_official_proxy("http://127.0.0.1:17733"),
                ("http://127.0.0.1:17733", "registry"),
            )

    def test_no_other_application_contract_is_read(self):
        source = Path(official_network.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Haichen Services", source)
        self.assertNotIn("official-proxy.json", source)
        self.assertNotIn("Application Support", source)


if __name__ == "__main__":
    unittest.main()
