import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.client_config import (
    BEGIN,
    ROOT_BEGIN,
    ROOT_END,
    _make_tree_owner_writable,
    choose_native_router_port,
    combined_catalog,
    configured_native_router_port,
    install_client_config,
    install_source_tree,
    managed_block,
    uninstall_client_config,
)
from pcl_codex_bridge.models import AGENTS, model_catalog
from pcl_codex_bridge.relay_discovery import find_tailscale


class ClientConfigTests(unittest.TestCase):
    def test_configured_router_port_prefers_codex_source_of_truth(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            (home / "config.toml").write_text(
                f'{ROOT_BEGIN}\nopenai_base_url = "http://127.0.0.1:15725/v1"\n{ROOT_END}\n',
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config._managed_router_service_port", return_value=15724),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={"native_router_port": 15724}),
            ):
                self.assertEqual(configured_native_router_port(), 15725)

    def test_choose_router_port_does_not_drift_during_managed_restart(self):
        with (
            mock.patch("pcl_codex_bridge.client_config.configured_native_router_port", return_value=15724),
            mock.patch("pcl_codex_bridge.client_config._managed_router_service_port", return_value=15724),
            mock.patch("pcl_codex_bridge.client_config.native_router_health", return_value={"reachable": False}),
            mock.patch("pcl_codex_bridge.client_config._port_is_bindable", return_value=False),
        ):
            self.assertEqual(choose_native_router_port(), 15724)

    def test_config_install_reports_real_port_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text(
                f'{ROOT_BEGIN}\nopenai_base_url = "http://127.0.0.1:15724/v1"\n{ROOT_END}\n\nmodel = "gpt-5.6-sol"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
            ):
                result = install_client_config(router_port=15725)
            self.assertTrue(result["router_port_changed"])
            self.assertTrue(result["codex_reload_required"])
            self.assertEqual(result["previous_router_port"], 15724)
            self.assertIn("127.0.0.1:15725/v1", config.read_text(encoding="utf-8"))

    def test_signed_bundle_copy_is_made_writable_before_reinstall(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pcl_codex_bridge"
            root.mkdir()
            source = root / "native_router.py"
            source.write_text("old\n", encoding="utf-8")
            source.chmod(0o444)
            root.chmod(0o555)
            _make_tree_owner_writable(root)
            source.write_text("new\n", encoding="utf-8")
            self.assertEqual(source.read_text(encoding="utf-8"), "new\n")

    def test_finds_tailscale_outside_gui_app_path(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Tailscale"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with (
                mock.patch.dict(os.environ, {"PCL_TAILSCALE_BIN": ""}, clear=False),
                mock.patch("pcl_codex_bridge.relay_discovery.shutil.which", return_value=None),
                mock.patch("pcl_codex_bridge.relay_discovery.TAILSCALE_CANDIDATES", (executable,)),
            ):
                self.assertEqual(find_tailscale(), str(executable))

    def test_tailscale_override_has_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "tailscale-custom"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with (
                mock.patch.dict(os.environ, {"PCL_TAILSCALE_BIN": str(executable)}),
                mock.patch("pcl_codex_bridge.relay_discovery.shutil.which", return_value="/bin/false"),
            ):
                self.assertEqual(find_tailscale(), str(executable))

    def test_installed_source_tree_can_reinstall_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "pcl_codex_bridge").mkdir()
            with (
                mock.patch("pcl_codex_bridge.client_config.INSTALL_ROOT", source),
                mock.patch("pcl_codex_bridge.client_config.BIN_PATH", source / "bin" / "pcl-codex"),
                mock.patch("pcl_codex_bridge.client_config.shutil.copytree") as copytree,
            ):
                install_source_tree(source)
            copytree.assert_not_called()

    def test_install_preserves_official_provider_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            original = 'model = "gpt-5.6-sol"\nmodel_provider = "openai"\n\n[features]\nmulti_agent = true\n'
            config.write_text(original, encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
            ):
                result = install_client_config("http://tailnet:15722/v1")
            updated = config.read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-sol"', updated)
            self.assertIn('model_provider = "openai"', updated)
            self.assertIn(BEGIN, updated)
            self.assertIn(ROOT_BEGIN, updated)
            self.assertIn('openai_base_url = "http://127.0.0.1:15724/v1"', updated)
            self.assertIn('[mcp_servers.pcl_relay]', updated)
            self.assertIn('[agents]', updated)
            self.assertIn('default_subagent_model = "pcl/DeepSeek-V4-Pro"', updated)
            self.assertIn('[features.multi_agent_v2]', updated)
            self.assertIn('hide_spawn_agent_metadata = true', updated)
            self.assertIn('tool_namespace = "agents"', updated)
            self.assertNotIn('[model_providers.pcl_internal]', updated)
            self.assertNotIn('[mcp_servers.pcl_agents]', updated)
            self.assertIn('default_tools_approval_mode = "approve"', updated)
            self.assertTrue(Path(result["catalog"]).exists())
            self.assertEqual(result["delegation"], "native_spawn_agent")

    def test_install_is_idempotent_and_uninstall_removes_only_managed_block(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
            ):
                install_client_config()
                install_client_config()
                self.assertEqual(config.read_text(encoding="utf-8").count(BEGIN), 1)
                self.assertEqual(config.read_text(encoding="utf-8").count(ROOT_BEGIN), 1)
                uninstall_client_config()
            remaining = config.read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-sol"', remaining)
            self.assertNotIn(BEGIN, remaining)
            self.assertNotIn(ROOT_BEGIN, remaining)

    def test_catalog_contains_all_fixed_agents(self):
        slugs = {item["slug"] for item in model_catalog()["models"]}
        self.assertEqual(slugs, {info["model"] for info in AGENTS.values()})

    def test_combined_catalog_routes_pcl_v2_and_preserves_native_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                models = combined_catalog()["models"]
        pcl = [item for item in models if item["slug"].startswith("pcl/")]
        official = [item for item in models if item["slug"].startswith("gpt-")]
        self.assertEqual({item["slug"] for item in pcl}, {"pcl/" + info["model"] for info in AGENTS.values()})
        self.assertTrue(official)
        self.assertTrue(all(item["multi_agent_version"] == "v2" for item in pcl))
        self.assertEqual(official[0]["multi_agent_version"], "v2")

    def test_install_writes_native_custom_roles_without_overwriting_user_role(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            roles = home / "agents"
            roles.mkdir(parents=True)
            user_role = roles / "pcl-deepseek-pro.toml"
            user_role.write_text('name = "pcl-deepseek-pro"\ndescription = "user owned"\n', encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
            ):
                result = install_client_config()
            self.assertIn('description = "user owned"', user_role.read_text(encoding="utf-8"))
            generated = {item["name"]: Path(item["path"]) for item in result["native_roles"]}
            self.assertIn("pcl-relay-deepseek-pro", generated)
            self.assertIn('model = "pcl/DeepSeek-V4-Pro"', generated["pcl-relay-deepseek-pro"].read_text(encoding="utf-8"))
            self.assertIn("never fall back to pcl_delegate", generated["pcl-relay-deepseek-pro"].read_text(encoding="utf-8"))

    def test_standalone_mcp_block_uses_bundled_cli(self):
        block = managed_block("http://tailnet:15722/v1", "/Users/test/.local/bin/pcl-codex", True)
        self.assertIn('command = "/Users/test/.local/bin/pcl-codex"', block)
        self.assertIn('args = ["mcp-server"]', block)
        self.assertNotIn("PYTHONPATH", block)

    def test_source_mcp_block_uses_python_module(self):
        block = managed_block("http://tailnet:15722/v1", "/usr/bin/python3", False)
        self.assertIn('args = ["-m", "pcl_codex_bridge.mcp_server"]', block)
        self.assertIn("PYTHONPATH", block)

    def test_existing_v2_table_is_preserved_without_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text(
                '[features.multi_agent_v2]\nenabled = true\nhide_spawn_agent_metadata = true\ntool_namespace = "agents"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}),
                mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
                mock.patch("pcl_codex_bridge.client_config.save_registry"),
            ):
                install_client_config()
            updated = config.read_text(encoding="utf-8")
            self.assertEqual(updated.count("[features.multi_agent_v2]"), 1)


if __name__ == "__main__":
    unittest.main()
