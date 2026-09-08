import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import sync_service


class SyncServiceTests(unittest.TestCase):
    def test_macos_service_is_independent_of_model_data_plane(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launch_agent = root / "LaunchAgents" / "relay-sync.plist"
            state_root = root / "state"
            service_config = root / "config" / "sync-service.json"
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with (
                mock.patch.object(sync_service.sys, "platform", "darwin"),
                mock.patch.object(sync_service, "LAUNCH_AGENT", launch_agent),
                mock.patch.object(sync_service, "STATE_ROOT", state_root),
                mock.patch.object(sync_service, "SERVICE_CONFIG", service_config),
                mock.patch.object(sync_service, "_launcher", return_value=["/tmp/pcl-codex"]),
                mock.patch.object(sync_service.subprocess, "run", return_value=completed),
            ):
                result = sync_service.install_sync_service(port=25726, interval=30)
            with launch_agent.open("rb") as handle:
                payload = plistlib.load(handle)
        arguments = payload["ProgramArguments"]
        self.assertEqual(arguments[:4], ["/tmp/pcl-codex", "sync", "serve", "--host"])
        self.assertIn("25726", arguments)
        self.assertNotIn("15724", arguments)
        self.assertFalse(result["model_data_plane_restarted"])


if __name__ == "__main__":
    unittest.main()
