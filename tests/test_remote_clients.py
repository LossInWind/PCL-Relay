import json
import subprocess
import unittest
from unittest import mock

from pcl_codex_bridge.remote_clients import (
    _is_relay_candidate,
    remote_client_status,
    check_client_connectivity,
)


class RemoteClientTests(unittest.TestCase):
    def test_remote_status_accepts_macos_and_linux_client_metadata(self):
        payload = {
            "home": "/home/test",
            "system": "Linux",
            "architecture": "x86_64",
            "python_version": "3.10.12",
            "supported_system": True,
            "config_managed": True,
            "client_installed": True,
            "native_v1": False,
            "native_v2": True,
            "native_roles": True,
            "client_version": "2.2.1",
            "expected_client_version": "2.2.1",
            "update_available": False,
            "native_router_reachable": True,
            "native_router_gateway_reachable": True,
            "gateway": "http://relay.tail.test:15722/v1",
            "gateway_reachable": True,
            "error": "",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with mock.patch("pcl_codex_bridge.remote_clients._run_remote_python", return_value=completed):
            result = remote_client_status("linux-server", payload["gateway"])
        self.assertTrue(result["ssh"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["system"], "Linux")

    def test_remote_status_rejects_unsupported_system(self):
        payload = {
            "system": "Windows",
            "supported_system": False,
            "config_managed": True,
            "client_installed": True,
            "native_v1": False,
            "native_v2": True,
            "native_roles": True,
            "client_version": "2.2.1",
            "expected_client_version": "2.2.1",
            "update_available": False,
            "native_router_reachable": True,
            "native_router_gateway_reachable": True,
            "gateway": "http://relay.tail.test:15722/v1",
            "gateway_reachable": True,
            "error": "",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with mock.patch("pcl_codex_bridge.remote_clients._run_remote_python", return_value=completed):
            result = remote_client_status("unsupported", payload["gateway"])
        self.assertFalse(result["ready"])

    def test_remote_status_marks_old_client_for_update(self):
        payload = {
            "system": "Linux",
            "supported_system": True,
            "config_managed": True,
            "client_installed": True,
            "client_version": "2.0.0",
            "expected_client_version": "2.2.1",
            "update_available": True,
            "native_v2": True,
            "native_roles": True,
            "native_router_reachable": True,
            "native_router_gateway_reachable": True,
            "gateway": "http://relay.tail.test:15722/v1",
            "gateway_reachable": True,
            "error": "",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with mock.patch("pcl_codex_bridge.remote_clients._run_remote_python", return_value=completed):
            result = remote_client_status("old-linux", payload["gateway"])
        self.assertTrue(result["update_available"])
        self.assertFalse(result["ready"])

    def test_remote_status_accepts_active_loopback_route_when_selected_relay_is_unreachable(self):
        payload = {
            "system": "Linux",
            "supported_system": True,
            "config_managed": True,
            "client_installed": True,
            "client_version": "2.2.1",
            "expected_client_version": "2.2.1",
            "update_available": False,
            "native_v2": True,
            "native_roles": True,
            "native_router_reachable": True,
            "native_router_gateway_reachable": True,
            "gateway": "http://relay.tail.test:15722/v1",
            "gateway_reachable": False,
            "configured_gateway": "http://127.0.0.1:15722/v1",
            "configured_gateway_reachable": True,
            "error": "selected relay timed out",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with mock.patch("pcl_codex_bridge.remote_clients._run_remote_python", return_value=completed):
            result = remote_client_status("pcl-pod", payload["gateway"])
        self.assertTrue(result["ready"])

    def test_management_ssh_disables_all_configured_forwardings(self):
        from pcl_codex_bridge.remote_clients import SSH_OPTIONS

        self.assertIn("ClearAllForwardings=yes", SSH_OPTIONS)
        self.assertIn("BatchMode=yes", SSH_OPTIONS)

    def test_loopback_adapter_without_workspace_tailnet_is_not_a_relay(self):
        node = {
            "gateway": True,
            "pcl_auth": "valid",
            "client_status": {
                "workspace_tailscale": False,
                "pcl_network_reachable": True,
                "relay_capable": False,
                "configured_gateway": "http://127.0.0.1:15722/v1",
                "configured_gateway_reachable": True,
            },
        }
        self.assertFalse(_is_relay_candidate(node))

    @mock.patch("pcl_codex_bridge.remote_clients._tailnet_node_snapshot")
    @mock.patch("pcl_codex_bridge.remote_clients.remote_client_status")
    def test_connectivity_test_explains_tailnet_offline(self, status, tailnet):
        tailnet.return_value = {"found": True, "online": False, "last_seen": "2026-08-31T02:42:37Z"}
        status.return_value = {"ssh": False, "error": "ssh timed out"}
        result = check_client_connectivity("bupt-a100-shared", "http://relay/v1", "100.64.0.4", deep=False)
        self.assertEqual(result["status"], "offline")
        self.assertIn("Tailscale", result["summary"])

    @mock.patch("pcl_codex_bridge.remote_clients._tailnet_node_snapshot")
    @mock.patch("pcl_codex_bridge.remote_clients.remote_client_status")
    def test_connectivity_test_prefers_verified_local_adapter(self, status, tailnet):
        tailnet.return_value = {"found": True, "online": True, "last_seen": ""}
        status.return_value = {
            "ssh": True,
            "supported_system": True,
            "configured_gateway": "http://127.0.0.1:15722/v1",
            "configured_gateway_reachable": True,
            "configured_gateway_models_reachable": True,
            "configured_gateway_model_count": 13,
            "configured_gateway_latency_ms": 53,
            "gateway_reachable": False,
            "error": "",
        }
        result = check_client_connectivity("pcl-pod", "http://relay/v1", "100.64.0.5", deep=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["route"], "local_pcl_direct")
        self.assertEqual(result["model_count"], 13)


if __name__ == "__main__":
    unittest.main()
