import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.cli import portal_status, select_models


class CliTests(unittest.TestCase):
    def test_portal_status_uses_selected_gateway_as_https_proxy(self):
        completed = mock.MagicMock(returncode=0, stdout="200\ntext/html; charset=utf-8\n0.082", stderr="")
        with mock.patch("pcl_codex_bridge.cli.subprocess.run", return_value=completed) as run:
            result = portal_status("http://relay.tail.test:15722/v1")
        self.assertTrue(result["available"])
        self.assertEqual(result["proxy_url"], "http://relay.tail.test:15722")
        self.assertEqual(result["pac_url"], "http://relay.tail.test:15722/admin/portal.pac")
        self.assertIn("http://relay.tail.test:15722", run.call_args.args[0])
        self.assertIn("--noproxy", run.call_args.args[0])

    def test_select_accepts_discovered_model_id_and_writes_catalog(self):
        registry = {
            "available_models": {
                "Qwen3.6-35B": {
                    "id": "Qwen3.6-35B",
                    "alias": "pcl_qwen3_6_35b",
                    "agent_eligible": True,
                    "description": "Qwen agent",
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch("pcl_codex_bridge.cli.load_registry", return_value=registry),
                mock.patch("pcl_codex_bridge.cli.save_registry") as save,
                mock.patch("pcl_codex_bridge.client_config.codex_home", return_value=Path(temp)),
            ):
                result = select_models(["Qwen3.6-35B"])
                catalog = json.loads((Path(temp) / "pcl-native-models.json").read_text())
        self.assertEqual(result["selected_agents"], ["pcl_qwen3_6_35b"])
        self.assertEqual(catalog["models"][0]["slug"], "pcl/Qwen3.6-35B")
        self.assertEqual(catalog["models"][0]["multi_agent_version"], "v2")
        self.assertEqual(save.call_args.args[0]["agent_definitions"]["pcl_qwen3_6_35b"]["model"], "Qwen3.6-35B")

    def test_select_rejects_non_agent_model(self):
        registry = {
            "available_models": {
                "bge-m3": {"id": "bge-m3", "alias": "pcl_bge_m3", "agent_eligible": False}
            }
        }
        with mock.patch("pcl_codex_bridge.cli.load_registry", return_value=registry):
            with self.assertRaisesRegex(RuntimeError, "cannot be used"):
                select_models(["bge-m3"])


if __name__ == "__main__":
    unittest.main()
