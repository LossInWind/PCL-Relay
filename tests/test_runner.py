import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import runner


class RunnerTests(unittest.TestCase):
    def test_non_git_snapshot_reports_created_modified_and_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "modified.txt").write_text("old", encoding="utf-8")
            (root / "deleted.txt").write_text("gone", encoding="utf-8")
            before = runner._snapshot_files(root)
            (root / "modified.txt").write_text("new content", encoding="utf-8")
            (root / "deleted.txt").unlink()
            (root / "created.txt").write_text("new", encoding="utf-8")
            after = runner._snapshot_files(root)
        self.assertEqual(
            runner._changed_files(before, after),
            ["created: created.txt", "deleted: deleted.txt", "modified: modified.txt"],
        )

    def test_read_only_delegate_does_not_take_write_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            process = subprocess.CompletedProcess([], 0, stdout='{"type":"item.completed"}\n', stderr="")
            with (
                mock.patch.object(runner, "find_codex", return_value="/usr/bin/codex"),
                mock.patch.object(runner, "load_registry", return_value={}),
                mock.patch.object(runner, "_is_git_repository", return_value=False),
                mock.patch.object(runner, "_snapshot_files", return_value={}),
                mock.patch.object(runner.subprocess, "run", return_value=process),
                mock.patch.object(runner.fcntl, "flock") as flock,
            ):
                result = runner.delegate(
                    "pcl_deepseek_flash", "inspect only", temp, execution_mode="read-only"
                )
        self.assertEqual(result["returncode"], 0)
        flock.assert_not_called()

    def test_write_timeout_releases_lock_and_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            timeout = subprocess.TimeoutExpired(["codex"], 30, output="partial", stderr="stopped")
            with (
                mock.patch.object(runner, "find_codex", return_value="/usr/bin/codex"),
                mock.patch.object(runner, "load_registry", return_value={}),
                mock.patch.object(runner, "_is_git_repository", return_value=True),
                mock.patch.object(runner, "_git", return_value=" M existing.txt\n"),
                mock.patch.object(runner.subprocess, "run", side_effect=timeout),
                mock.patch.object(runner.fcntl, "flock") as flock,
            ):
                result = runner.delegate("pcl_deepseek_flash", "edit", temp, timeout=30)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["returncode"], 124)
        self.assertIn("stopped", result["stderr_tail"])
        self.assertEqual(flock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
