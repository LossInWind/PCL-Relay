import fcntl
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pcl_codex_bridge import catalog_refresh as catalog


class CatalogRefreshTests(unittest.TestCase):
    def setUp(self):
        roles = patch("pcl_codex_bridge.agent_catalog.sync_catalog_roles", return_value={"reload_required": False})
        roles.start()
        self.addCleanup(roles.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.runtime = patch.object(catalog, "installed_runtime", return_value=SimpleNamespace(version="test", commit="abc"))
        self.identity = patch.object(catalog, "_identity", return_value="same")
        self.runtime.start()
        self.identity.start()
        self.addCleanup(self.runtime.stop)
        self.addCleanup(self.identity.stop)

    def run_refresh(self, data, **kwargs):
        with patch.object(catalog, "invoke_sidecar", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as call:
            result = catalog.refresh_catalog(home=self.home, **kwargs)
            return result, call

    def test_upstream_catalog_only_and_success_ttl(self):
        result, call = self.run_refresh({"status": "success", "catalog_written": True}, now=10000)
        script = call.call_args.args[1][1]
        self.assertIn("refreshCodexModelCatalog", script)
        for forbidden in ("injectCodex", "syncModelsToCodex", "restart", "auth.json"):
            self.assertNotIn(forbidden, script)
        self.assertEqual(result["last_success"], 10000)
        _, call = self.run_refresh({}, now=10001, if_due=True)
        call.assert_not_called()

    def test_failure_retains_success_and_has_bounded_retry(self):
        self.run_refresh({"status": "success"}, now=10000)
        with patch.object(catalog, "invoke_sidecar", side_effect=subprocess.TimeoutExpired("hidden", 120)):
            result = catalog.refresh_catalog(home=self.home, now=14000)
        self.assertEqual(result["last_success"], 10000)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "TimeoutExpired")
        _, call = self.run_refresh({}, now=14001, if_due=True)
        call.assert_not_called()

    def test_changed_runtime_bypasses_ttl(self):
        self.run_refresh({"status": "success"}, now=10000)
        with patch.object(catalog, "_identity", return_value="new"):
            _, call = self.run_refresh({"status": "success"}, now=10001, if_due=True)
            call.assert_called_once()

    def test_concurrent_refresh_never_runs_twice(self):
        with (self.home / "relay-catalog-refresh.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result, call = self.run_refresh({}, now=10000)
            self.assertEqual(result["status"], "busy")
            call.assert_not_called()

    def test_disabled_is_not_reported_as_success(self):
        result, _ = self.run_refresh({"status": "disabled"}, now=10000)
        self.assertNotIn("last_success", result)
        self.assertEqual(result["status"], "disabled")

    def test_malformed_result_and_stderr_are_not_exposed(self):
        with patch.object(catalog, "invoke_sidecar", return_value=subprocess.CompletedProcess([], 1, "", "secret-value")):
            result = catalog.refresh_catalog(home=self.home, now=10000)
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(result["status"], "failed")

    def test_passive_status_does_not_create_files(self):
        self.assertEqual(catalog.catalog_status(self.home)["status"], "never_checked")
        self.assertEqual(list(self.home.iterdir()), [])
