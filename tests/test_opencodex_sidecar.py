import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.opencodex_sidecar import (
    OPENCODEX_COMMIT,
    OPENCODEX_DEFAULT_PORT,
    OPENCODEX_RELEASE_ID,
    OPENCODEX_VERSION,
    activate_sidecar,
    apply_pending_opencodex_proxy_policy,
    configure_sidecar,
    configure_opencodex_proxy_policy,
    deactivate_sidecar,
    invoke_sidecar,
    prepare_sidecar,
    runtime_at,
    reload_pcl_provider,
    opencodex_proxy_policy,
    sidecar_environment,
    sidecar_health,
    sidecar_integration_status,
    stage_bundled_runtime,
    switch_pcl_gateway,
)


def fake_runtime(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "src" / "cli").mkdir(parents=True)
    bun = root / "bin" / "bun"
    bun.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bun.chmod(0o755)
    (root / "src" / "cli" / "index.ts").write_text("", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "@bitkyc08/opencodex", "version": OPENCODEX_VERSION}),
        encoding="utf-8",
    )
    (root / "UPSTREAM.json").write_text(
        json.dumps({"version": OPENCODEX_VERSION, "commit": OPENCODEX_COMMIT}),
        encoding="utf-8",
    )
    return root


class OpenCodexSidecarTests(unittest.TestCase):
    def test_service_install_pins_runtime_not_build_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            with mock.patch("pcl_codex_bridge.opencodex_sidecar.subprocess.run") as run:
                invoke_sidecar(runtime, ["service", "install"], Path(temp) / "config")
            self.assertEqual(run.call_args.kwargs["env"]["OPENCODEX_BUN_PATH"], str(runtime.bun))

    def test_provider_reload_rejection_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            def runner(_runtime, arguments, _home, _timeout):
                self.assertEqual(arguments[0], "--eval")
                self.assertIn('requestBoundLocalProviderReload(p,"pcl")', arguments[1])
                return subprocess.CompletedProcess(arguments, 1, stdout='{"kind":"unavailable"}', stderr="")
            with self.assertRaises(RuntimeError):
                reload_pcl_provider(runtime, Path(temp) / "config", runner)

    def test_runtime_requires_exact_upstream_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = fake_runtime(Path(temp) / "opencodex")
            runtime = runtime_at(root)
            self.assertEqual(runtime.version, OPENCODEX_VERSION)
            self.assertEqual(runtime.commit, OPENCODEX_COMMIT)
            self.assertTrue(OPENCODEX_RELEASE_ID.endswith("-bun1.3.14"))
            (root / "UPSTREAM.json").write_text(
                json.dumps({"version": OPENCODEX_VERSION, "commit": "wrong"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "pinned source"):
                runtime_at(root)

    def test_stage_is_versioned_and_does_not_restart_or_replace_current(self):
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp) / "bundle"
            runtime_root = fake_runtime(source_root / "opencodex")
            install_home = Path(temp) / "install" / "opencodex"
            first = stage_bundled_runtime(source_root, install_home)
            self.assertTrue(first["installed"])
            self.assertTrue(first["activated"])
            self.assertFalse(first["service_restarted"])
            self.assertEqual(
                (install_home / "current").resolve(),
                (install_home / "releases" / OPENCODEX_RELEASE_ID).resolve(),
            )
            second = stage_bundled_runtime(source_root, install_home)
            self.assertFalse(second["installed"])
            self.assertFalse(second["activated"])
            self.assertFalse(second["service_restarted"])
            self.assertTrue(runtime_root.is_dir())

    def test_read_only_app_runtime_can_be_staged_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bundle"
            root = fake_runtime(source / "opencodex")
            root.chmod(0o555)
            try:
                result = stage_bundled_runtime(source, Path(temp) / "install")
                self.assertTrue(result["installed"])
                self.assertTrue(Path(result["runtime"]).stat().st_mode & 0o200)
                self.assertEqual(root.stat().st_mode & 0o777, 0o555)
            finally:
                root.chmod(0o755)

    def test_environment_hands_discovered_codex_to_upstream_runtime_resolver(self):
        with tempfile.TemporaryDirectory() as temp:
            config_home = Path(temp) / "ocx"
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "pcl_codex_bridge.client_config.find_codex",
                return_value="/Applications/ChatGPT.app/Contents/Resources/codex",
            ):
                environment = sidecar_environment(config_home)
            self.assertEqual(environment["OPENCODEX_HOME"], str(config_home))
            self.assertEqual(
                environment["CODEX_CLI_PATH"],
                "/Applications/ChatGPT.app/Contents/Resources/codex",
            )

            with mock.patch.dict(
                os.environ,
                {"CODEX_CLI_PATH": "/chosen/codex"},
                clear=True,
            ):
                environment = sidecar_environment(config_home)
            self.assertEqual(environment["CODEX_CLI_PATH"], "/chosen/codex")

    def test_configuration_is_only_thin_upstream_cli_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                commands.append(list(arguments))
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            config_home = Path(temp) / "config"
            result = configure_sidecar(
                runtime,
                "http://100.113.234.58:15722/v1",
                ["DeepSeek-V4-Pro", "GLM-5.2", "GLM-5.2"],
                config_home=config_home,
                runner=runner,
            )

            self.assertEqual(result["port"], OPENCODEX_DEFAULT_PORT)
            self.assertEqual(result["selected_models"], ["DeepSeek-V4-Pro", "GLM-5.2"])
            self.assertEqual(result["subagent_models"], ["pcl/DeepSeek-V4-Pro", "pcl/GLM-5.2"])
            self.assertEqual(result["official_provider"], "openai")
            self.assertEqual(result["official_account_mode"], "direct")
            self.assertFalse(result["codex_integration_enabled"])
            self.assertIn(
                ["config", "set", "port", str(OPENCODEX_DEFAULT_PORT), "--json"],
                commands,
            )
            self.assertIn(
                ["config", "set", "websockets", "true", "--json"],
                commands,
            )
            provider_command = next(
                command
                for command in commands
                if command[:3] == ["provider", "add", "pcl"]
            )
            self.assertEqual(commands[0], provider_command)
            self.assertEqual(
                provider_command[provider_command.index("--adapter") + 1],
                "openai-chat",
            )
            self.assertFalse(
                any(
                    command[:3] == ["config", "set", "providers.pcl.responsesPath"]
                    for command in commands
                )
            )
            self.assertIn(
                ["config", "set", "clientIntegrations", '{"codex": false}', "--json"],
                commands,
            )
            self.assertIn(
                ["config", "set", "multiAgentMode", '"v2"', "--json"],
                commands,
            )
            self.assertIn(
                ["config", "set", "providers.pcl.autoToolChoiceOnlyModels", '["GLM-5.2"]', "--json"],
                commands,
            )
            self.assertIn(
                ["config", "set", "keepNativeChatGptOnV1", "true", "--json"],
                commands,
            )
            self.assertIn(
                ["config", "set", "providers.openai.codexAccountMode", '"direct"', "--json"],
                commands,
            )
            self.assertFalse(any("proxy" in command or "noProxy" in command for command in commands))
            self.assertFalse(any(command[:3] == ["provider", "set-default", "pcl"] for command in commands))
            self.assertIn(["config", "validate", "--json"], commands)
            self.assertEqual(commands[-2][:3], ["config", "set", "clientIntegrations"])
            self.assertEqual(commands[-1][0], "--eval")
            self.assertIn("requestBoundLocalProviderReload", commands[-1][1])
            self.assertEqual(os.stat(config_home).st_mode & 0o777, 0o700)

    def test_gateway_switch_changes_only_pcl_provider_and_validates(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                commands.append(list(arguments))
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            result = switch_pcl_gateway(
                runtime,
                "http://relay.example:15722/v1",
                ["GLM-5.2", "DeepSeek-V4-Pro"],
                config_home=Path(temp) / "config",
                runner=runner,
            )
        self.assertEqual(commands[0][:3], ["provider", "add", "pcl"])
        self.assertEqual(commands[-2], ["config", "validate", "--json"])
        self.assertEqual(commands[-1][0], "--eval")
        self.assertFalse(any("openai" in command for command in commands))
        self.assertFalse(any("clientIntegrations" in command for command in commands))
        self.assertFalse(result["service_restarted"])
        self.assertFalse(result["official_route_changed"])

    def test_explicit_proxy_policy_is_delegated_to_upstream_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command[:3] == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout='{"proxy":"http://old:7890","noProxy":["localhost"]}\n',
                        stderr="",
                    )
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            policy = opencodex_proxy_policy(runtime, Path(temp) / "config", runner)
            self.assertEqual(policy["proxy"], "http://old:7890")
            result = configure_opencodex_proxy_policy(
                runtime,
                proxy="http://new:7890",
                no_proxy=["localhost", "relay.internal"],
                config_home=Path(temp) / "config",
                runner=runner,
            )
        self.assertIn(["config", "set", "proxy", '"http://new:7890"', "--json"], commands)
        self.assertIn(
            ["config", "set", "noProxy", '["localhost", "relay.internal"]', "--json"],
            commands,
        )
        self.assertIn(["config", "validate", "--json"], commands)
        self.assertFalse(result["automatic_discovery"])
        self.assertFalse(result["service_restarted"])

    def test_proxy_policy_failure_restores_previous_upstream_values(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []
            failed = False

            def runner(_runtime, arguments, _config_home, _timeout):
                nonlocal failed
                command = list(arguments)
                commands.append(command)
                if command[:3] == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"proxy":"http://old:7890","noProxy":["localhost"]}\n', stderr="")
                if command[:3] == ["config", "validate", "--json"] and not failed:
                    failed = True
                    return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="invalid")
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            with self.assertRaisesRegex(RuntimeError, "rolled back"):
                configure_opencodex_proxy_policy(
                    runtime,
                    proxy="http://new:7890",
                    config_home=Path(temp) / "config",
                    runner=runner,
                )
        self.assertGreaterEqual(
            commands.count(["config", "set", "proxy", '"http://old:7890"', "--json"]),
            1,
        )

    def test_proxy_clear_is_idempotent_before_upstream_config_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout="{}\n", stderr="")
                if command[:2] == ["config", "unset"]:
                    return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="Error: config is missing")
                if command == ["health", "--json"]:
                    return subprocess.CompletedProcess(arguments, 1, stdout='{"ok":false}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="unexpected")

            result = configure_opencodex_proxy_policy(
                runtime,
                clear=True,
                config_home=Path(temp) / "config",
                runner=runner,
            )
        self.assertFalse(result["restart_required"])
        self.assertFalse(result["service_restarted"])
        self.assertNotIn(["config", "validate", "--json"], commands)

    def test_proxy_set_missing_config_preserves_original_error_after_noop_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                if command == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout="{}\n", stderr="")
                if command[:3] == ["config", "set", "proxy"]:
                    return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="Error: config is missing")
                if command[:2] == ["config", "unset"]:
                    return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="Error: config is missing")
                return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="unexpected")

            with self.assertRaisesRegex(RuntimeError, "rejected and rolled back") as raised:
                configure_opencodex_proxy_policy(
                    runtime,
                    proxy="http://127.0.0.1:7890",
                    config_home=Path(temp) / "config",
                    runner=runner,
                )
        self.assertNotIn("rollback failed", str(raised.exception))

    def test_proxy_change_waits_when_upstream_reports_active_turns(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout='{"proxy":"http://127.0.0.1:7890","noProxy":["localhost"]}\n',
                        stderr="",
                    )
                if command == ["health", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"pid":101,"port":15725}\n', stderr="")
                if command == ["memory", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"activeTurnCount":2,"isDraining":false}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            result = configure_opencodex_proxy_policy(
                runtime,
                proxy="http://127.0.0.1:7890",
                no_proxy=["localhost"],
                config_home=Path(temp) / "config",
                runner=runner,
            )
            shown = opencodex_proxy_policy(runtime, Path(temp) / "config", runner)
        self.assertTrue(result["restart_required"])
        self.assertFalse(result["service_restarted"])
        self.assertEqual(result["active_turn_count"], 2)
        self.assertEqual(result["lifecycle_reason"], "active_requests")
        self.assertTrue(shown["restart_required"])
        self.assertNotIn(["restart"], commands)

    def test_pending_proxy_change_uses_upstream_idle_restart_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            config_home = Path(temp) / "config"
            config_home.mkdir()
            (config_home / ".pcl-relay-proxy-restart.json").write_text(
                '{"pid":101}\n', encoding="utf-8"
            )
            commands = []
            restarted = False

            def runner(_runtime, arguments, _config_home, _timeout):
                nonlocal restarted
                command = list(arguments)
                commands.append(command)
                if command == ["health", "--json"]:
                    pid = 202 if restarted else 101
                    return subprocess.CompletedProcess(arguments, 0, stdout=f'{{"ok":true,"pid":{pid},"port":15725}}\n', stderr="")
                if command == ["memory", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"activeTurnCount":0,"isDraining":false}\n', stderr="")
                if command == ["restart"]:
                    restarted = True
                    return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
                if command and command[0] == "ready":
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ready":true,"pid":202,"port":15725}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="unexpected")

            result = apply_pending_opencodex_proxy_policy(
                runtime,
                config_home=config_home,
                runner=runner,
            )
            marker_exists = (config_home / ".pcl-relay-proxy-restart.json").exists()
        self.assertFalse(result["restart_required"])
        self.assertTrue(result["service_restarted"])
        self.assertEqual(result["previous_pid"], 101)
        self.assertEqual(result["pid"], 202)
        self.assertIn(["restart"], commands)
        self.assertFalse(marker_exists)

    def test_prepare_reuses_a_healthy_sidecar_without_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command == ["health", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"port":15725}\n', stderr="")
                if command and command[0] == "ready":
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ready":true,"pid":42,"port":15725}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true}\n', stderr="")

            result = prepare_sidecar(
                runtime,
                "http://100.113.234.58:15722/v1",
                ["GLM-5.2"],
                config_home=Path(temp) / "config",
                runner=runner,
            )
            self.assertTrue(result["ready"])
            self.assertFalse(result["service_started"])
            self.assertNotIn(["service", "install"], commands)
            self.assertEqual(commands[0], ["health", "--json"])
            self.assertEqual(commands[-1][0], "ready")

    def test_prepare_starts_service_only_when_health_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command == ["health", "--json"]:
                    return subprocess.CompletedProcess(arguments, 1, stdout='{"ok":false}\n', stderr="")
                if command and command[0] == "ready":
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ready":true,"pid":43,"port":15725}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

            result = prepare_sidecar(
                runtime,
                "http://100.113.234.58:15722/v1",
                ["GLM-5.2"],
                config_home=Path(temp) / "config",
                runner=runner,
            )
            self.assertTrue(result["service_started"])
            self.assertIn(["service", "install"], commands)

    def test_prepare_refuses_to_move_a_healthy_data_plane(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))

            def runner(_runtime, arguments, _config_home, _timeout):
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"port":15726}\n', stderr="")

            with self.assertRaisesRegex(RuntimeError, "refusing to restart"):
                prepare_sidecar(
                    runtime,
                    "http://100.113.234.58:15722/v1",
                    ["GLM-5.2"],
                    config_home=Path(temp) / "config",
                    runner=runner,
                )

    def test_activation_and_restore_are_delegated_to_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command and command[0] == "ready":
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ready":true,"pid":44,"port":15725}\n', stderr="")
                if command == ["integration", "native", "codex", "on", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"desiredEnabled":true,"state":"current"}\n', stderr="")
                if command == ["integration", "native", "codex", "off", "--json"]:
                    return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"desiredEnabled":false,"state":"absent"}\n', stderr="")
                return subprocess.CompletedProcess(arguments, 1, stdout='{"ok":false}\n', stderr="")

            active = activate_sidecar(runtime, config_home=Path(temp), runner=runner)
            restored = deactivate_sidecar(runtime, config_home=Path(temp), runner=runner)
            self.assertTrue(active["active"])
            self.assertFalse(restored["active"])
            self.assertFalse(restored["sidecar_stopped"])
            self.assertIn(["integration", "native", "codex", "on", "--json"], commands)
            self.assertIn(["integration", "native", "codex", "off", "--json"], commands)

    def test_health_is_open_codex_cli_output(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))

            def runner(_runtime, arguments, _config_home, _timeout):
                return subprocess.CompletedProcess(arguments, 0, stdout='{"ok":true,"pid":45,"port":15725}\n', stderr="")

            self.assertEqual(sidecar_health(runtime, Path(temp), runner)["pid"], 45)

    def test_restore_intent_without_restored_artifacts_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            def runner(_runtime, arguments, _home, _timeout):
                return subprocess.CompletedProcess(arguments, 0,
                    stdout='{"ok":true,"desiredEnabled":false,"state":"unsafe","reason":"restore_incomplete"}', stderr="")
            with self.assertRaisesRegex(RuntimeError, "did not restore"):
                deactivate_sidecar(runtime, config_home=Path(temp), runner=runner)

    def test_integration_status_uses_durable_and_applied_open_codex_state(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))
            commands = []

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                commands.append(command)
                if command == ["status", "--json"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=(
                            '{"proxy":{"running":true},'
                            '"startup":{"routingInjected":true,"routingKind":"opencodex-local"},'
                            '"codexHome":{"effectiveCodexHome":"/tmp/codex"}}\n'
                        ),
                        stderr="",
                    )
                if command == ["config", "show", "--json"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout='{"providers":{},"defaultProvider":"openai"}\n',
                        stderr="",
                    )
                return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="unexpected")

            status = sidecar_integration_status(runtime, Path(temp), runner)
            self.assertEqual(status["codex"]["state"], "current")
            self.assertTrue(status["codex"]["desiredEnabled"])
            self.assertEqual(status["codex"]["configPath"], "/tmp/codex/config.toml")
            self.assertEqual(
                commands,
                [["status", "--json"], ["config", "show", "--json"]],
            )

    def test_integration_status_keeps_durable_off_separate_from_stale_applied_state(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = runtime_at(fake_runtime(Path(temp) / "runtime"))

            def runner(_runtime, arguments, _config_home, _timeout):
                command = list(arguments)
                stdout = (
                    '{"startup":{"routingInjected":true}}\n'
                    if command == ["status", "--json"]
                    else '{"clientIntegrations":{"codex":false}}\n'
                )
                return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

            status = sidecar_integration_status(runtime, Path(temp), runner)
            self.assertEqual(status["codex"]["state"], "current")
            self.assertFalse(status["codex"]["desiredEnabled"])


if __name__ == "__main__":
    unittest.main()
