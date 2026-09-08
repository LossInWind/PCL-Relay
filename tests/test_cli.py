import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.cli import (
    activate_opencodex_sidecar,
    integration_status,
    prepare_opencodex_sidecar,
    portal_status,
    select_models,
    stage_opencodex_sidecar,
)
from pcl_codex_bridge import cli as cli_module


class CliTests(unittest.TestCase):
    def test_legacy_router_restart_entrypoint_is_unadvertised_but_still_dispatches(self):
        with (
            mock.patch.object(sys, "argv", ["pcl-codex", "native-router", "--port", "15724"]),
            mock.patch("pcl_codex_bridge.cli.serve_native_router") as serve,
            mock.patch("pcl_codex_bridge.cli.parser") as public_parser,
        ):
            cli_module.main()
        public_parser.assert_not_called()
        self.assertEqual(serve.call_args.args[0].port, 15724)

    def test_sidecar_stage_does_not_start_or_activate_service(self):
        runtime = mock.MagicMock(root=Path("/runtime"), version="2.46.0", commit="abc")
        with (
            mock.patch("pcl_codex_bridge.cli.install_source_tree") as install,
            mock.patch("pcl_codex_bridge.cli.installed_runtime", return_value=runtime),
            mock.patch("pcl_codex_bridge.cli.prepare_sidecar") as prepare,
            mock.patch("pcl_codex_bridge.cli.activate_sidecar") as activate,
        ):
            result = stage_opencodex_sidecar(mock.MagicMock())
        install.assert_called_once_with()
        prepare.assert_not_called()
        activate.assert_not_called()
        self.assertFalse(result["service_restarted"])
        self.assertFalse(result["codex_config_changed"])

    def test_sidecar_prepare_uses_selected_models_without_persisting_network_policy(self):
        runtime = mock.MagicMock()
        args = mock.MagicMock(
            gateway_url="http://100.113.234.58:15722/v1",
            port=15725,
        )
        registry = {
            "official_proxy": "http://127.0.0.1:7890",
            "selected_agents": ["pcl_glm"],
            "agent_definitions": {
                "pcl_glm": {"model": "GLM-5.2", "description": "GLM"}
            },
        }
        with (
            mock.patch("pcl_codex_bridge.cli.install_source_tree"),
            mock.patch("pcl_codex_bridge.cli.installed_runtime", return_value=runtime),
            mock.patch("pcl_codex_bridge.cli.load_registry", return_value=registry),
            mock.patch(
                "pcl_codex_bridge.cli.prepare_sidecar",
                return_value={"prepared": True, "codex_integration_enabled": False},
            ) as prepare,
            mock.patch("pcl_codex_bridge.cli.save_registry") as save,
        ):
            result = prepare_opencodex_sidecar(args)
        self.assertEqual(prepare.call_args.args[2], ["GLM-5.2"])
        self.assertNotIn("official_proxy", prepare.call_args.kwargs)
        self.assertFalse(result["codex_integration_enabled"])
        self.assertEqual(result["official_network_owner"], "upstream-codex-environment")
        self.assertNotIn("official_proxy", save.call_args.args[0])
        self.assertEqual(save.call_args.args[0]["opencodex_port"], 15725)

    def test_integration_status_is_open_codex_native_state(self):
        runtime = mock.MagicMock(root=Path("/runtime"), version="2.46.0", commit="abc")
        with (
            mock.patch("pcl_codex_bridge.cli.installed_runtime", return_value=runtime),
            mock.patch(
                "pcl_codex_bridge.cli.sidecar_health",
                return_value={"ok": True, "pid": 42, "port": 15725},
            ),
            mock.patch(
                "pcl_codex_bridge.cli.sidecar_integration_status",
                return_value={
                    "ok": True,
                    "codex": {
                        "clientId": "codex",
                        "state": "current",
                        "desiredEnabled": True,
                    },
                },
            ),
        ):
            status = integration_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["active"])
        self.assertEqual(status["transport_implementation"], "upstream-opencodex")

    def test_activation_failure_restores_upstream_and_legacy_config(self):
        runtime = mock.MagicMock()
        args = mock.MagicMock(port=15725)
        handoff = {"changed": True, "backup": "/tmp/config.backup"}
        with (
            mock.patch("pcl_codex_bridge.cli.installed_runtime", return_value=runtime),
            mock.patch(
                "pcl_codex_bridge.cli.prepare_legacy_opencodex_handoff",
                return_value=handoff,
            ),
            mock.patch(
                "pcl_codex_bridge.cli.activate_sidecar",
                side_effect=RuntimeError("injection refused"),
            ),
            mock.patch(
                "pcl_codex_bridge.cli.deactivate_sidecar",
                return_value={"active": False},
            ) as deactivate,
            mock.patch(
                "pcl_codex_bridge.cli.restore_legacy_opencodex_handoff",
                return_value={"restored": True},
            ) as restore,
        ):
            with self.assertRaisesRegex(RuntimeError, "pre-handoff Codex config was restored"):
                activate_opencodex_sidecar(args)
        deactivate.assert_called_once_with(runtime)
        restore.assert_called_once_with(handoff)

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
