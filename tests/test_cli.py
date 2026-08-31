import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.cli import select_models


class CliTests(unittest.TestCase):
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
                mock.patch("pcl_codex_bridge.cli.codex_home", return_value=Path(temp)),
            ):
                result = select_models(["Qwen3.6-35B"])
                catalog = json.loads((Path(temp) / "pcl-models.json").read_text())
        self.assertEqual(result["selected_agents"], ["pcl_qwen3_6_35b"])
        self.assertEqual(catalog["models"][0]["slug"], "Qwen3.6-35B")
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
