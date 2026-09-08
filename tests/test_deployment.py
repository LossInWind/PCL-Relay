import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import deployment


class DeploymentTests(unittest.TestCase):
    def test_registry_contains_only_explicit_targets_and_private_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deployment-targets.json"
            with (
                mock.patch.object(deployment, "TARGETS_PATH", path),
                mock.patch.object(deployment, "probe_target", return_value={"ssh": False}),
            ):
                result = deployment.add_target(
                    "kai-mac",
                    "http://100.64.0.8:15726",
                    "Kai Mac",
                )
                catalog = deployment.list_targets()
            self.assertTrue(result["added"])
            self.assertEqual(catalog["count"], 1)
            self.assertEqual(catalog["targets"][0]["ssh_target"], "kai-mac")
            self.assertFalse(catalog["discovery"])
            self.assertFalse(catalog["credentials_synchronized"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unsafe_ssh_target_and_credential_url_are_rejected(self):
        with self.assertRaises(RuntimeError):
            deployment._validate_ssh_target("host; touch /tmp/bad")
        with self.assertRaises(RuntimeError):
            deployment.add_target("safe-host", "http://user:secret@100.64.0.8:15726")

    def test_control_endpoint_is_derived_from_one_explicit_ssh_alias(self):
        completed = mock.Mock(
            returncode=0,
            stdout="host kai-mac\nhostname 100.64.0.8\nuser Apple\n",
            stderr="",
        )
        with mock.patch.object(deployment.subprocess, "run", return_value=completed) as run:
            result = deployment._control_url_from_ssh("kai-mac")
        self.assertEqual(result, "http://100.64.0.8:15726")
        self.assertEqual(run.call_args.args[0], ["ssh", "-G", "kai-mac"])

    def test_explicit_ssh_import_deduplicates_bootstrap_aliases_without_connecting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            registry = root / "targets.json"
            config.write_text(
                "Host server-main\n  HostName 100.64.0.9\n"
                "Host server-bootstrap\n  HostName 100.64.0.9\n"
                "Host kai-mac\n  HostName 100.64.0.10\n",
                encoding="utf-8",
            )

            def effective(command, **_kwargs):
                alias = command[-1]
                host = "100.64.0.10" if alias == "kai-mac" else "100.64.0.9"
                return mock.Mock(returncode=0, stdout=f"hostname {host}\nuser Apple\n", stderr="")

            with (
                mock.patch.object(deployment, "TARGETS_PATH", registry),
                mock.patch.object(deployment.subprocess, "run", side_effect=effective) as run,
            ):
                result = deployment.import_ssh_targets(config)
            self.assertEqual(result["count"], 2)
            self.assertTrue(result["network_connections_opened"] is False)
            self.assertTrue(result["credentials_read"] is False)
            aliases = {item["ssh_target"] for item in result["targets"]}
            self.assertEqual(aliases, {"server-main", "kai-mac"})
            self.assertTrue(all(call.args[0][:2] == ["ssh", "-G"] for call in run.call_args_list))

    def test_probe_does_not_infer_or_scan_targets(self):
        target = {
            "id": "one",
            "name": "One",
            "ssh_target": "one-mac",
            "control_url": "http://100.64.0.9:15726",
            "added_at": "",
        }
        completed = mock.Mock(returncode=0, stdout=json.dumps({
            "system": "Darwin", "architecture": "arm64", "version": "", "installed": False,
        }).encode(), stderr=b"")
        with (
            mock.patch.object(deployment, "heartbeat", side_effect=RuntimeError("offline")),
            mock.patch.object(deployment, "_run_ssh", return_value=completed) as ssh,
        ):
            result = deployment.probe_target(target)
        self.assertTrue(result["ssh"])
        self.assertFalse(result["receiver_online"])
        ssh.assert_called_once_with("one-mac", deployment.REMOTE_PROBE, 20)

    def test_receiver_ready_target_is_registered_without_ssh_bootstrap(self):
        target = {
            "id": "one", "name": "One", "ssh_target": "one-mac",
            "control_url": "http://100.64.0.9:15726", "added_at": "",
        }
        with (
            mock.patch.object(deployment, "_load_targets", return_value=[target]),
            mock.patch.object(deployment, "latest_release_manifest", return_value={"latest_version": "2.6.0"}),
            mock.patch.object(deployment, "probe_target", return_value={
                "receiver_online": True, "relay_version": "2.5.6", "ssh": False,
            }),
            mock.patch.object(deployment, "add_peer") as add,
            mock.patch.object(deployment, "_run_ssh") as ssh,
        ):
            result = deployment.deploy_target("one")
        self.assertEqual(result["action"], "receiver-ready")
        add.assert_called_once_with(target["control_url"], target["name"])
        ssh.assert_not_called()

    def test_deployment_refuses_to_downgrade_to_an_older_public_release(self):
        target = {
            "id": "one", "name": "One", "ssh_target": "one-mac",
            "control_url": "http://100.64.0.9:15726", "added_at": "",
        }
        with mock.patch.object(deployment, "_load_targets", return_value=[target]):
            with self.assertRaisesRegex(RuntimeError, "older than this node"):
                deployment.deploy_target("one", {"latest_version": "2.5.4"})

    def test_github_failure_uses_verified_local_cache_for_bootstrap(self):
        target = {
            "id": "one", "name": "One", "ssh_target": "one-mac",
            "control_url": "http://100.64.0.9:15726", "added_at": "",
        }
        manifest = {
            "latest_version": "2.6.0",
            "assets": {
                "PCL-Relay-macOS.zip": {
                    "asset_name": "PCL-Relay-macOS.zip",
                    "asset_url": "https://example/release.zip",
                    "asset_size": 7,
                    "asset_sha256": "a" * 64,
                }
            },
        }
        first = mock.Mock(returncode=75, stdout=b'{"needs_peer_fallback":true,"error":"timeout"}', stderr=b"")
        second = mock.Mock(returncode=0, stdout=b'{"installed":true,"version":"2.6.0"}', stderr=b"")
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "release.zip"
            archive.write_bytes(b"release")
            with (
                mock.patch.object(deployment, "_load_targets", return_value=[target]),
                mock.patch.object(deployment, "probe_target", return_value={
                    "receiver_online": False, "ssh": True, "system": "Darwin", "architecture": "arm64",
                }),
                mock.patch.object(deployment, "_run_ssh", side_effect=[first, second]) as run,
                mock.patch.object(deployment, "cache_release_asset", return_value={"path": str(archive)}),
                mock.patch.object(deployment, "add_peer"),
                mock.patch.object(deployment, "sync_once"),
                mock.patch.object(deployment.time, "sleep"),
            ):
                result = deployment.deploy_target("one", manifest)
        self.assertEqual(result["artifact_source"] if "artifact_source" in result else result["action"], "bootstrapped")
        self.assertEqual(result["github_error"], "timeout")
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
