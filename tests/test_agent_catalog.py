import json
import tempfile
import unittest
from pathlib import Path

from pcl_codex_bridge.agent_catalog import sync_catalog_roles, maintain_catalog_roles, MARKER


class AgentCatalogTests(unittest.TestCase):
    def test_background_follows_catalog_only_when_integration_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.json"
            config.write_text('{"clientIntegrations":{"codex":false}}')
            catalog = home / "opencodex-catalog.json"
            catalog.write_text('{"models":[{"slug":"gpt-6-sol","visibility":"list"}]}')
            self.assertEqual(maintain_catalog_roles(home, home)["status"], "disabled")
            self.assertFalse((home / "agents").exists())
            config.write_text('{"clientIntegrations":{"codex":true}}')
            self.assertEqual(maintain_catalog_roles(home, home)["status"], "success")
            self.assertEqual(maintain_catalog_roles(home, home)["status"], "unchanged")
            catalog.write_text('{"models":[{"slug":"gpt-next","visibility":"list"}]}')
            self.assertEqual(maintain_catalog_roles(home, home)["roles"][0]["model"], "gpt-next")

    def test_all_models_and_efforts_without_five_model_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            models = [{"slug": f"gpt-test-{i}", "visibility": "list",
                       "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}
                      for i in range(8)]
            models += [{"slug": "hidden", "visibility": "hide"},
                       {"slug": "disabled", "visibility": "list", "multi_agent_version": "disabled"}]
            (home / "opencodex-catalog.json").write_text(json.dumps({"models": models}))
            result = sync_catalog_roles(home)
            self.assertEqual(len(result["roles"]), 8)
            for role in result["roles"]:
                content = (home / "agents" / (role["name"] + ".toml")).read_text()
                self.assertNotIn("model_reasoning_effort =", content)
                self.assertNotIn("sandbox_mode =", content)
                self.assertIn("low, high", content)
            self.assertFalse(sync_catalog_roles(home)["reload_required"])

    def test_user_owned_roles_and_malformed_catalog_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "agents").mkdir()
            user = home / "agents/relay-gpt-6-sol.toml"
            user.write_text("user owned")
            catalog = home / "opencodex-catalog.json"
            catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-sol", "visibility": "list"}]}))
            result = sync_catalog_roles(home)
            self.assertEqual(user.read_text(), "user owned")
            self.assertNotEqual(result["roles"][0]["name"], "relay-gpt-6-sol")
            before = {p.name: p.read_text() for p in (home / "agents").iterdir()}
            catalog.write_text("broken")
            self.assertEqual(sync_catalog_roles(home)["status"], "unavailable")
            self.assertEqual(before, {p.name: p.read_text() for p in (home / "agents").iterdir()})

    def test_stale_managed_roles_removed_only_after_valid_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "agents").mkdir()
            stale = home / "agents/stale.toml"
            stale.write_text(MARKER + "\n")
            (home / "opencodex-catalog.json").write_text(json.dumps({"models": [{"slug": "pcl/Kimi-K3", "visibility": "list"}]}))
            result = sync_catalog_roles(home)
            self.assertEqual(result["roles"][0]["name"], "pcl-kimi")
            self.assertFalse(stale.exists())
