import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import deployment, topology_sync
from pcl_codex_bridge.runtime_snapshot import runtime_snapshot


class RoutingDashboardTests(unittest.TestCase):
    def test_first_status_read_does_not_register_or_save(self):
        with mock.patch.object(topology_sync, "load_registry", return_value={}), mock.patch.object(topology_sync, "save_registry") as save:
            first = topology_sync.list_peers()
            second = topology_sync.list_peers()
        save.assert_not_called()
        self.assertEqual(first["node_id"], "")
        self.assertEqual(first["node_id"], second["node_id"])

    def test_single_device_probe_never_connects_other_targets(self):
        targets = [{"id": "a"}, {"id": "b"}]
        with mock.patch.object(deployment, "_load_targets", return_value=targets), mock.patch.object(deployment, "probe_target", side_effect=lambda row, timeout: row) as probe:
            self.assertEqual(deployment.list_targets(True, 2, "a")["targets"], [{"id": "a"}])
            probe.assert_called_once_with({"id": "a"}, 2)

    def test_peer_probe_keeps_registration_id_and_reports_node_identity(self):
        registry = {"gateway": "http://relay:15722/v1"}
        topology_sync._sync_metadata(registry)
        peer = {"id": "registered", "name": "Peer", "url": "http://peer:15726", "token_file": ""}
        with mock.patch.object(topology_sync, "load_registry", return_value=registry), mock.patch.object(topology_sync, "save_registry"), mock.patch.object(topology_sync, "_peer_records", return_value=[peer]), mock.patch.object(topology_sync, "heartbeat", return_value={"node_id": "actual", "version": "2.5.10"}) as probe:
            result = topology_sync.list_peers(True, 2, "registered")["peers"][0]
        self.assertEqual(result["id"], "registered")
        self.assertEqual(result["node_id"], "actual")
        self.assertIsNone(result["runtime"])
        probe.assert_called_once()

    def test_missing_runtime_is_unknown_not_installed_version(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(runtime_snapshot(temp)["opencodex_version"])

    def test_runtime_snapshot_is_read_only_and_does_not_export_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / ".config/pcl-codex-bridge/opencodex/config.json"
            config.parent.mkdir(parents=True)
            original = json.dumps({"providers": {"pcl": {"baseUrl": "http://relay:15722/v1", "apiKey": "DO_NOT_EXPORT"}}, "port": 15725})
            config.write_text(original)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"status":"ok","service":"opencodex","version":"2.46.0"}'
            with mock.patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = response
                result = runtime_snapshot(home)
            self.assertEqual(config.read_text(), original)
            self.assertNotIn("DO_NOT_EXPORT", json.dumps(result))
            self.assertEqual(result["opencodex_version"], "2.46.0")
            self.assertFalse(result["model_call_verified"])
            opener.return_value.open.assert_called_once_with("http://127.0.0.1:15725/healthz", timeout=2)

    def test_credential_url_is_not_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / ".config/pcl-codex-bridge/opencodex/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"port": 0, "providers": {"pcl": {"baseUrl": "http://user:SECRET@host/v1"}}}))
            self.assertNotIn("SECRET", json.dumps(runtime_snapshot(temp)))

    def test_refresh_path_does_not_stage_or_apply_configuration(self):
        root = Path(__file__).resolve().parents[1] / "macos/Sources/PCLCodexManager"
        app = (root / "AppModel.swift").read_text()
        refresh = app.split("func refreshAll()", 1)[1].split("func copyGatewayURL", 1)[0]
        self.assertNotIn("bootstrapClientIfNeeded", refresh)
        source = (root / "State/AppModel+RoutingDashboard.swift").read_text()
        refresh = source.split("func checkRoutingDashboard()", 1)[1].split("func checkRoutingDevice", 1)[0]
        for mutation in ("synchronizeRelayTopology", "selectGatewayRoute", "install", "restart"):
            self.assertNotIn(mutation, refresh)


if __name__ == "__main__":
    unittest.main()
