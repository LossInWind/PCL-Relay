import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.client_config import (
    BEGIN,
    END,
    ROOT_BEGIN,
    ROOT_END,
    _make_tree_owner_writable,
    choose_native_router_port,
    combined_catalog,
    configured_native_router_port,
    detect_official_proxy,
    find_codex,
    install_client_config,
    install_source_tree,
    managed_block,
    migrate_thread_provider_index,
    native_router_health,
    prepare_legacy_opencodex_handoff,
    restore_legacy_opencodex_handoff,
    uninstall_client_config,
)
from pcl_codex_bridge.models import AGENTS, model_catalog
from pcl_codex_bridge.relay_discovery import find_tailscale


class ClientConfigTests(unittest.TestCase):
    def test_codex_runtime_discovery_follows_explicit_persistent_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home, pvc = Path(temp) / "home", Path(temp) / "pvc"
            home.mkdir()
            (pvc / ".codex").mkdir(parents=True)
            (home / ".codex").symlink_to(pvc / ".codex", target_is_directory=True)
            binary = pvc / ".vscode-server/extensions/openai.chatgpt-26.901/bin/linux-x86_64/codex"
            binary.parent.mkdir(parents=True)
            binary.write_text("test fixture")
            binary.chmod(0o755)
            def version(command, **_kwargs):
                return mock.Mock(returncode=0 if Path(command[0]).resolve() == binary.resolve() else 1, stdout="codex")
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                os.environ, {"CODEX_HOME": str(home / ".codex")}
            ), mock.patch("pcl_codex_bridge.client_config.subprocess.run", side_effect=version):
                self.assertEqual(Path(find_codex()).resolve(), binary.resolve())

    def test_legacy_handoff_is_scoped_and_exactly_rollbackable(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            config = home / "config.toml"
            original = (
                f"{ROOT_BEGIN}\n"
                'model_provider = "pcl_relay_official"\n'
                f"{ROOT_END}\n\n"
                'model = "gpt-5.6-sol"\n\n'
                f"{BEGIN}\n"
                "[mcp_servers.pcl_relay]\n"
                'command = "pcl-codex"\n'
                f"{END}\n"
            )
            config.write_text(original, encoding="utf-8")
            roles = home / "agents"
            roles.mkdir()
            role = roles / "pcl-glm.toml"
            role.write_text("legacy role\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                handoff = prepare_legacy_opencodex_handoff()

            cleaned = config.read_text(encoding="utf-8")
            self.assertTrue(handoff["changed"])
            self.assertNotIn(ROOT_BEGIN, cleaned)
            self.assertNotIn(BEGIN, cleaned)
            self.assertIn('model = "gpt-5.6-sol"', cleaned)
            self.assertEqual(Path(handoff["backup"]).read_text(encoding="utf-8"), original)
            self.assertEqual(role.read_text(encoding="utf-8"), "legacy role\n")

            config.write_text("partial OpenCodex injection\n", encoding="utf-8")
            restored = restore_legacy_opencodex_handoff(handoff)
            self.assertTrue(restored["restored"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_provider_index_migration_backs_up_and_preserves_other_rows(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            rollouts = home / "sessions"
            rollouts.mkdir()
            first = rollouts / "old-1.jsonl"
            second = rollouts / "old-2.jsonl"
            other = rollouts / "other.jsonl"
            first.write_text('{"type":"session_meta","payload":{"model_provider":"openai"}}\n{"type":"event","value":1}\n')
            second.write_text(
                '{"type":"session_meta","payload":{"model_provider": "openai"}}\n'
                '{"type":"event","value":"\\\"model_provider\\\":\\\"openai\\\""}\n'
                '{"type":"session_meta","payload":{"model_provider":"openai"}}\n'
            )
            other.write_text('{"type":"session_meta","payload":{"model_provider":"custom"}}\n')
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, model_provider TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?)",
                [
                    ("old-1", str(first), "openai"),
                    # A previous database-only repair must not hide this rollout
                    # from the durable session metadata migration.
                    ("old-2", str(second), "pcl_relay_official"),
                    ("other", str(other), "custom"),
                ],
            )
            connection.commit()
            connection.close()

            result = migrate_thread_provider_index(home, "openai", "pcl_relay_official")

            self.assertEqual(result["migrated_threads"], 2)
            self.assertEqual(result["migrated_rollouts"], 2)
            self.assertTrue(Path(result["backup"]).exists())
            self.assertTrue(Path(result["journal"]).exists())
            connection = sqlite3.connect(database)
            rows = connection.execute("SELECT id, model_provider FROM threads ORDER BY id").fetchall()
            connection.close()
            self.assertEqual(
                rows,
                [("old-1", "pcl_relay_official"), ("old-2", "pcl_relay_official"), ("other", "custom")],
            )
            self.assertIn('"model_provider":"pcl_relay_official"', first.read_text())
            self.assertEqual(first.read_text().splitlines()[1], '{"type":"event","value":1}')
            second_lines = second.read_text().splitlines()
            self.assertIn('"model_provider":"pcl_relay_official"', second_lines[0])
            self.assertEqual(second_lines[1], '{"type":"event","value":"\\\"model_provider\\\":\\\"openai\\\""}')
            self.assertIn('"model_provider":"pcl_relay_official"', second_lines[2])

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
            (source / "LICENSE").write_text("license\n", encoding="utf-8")
            (source / "NOTICE").write_text("notice\n", encoding="utf-8")
            with (
                mock.patch("pcl_codex_bridge.client_config.INSTALL_ROOT", source),
                mock.patch("pcl_codex_bridge.client_config.BIN_PATH", source / "bin" / "pcl-codex"),
                mock.patch("pcl_codex_bridge.client_config.shutil.copytree") as copytree,
            ):
                install_source_tree(source)
            copytree.assert_not_called()

    def test_legacy_proxy_detection_consumes_only_explicit_environment(self):
        with (
            mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
            mock.patch.dict(
                os.environ,
                {"PCL_RELAY_OFFICIAL_PROXY": "http://127.0.0.1:12450"},
                clear=True,
            ),
        ):
            selected = detect_official_proxy()
        self.assertEqual(selected, "http://127.0.0.1:12450")

    def test_proxy_detection_does_not_guess_open_local_ports(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PCL_RELAY_OFFICIAL_PROXY": "",
                    "HTTPS_PROXY": "",
                    "https_proxy": "",
                },
                clear=False,
            ),
            mock.patch("pcl_codex_bridge.client_config.load_registry", return_value={}),
            mock.patch("pcl_codex_bridge.client_config.probe_official_proxy") as probe,
        ):
            selected = detect_official_proxy()
        self.assertEqual(selected, "")
        probe.assert_not_called()

    def test_deep_router_health_explicitly_probes_official_route(self):
        with (
            mock.patch(
                "pcl_codex_bridge.client_config.load_registry",
                return_value={"native_router_port": 15724},
            ),
            mock.patch(
                "pcl_codex_bridge.client_config.request_json",
                return_value={
                    "service": "pcl-relay-native-router",
                    "official_route_reachable": True,
                },
            ) as request,
        ):
            result = native_router_health(timeout=10, probe_official=True)
        self.assertTrue(result["reachable"])
        self.assertIn("?probe=official", request.call_args.args[0])

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
            self.assertIn('model_provider = "pcl_relay_official"', updated)
            self.assertIn('[model_providers.pcl_relay_official]', updated)
            self.assertIn('supports_websockets = false', updated)
            self.assertIn('requires_openai_auth = true', updated)
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

    def test_combined_catalog_refreshes_stale_native_base_from_codex_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            (home / "pcl-native-base-models.json").write_text(
                '{"models":[{"slug":"gpt-5.6-sol","display_name":"GPT-5.6 Sol"}]}\n',
                encoding="utf-8",
            )
            (home / "models_cache.json").write_text(
                '{"models":[{"slug":"gpt-5.6-sol","display_name":"GPT-5.6 Sol"},'
                '{"slug":"gpt-6-astra","display_name":"GPT-6 Astra"}]}\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                models = combined_catalog()["models"]
            self.assertIn("gpt-6-astra", {item["slug"] for item in models})
            refreshed = json.loads((home / "pcl-native-base-models.json").read_text(encoding="utf-8"))
            self.assertIn("gpt-6-astra", {item["slug"] for item in refreshed["models"]})

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
