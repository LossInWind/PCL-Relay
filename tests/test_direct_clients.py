import json
import subprocess
import unittest
from unittest import mock

from pcl_codex_bridge.direct_clients import install_local_direct


class DirectClientTests(unittest.TestCase):
    def setUp(self):
        self.registry = {"gateway": "http://100.64.0.1:15722/v1"}
        self.nodes = {
            "nodes": [
                {
                    "node_name": "pod",
                    "tailscale_ip": "100.64.0.2",
                    "ssh_target": "pod",
                }
            ]
        }
        self.installed = json.dumps(
            {"update_source": "github_release", "client_version": "2.5.2"}
        ).encode("utf-8")

    def common_patches(self):
        return (
            mock.patch("pcl_codex_bridge.direct_clients.load_registry", return_value=self.registry),
            mock.patch("pcl_codex_bridge.direct_clients.discover_relays", return_value=self.nodes),
            mock.patch("pcl_codex_bridge.direct_clients.ssh_inventory", return_value={}),
            mock.patch("pcl_codex_bridge.direct_clients._node_ssh_target", return_value="pod"),
            mock.patch(
                "pcl_codex_bridge.direct_clients._selected_relay_ssh_target",
                return_value="relay",
            ),
            mock.patch("pcl_codex_bridge.direct_clients._read_relay_key", return_value=b"x" * 32),
            mock.patch(
                "pcl_codex_bridge.direct_clients.remote_client_status",
                return_value={"ready": True},
            ),
        )

    def test_direct_update_does_not_send_mac_archive_when_github_succeeds(self):
        completed = subprocess.CompletedProcess([], 0, self.installed, b"")
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], mock.patch(
            "pcl_codex_bridge.direct_clients._run_remote_python", return_value=completed
        ) as run, mock.patch("pcl_codex_bridge.direct_clients._source_archive") as archive:
            result = install_local_direct("pod")
        self.assertEqual(result["update_source"], "github_release")
        archive.assert_not_called()
        self.assertEqual(len(run.call_args.kwargs["stdin"]), 8 + 32)

    def test_direct_update_receives_mac_archive_only_after_github_failure(self):
        unavailable = subprocess.CompletedProcess(
            [], 1, b"", b"PCL_GITHUB_UNAVAILABLE: GitHub unavailable"
        )
        installed = dict(json.loads(self.installed))
        installed["update_source"] = "current_mac_fallback"
        fallback = subprocess.CompletedProcess([], 0, json.dumps(installed).encode(), b"")
        patches = self.common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], mock.patch(
            "pcl_codex_bridge.direct_clients._run_remote_python",
            side_effect=[unavailable, fallback],
        ) as run, mock.patch(
            "pcl_codex_bridge.direct_clients._source_archive", return_value=b"fallback"
        ) as archive:
            result = install_local_direct("pod")
        self.assertEqual(result["update_source"], "current_mac_fallback")
        self.assertIn("GitHub unavailable", result["github_error"])
        archive.assert_called_once_with()
        self.assertEqual(len(run.call_args_list[0].kwargs["stdin"]), 8 + 32)
        self.assertEqual(run.call_args_list[1].kwargs["stdin"][-8:], b"fallback")


if __name__ == "__main__":
    unittest.main()
