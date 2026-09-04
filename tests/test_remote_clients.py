import json
import subprocess
import unittest
from unittest import mock

from pcl_codex_bridge import __version__
from pcl_codex_bridge.remote_clients import (
    _completed_topology_round,
    _is_relay_candidate,
    check_client_connectivity,
    discover_remote_clients,
    remote_client_status,
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

    def test_healthy_tailnet_relay_does_not_require_ssh_credentials(self):
        node = {
            "online": True,
            "gateway": True,
            "pcl_auth": "valid",
            "model_count": 13,
            "client_status": {
                "ssh": False,
                "relay_capable": False,
            },
        }
        self.assertTrue(_is_relay_candidate(node))

    def test_local_mac_is_ready_when_gateway_probe_succeeds_without_ssh_inventory(self):
        relay_report = {
            "checked_at": "2026-09-02T18:39:54+0800",
            "nodes": [
                {
                    "node_name": "relay",
                    "magic_dns": "relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 20,
                    "selected": True,
                },
                {
                    "node_name": "local-mac",
                    "magic_dns": "local.tail.test",
                    "tailscale_ip": "100.64.0.9",
                    "online": True,
                    "self": True,
                    "gateway": False,
                    "pcl_auth": "not_checked",
                    "model_count": 0,
                    "latency_ms": 5,
                    "selected": False,
                },
            ],
        }
        with (
            mock.patch(
                "pcl_codex_bridge.remote_clients.load_registry",
                return_value={"gateway": "http://relay.tail.test:15722/v1"},
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.discover_relays",
                return_value=relay_report,
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients._shared_topology_reports",
                return_value={
                    "42:100.64.0.9": {
                        "node_id": "100.64.0.9",
                        "node_name": "local-mac",
                        "client_version": __version__,
                        "relay_reachable": True,
                        "client_ready": True,
                        "config_managed": True,
                        "native_v2": True,
                        "native_roles": True,
                        "pcl_direct": False,
                        "round_id": 42,
                    }
                },
            ),
            mock.patch("pcl_codex_bridge.remote_clients.ssh_inventory", return_value=[]),
        ):
            result = discover_remote_clients(timeout=2)
        local = next(node for node in result["nodes"] if node["self"])
        self.assertEqual(result["recommendation"]["relay_id"], "100.64.0.8")
        self.assertEqual(local["feasibility"]["recommended_route"], "direct")
        self.assertEqual(result["ready_count"], 1)

    def test_consensus_publishes_latest_complete_round_not_partial_new_round(self):
        relay_report = {
            "nodes": [
                {"tailscale_ip": "100.64.0.8", "online": True},
                {"tailscale_ip": "100.64.0.9", "online": True},
            ]
        }
        reports = {
            "41:a": {"node_id": "100.64.0.8", "round_id": 41},
            "41:b": {"node_id": "100.64.0.9", "round_id": 41},
            "42:a": {"node_id": "100.64.0.8", "round_id": 42},
        }
        selected, state = _completed_topology_round(relay_report, reports)
        self.assertEqual(state["round_id"], 41)
        self.assertTrue(state["complete"])
        self.assertEqual(set(selected), {"100.64.0.8", "100.64.0.9"})

    def test_online_peer_without_heartbeat_is_not_inferred_reachable(self):
        relay_report = {
            "checked_at": "2026-09-02T19:30:00+0800",
            "nodes": [
                {
                    "node_name": "relay",
                    "magic_dns": "relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 10,
                    "selected": True,
                },
                {
                    "node_name": "unknown-peer",
                    "magic_dns": "unknown.tail.test",
                    "tailscale_ip": "100.64.0.12",
                    "online": True,
                    "self": False,
                    "gateway": False,
                    "pcl_auth": "not_checked",
                    "model_count": 0,
                    "latency_ms": 30,
                    "selected": False,
                },
            ],
        }
        with (
            mock.patch("pcl_codex_bridge.remote_clients.load_registry", return_value={"gateway": "http://relay.tail.test:15722/v1"}),
            mock.patch("pcl_codex_bridge.remote_clients.discover_relays", return_value=relay_report),
            mock.patch("pcl_codex_bridge.remote_clients._shared_topology_reports", return_value={}),
            mock.patch("pcl_codex_bridge.remote_clients.ssh_inventory", return_value=[]),
        ):
            result = discover_remote_clients(timeout=2)
        peer = next(item for item in result["nodes"] if item["node_name"] == "unknown-peer")
        self.assertEqual(peer["feasibility"]["recommended_route"], "unavailable")
        self.assertFalse(peer["feasibility"]["direct"])

    def test_pcl_upstream_direct_takes_priority_over_selected_relay(self):
        relay_report = {
            "checked_at": "2026-09-02T19:10:00+0800",
            "nodes": [
                {
                    "node_name": "selected-relay",
                    "magic_dns": "selected-relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 10,
                    "selected": True,
                },
                {
                    "node_name": "pcl-direct",
                    "magic_dns": "pcl-direct.tail.test",
                    "tailscale_ip": "100.64.0.10",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 40,
                    "selected": False,
                },
            ],
        }
        status = {
            "ssh": True,
            "supported_system": True,
            "pcl_network_reachable": True,
            "gateway_reachable": True,
            "configured_gateway": "http://127.0.0.1:15722/v1",
            "configured_gateway_reachable": True,
            "config_managed": True,
            "client_installed": True,
        }
        with (
            mock.patch(
                "pcl_codex_bridge.remote_clients.load_registry",
                return_value={"gateway": "http://selected-relay.tail.test:15722/v1"},
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.discover_relays",
                return_value=relay_report,
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.ssh_inventory",
                return_value=[{"target": "pcl-direct", "hostname": "pcl-direct", "user": "root", "port": 22}],
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.remote_client_status",
                return_value=status,
            ),
        ):
            result = discover_remote_clients(timeout=2)
        node = next(item for item in result["nodes"] if item["node_name"] == "pcl-direct")
        self.assertTrue(node["feasibility"]["direct"])
        self.assertTrue(node["feasibility"]["pcl_network_reachable"])
        self.assertEqual(node["feasibility"]["recommended_route"], "local_pcl_direct")
        self.assertTrue(
            any(
                edge["from"] == "pcl-api"
                and edge["to"] == "100.64.0.10"
                and edge["type"] == "local_pcl_direct"
                for edge in result["recommendation"]["edges"]
            )
        )

    def test_tailnet_peer_uses_shared_heartbeat_without_local_ssh(self):
        relay_report = {
            "checked_at": "2026-09-02T19:20:00+0800",
            "nodes": [
                {
                    "node_name": "relay",
                    "magic_dns": "relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 10,
                    "selected": True,
                },
                {
                    "node_name": "peer-mac",
                    "magic_dns": "peer-mac.tail.test",
                    "tailscale_ip": "100.64.0.11",
                    "online": True,
                    "self": False,
                    "gateway": False,
                    "pcl_auth": "not_checked",
                    "model_count": 0,
                    "latency_ms": 20,
                    "selected": False,
                },
            ],
        }
        with (
            mock.patch(
                "pcl_codex_bridge.remote_clients.load_registry",
                return_value={"gateway": "http://relay.tail.test:15722/v1"},
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.discover_relays",
                return_value=relay_report,
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients._shared_topology_reports",
                return_value={
                    "100.64.0.11": {
                        "node_id": "100.64.0.11",
                        "node_name": "peer-mac",
                        "client_version": __version__,
                        "relay_reachable": True,
                        "relay_latency_ms": 21,
                        "client_ready": True,
                        "config_managed": True,
                        "native_v2": True,
                        "native_roles": True,
                        "pcl_direct": False,
                        "round_id": 42,
                    }
                },
            ),
            mock.patch("pcl_codex_bridge.remote_clients.ssh_inventory", return_value=[]),
        ):
            result = discover_remote_clients(timeout=2)
        peer = next(item for item in result["nodes"] if item["node_name"] == "peer-mac")
        self.assertEqual(peer["feasibility"]["recommended_route"], "direct")
        self.assertTrue(peer["feasibility"]["direct_verified"])
        self.assertTrue(peer["feasibility"]["client_direct_verified"])
        self.assertTrue(peer["client_status"]["consensus_reported"])
        edge = next(item for item in result["recommendation"]["edges"] if item["to"] == "100.64.0.11")
        self.assertEqual(edge["type"], "direct")
        self.assertTrue(edge["verified"])

    def test_healthy_gateway_proves_pcl_upstream_without_local_ssh(self):
        relay_report = {
            "checked_at": "2026-09-02T19:24:00+0800",
            "nodes": [
                {
                    "node_name": "selected-relay",
                    "magic_dns": "selected-relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 10,
                    "selected": True,
                },
                {
                    "node_name": "direct-pcl-gateway",
                    "magic_dns": "direct-pcl.tail.test",
                    "tailscale_ip": "100.64.0.13",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 20,
                    "selected": False,
                },
            ],
        }
        with (
            mock.patch(
                "pcl_codex_bridge.remote_clients.load_registry",
                return_value={"gateway": "http://selected-relay.tail.test:15722/v1"},
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.discover_relays",
                return_value=relay_report,
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients._shared_topology_reports",
                return_value={
                    "100.64.0.13": {
                        "node_id": "100.64.0.13",
                        "node_name": "direct-pcl-gateway",
                        "client_version": __version__,
                        "relay_reachable": False,
                        "client_ready": True,
                        "config_managed": True,
                        "native_v2": True,
                        "native_roles": True,
                        "pcl_direct": True,
                        "round_id": 42,
                    }
                },
            ),
            mock.patch("pcl_codex_bridge.remote_clients.ssh_inventory", return_value=[]),
        ):
            result = discover_remote_clients(timeout=2)
        gateway = next(item for item in result["nodes"] if item["node_name"] == "direct-pcl-gateway")
        self.assertEqual(gateway["feasibility"]["recommended_route"], "local_pcl_direct")
        edge = next(item for item in result["recommendation"]["edges"] if item["to"] == "100.64.0.13")
        self.assertEqual(edge["type"], "local_pcl_direct")
        self.assertTrue(edge["verified"])

    def test_healthy_selected_relay_is_stable_even_when_another_probe_is_faster(self):
        relay_report = {
            "checked_at": "2026-09-02T19:25:00+0800",
            "nodes": [
                {
                    "node_name": "selected-relay",
                    "magic_dns": "selected-relay.tail.test",
                    "tailscale_ip": "100.64.0.8",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 80,
                    "selected": True,
                },
                {
                    "node_name": "transiently-faster-relay",
                    "magic_dns": "fast.tail.test",
                    "tailscale_ip": "100.64.0.12",
                    "online": True,
                    "self": False,
                    "gateway": True,
                    "pcl_auth": "valid",
                    "model_count": 13,
                    "latency_ms": 5,
                    "selected": False,
                },
            ],
        }
        with (
            mock.patch(
                "pcl_codex_bridge.remote_clients.load_registry",
                return_value={"gateway": "http://selected-relay.tail.test:15722/v1"},
            ),
            mock.patch(
                "pcl_codex_bridge.remote_clients.discover_relays",
                return_value=relay_report,
            ),
            mock.patch("pcl_codex_bridge.remote_clients.ssh_inventory", return_value=[]),
        ):
            result = discover_remote_clients(timeout=2)
        self.assertEqual(result["recommendation"]["relay_id"], "100.64.0.8")
        self.assertEqual(result["recommendation"]["relay_name"], "selected-relay")

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
