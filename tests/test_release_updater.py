import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.release_updater import (
    MAC_ASSET_NAME,
    _expected_digest,
    _version_tuple,
    latest_release_status,
)


class ReleaseUpdaterTests(unittest.TestCase):
    def test_version_comparison_ignores_v_prefix_and_prerelease_suffix(self):
        self.assertGreater(_version_tuple("v2.2.0"), _version_tuple("2.1.9"))
        self.assertEqual(_version_tuple("2.2.0-beta.1"), (2, 2, 0))

    def test_latest_release_reports_installable_macos_asset(self):
        release = {
            "tag_name": "v2.2.0",
            "html_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.2.0",
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [
                {
                    "name": MAC_ASSET_NAME,
                    "browser_download_url": "https://example.test/app.zip",
                    "size": 42,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
        with mock.patch("pcl_codex_bridge.release_updater._read_json", return_value=release):
            result = latest_release_status("2.1.0")
        self.assertTrue(result["available"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "2.2.0")
        self.assertEqual(result["asset_size"], 42)

    def test_latest_release_fails_closed_without_expected_asset(self):
        with mock.patch(
            "pcl_codex_bridge.release_updater._read_json",
            return_value={"tag_name": "v2.2.0", "assets": []},
        ):
            result = latest_release_status("2.1.0")
        self.assertFalse(result["available"])
        self.assertIn(MAC_ASSET_NAME, result["error"])

    def test_digest_prefers_github_asset_digest_without_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("pcl_codex_bridge.release_updater._download") as download:
                result = _expected_digest(
                    {"asset_digest": "sha256:" + "B" * 64, "checksum_url": ""},
                    Path(temporary),
                )
        self.assertEqual(result, "b" * 64)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
