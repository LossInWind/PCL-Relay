import unittest
from unittest import mock

from pcl_codex_bridge import cli


class ServerCliTests(unittest.TestCase):
    def test_status_uses_tailnet_admin_endpoint(self):
        with mock.patch("pcl_codex_bridge.cli.request_json", return_value={"status": "active"}) as request:
            value = cli.server_status("http://relay.tailnet:15722/v1")
        self.assertEqual(value["status"], "active")
        request.assert_called_once_with("http://relay.tailnet:15722/admin/status", timeout=15)

    def test_restart_waits_for_new_gateway_pid(self):
        with (
            mock.patch("pcl_codex_bridge.cli.server_status", side_effect=[{"pid": 10}, {"status": "active", "pid": 11}]),
            mock.patch("pcl_codex_bridge.cli.request_json", return_value={"status": "restarting"}),
            mock.patch("pcl_codex_bridge.cli.time.sleep"),
        ):
            result = cli.server_restart("http://relay.tailnet:15722/v1")
        self.assertEqual(result["before_pid"], 10)
        self.assertEqual(result["status"]["pid"], 11)


if __name__ == "__main__":
    unittest.main()
