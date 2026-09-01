import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.client_config import (
    BEGIN,
    END,
    INSTALL_ROOT,
    find_tailscale,
    install_client_config,
    install_source_tree,
    managed_block,
    strip_managed_block,
    uninstall_client_config,
)
from pcl_codex_bridge.models import AGENTS, model_catalog


class ClientConfigTests(unittest.TestCase):
    def test_finds_tailscale_outside_gui_app_path(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Tailscale"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with (
                mock.patch.dict(os.environ, {"PCL_TAILSCALE_BIN": ""}, clear=False),
                mock.patch("pcl_codex_bridge.client_config.shutil.which", return_value=None),
                mock.patch("pcl_codex_bridge.client_config.TAILSCALE_CANDIDATES", (executable,)),
            ):
                self.assertEqual(find_tailscale(), str(executable))

    def test_tailscale_override_has_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "tailscale-custom"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with (
                mock.patch.dict(os.environ, {"PCL_TAILSCALE_BIN": str(executable)}),
                mock.patch("pcl_codex_bridge.client_config.shutil.which", return_value="/bin/false"),
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
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                result = install_client_config("http://tailnet:15722/v1")
            updated = config.read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-sol"', updated)
            self.assertIn('model_provider = "openai"', updated)
            self.assertIn(BEGIN, updated)
            self.assertIn('[model_providers.pcl_internal]', updated)
            self.assertIn('default_tools_approval_mode = "approve"', updated)
            self.assertTrue(Path(result["profile"]).exists())

    def test_install_is_idempotent_and_uninstall_removes_only_managed_block(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                install_client_config()
                install_client_config()
                self.assertEqual(config.read_text(encoding="utf-8").count(BEGIN), 1)
                uninstall_client_config()
            remaining = config.read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-sol"', remaining)
            self.assertNotIn(BEGIN, remaining)

    def test_catalog_contains_all_fixed_agents(self):
        slugs = {item["slug"] for item in model_catalog()["models"]}
        self.assertEqual(slugs, {info["model"] for info in AGENTS.values()})

    def test_standalone_mcp_block_uses_bundled_cli(self):
        block = managed_block("http://tailnet:15722/v1", "/Users/test/.local/bin/pcl-codex", True)
        self.assertIn('command = "/Users/test/.local/bin/pcl-codex"', block)
        self.assertIn('args = ["mcp-server"]', block)
        self.assertNotIn("PYTHONPATH", block)

    def test_source_mcp_block_uses_python_module(self):
        block = managed_block("http://tailnet:15722/v1", "/usr/bin/python3", False)
        self.assertIn('args = ["-m", "pcl_codex_bridge.mcp_server"]', block)
        self.assertIn("PYTHONPATH", block)


if __name__ == "__main__":
    unittest.main()
