import tempfile
import unittest
import hashlib
import os
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest import mock

from pcl_codex_bridge.release_updater import (
    LINUX_AARCH64_ASSET_NAME,
    LINUX_X86_64_ASSET_NAME,
    MAC_ASSET_NAME,
    RELEASE_API,
    RELEASE_METADATA_URL,
    _latest_release_metadata,
    _verify_linux_bundle,
    _expected_digest,
    _version_tuple,
    cache_release_asset,
    latest_release_status,
    promote_verified_release,
    release_asset_name,
    verified_cached_release,
)


class ReleaseUpdaterTests(unittest.TestCase):
    def test_linux_upgrade_uses_new_bundles_pin_not_running_updaters_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {
                "pcl-codex": "", "install.sh": "", "pcl_codex_bridge/VERSION": "9.0.0",
                "pcl_codex_bridge/opencodex_sidecar.py": 'OPENCODEX_COMMIT = "' + "a" * 40 + '"\nOPENCODEX_VERSION = "9.1.0"\nraise RuntimeError("must never execute")',
                "opencodex/UPSTREAM.json": json.dumps({"commit": "a" * 40, "version": "9.1.0"}),
                "opencodex/package.json": json.dumps({"version": "9.1.0"}),
                "opencodex/src/cli/index.ts": "", "opencodex/bin/bun": "",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            with mock.patch("pcl_codex_bridge.release_updater.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "1.3.14\n", "")):
                _verify_linux_bundle(root, "9.0.0")
                (root / "opencodex/package.json").write_text('{"version":"9.2.0"}')
                with self.assertRaisesRegex(RuntimeError, "unexpected OpenCodex version"):
                    _verify_linux_bundle(root, "9.0.0")

    def test_rate_limit_falls_back_to_public_release_metadata_without_credentials(self):
        release = {"tag_name": "v2.5.14", "assets": [{
            "name": MAC_ASSET_NAME,
            "browser_download_url": "https://github.com/LossInWind/PCL-Relay/releases/download/v2.5.14/" + MAC_ASSET_NAME,
            "size": 42,
        }]}
        error = urllib.error.HTTPError(RELEASE_API, 403, "rate limit exceeded", {}, None)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "pcl_codex_bridge.release_updater._read_json", side_effect=[error, release]
        ) as read:
            self.assertEqual(_latest_release_metadata(), release)
            self.assertEqual(read.call_args_list, [mock.call(RELEASE_API), mock.call(RELEASE_METADATA_URL)])

    def test_static_metadata_rejects_foreign_download_url(self):
        error = urllib.error.HTTPError(RELEASE_API, 429, "rate limit", {}, None)
        release = {"tag_name": "v2.5.14", "assets": [{"name": MAC_ASSET_NAME, "browser_download_url": "https://other.test/app.zip"}]}
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "pcl_codex_bridge.release_updater._read_json", side_effect=[error, release]
        ), self.assertRaisesRegex(RuntimeError, "public release metadata unavailable"):
            _latest_release_metadata()

    def test_custom_release_source_does_not_silently_fall_back(self):
        error = urllib.error.HTTPError("https://custom.test", 403, "denied", {}, None)
        with mock.patch.dict(os.environ, {"PCL_RELAY_RELEASE_API": "https://custom.test"}), mock.patch(
            "pcl_codex_bridge.release_updater._read_json", side_effect=error
        ) as read, self.assertRaises(urllib.error.HTTPError):
            _latest_release_metadata()
        read.assert_called_once_with("https://custom.test")

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
        with (
            mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value=MAC_ASSET_NAME),
            mock.patch("pcl_codex_bridge.release_updater._read_json", return_value=release),
        ):
            result = latest_release_status("2.1.0")
        self.assertTrue(result["available"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "2.2.0")
        self.assertEqual(result["asset_size"], 42)

    def test_latest_release_fails_closed_without_expected_asset(self):
        with (
            mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value=MAC_ASSET_NAME),
            mock.patch(
                "pcl_codex_bridge.release_updater._read_json",
                return_value={"tag_name": "v2.2.0", "assets": []},
            ),
        ):
            result = latest_release_status("2.1.0")
        self.assertFalse(result["available"])
        self.assertIn(MAC_ASSET_NAME, result["error"])

    def test_local_version_newer_than_release_blocks_topology_deployment(self):
        release = {
            "tag_name": "v2.5.4",
            "html_url": "https://example.test/v2.5.4",
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [{
                "name": MAC_ASSET_NAME,
                "browser_download_url": "https://example.test/app.zip",
                "size": 42,
                "digest": "sha256:" + "a" * 64,
            }],
        }
        with (
            mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value=MAC_ASSET_NAME),
            mock.patch("pcl_codex_bridge.release_updater._read_json", return_value=release),
        ):
            result = latest_release_status("2.5.7")
        self.assertTrue(result["local_newer_than_published"])
        self.assertFalse(result["topology_deployment_ready"])

    def test_digest_prefers_github_asset_digest_without_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("pcl_codex_bridge.release_updater._download") as download:
                result = _expected_digest(
                    {"asset_digest": "sha256:" + "B" * 64, "checksum_url": ""},
                    Path(temporary),
                )
        self.assertEqual(result, "b" * 64)
        download.assert_not_called()

    def test_release_asset_is_selected_by_platform_and_architecture(self):
        self.assertEqual(release_asset_name("darwin", "arm64"), MAC_ASSET_NAME)
        self.assertEqual(release_asset_name("linux", "x86_64"), LINUX_X86_64_ASSET_NAME)
        self.assertEqual(release_asset_name("linux", "aarch64"), LINUX_AARCH64_ASSET_NAME)

    def test_unknown_linux_architecture_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported Linux architecture"):
            release_asset_name("linux", "riscv64")

    def test_verified_cache_hit_does_not_download_again(self):
        payload = b"verified release bytes"
        digest = hashlib.sha256(payload).hexdigest()
        status = {
            "latest_version": "2.6.0",
            "asset_name": MAC_ASSET_NAME,
            "asset_size": len(payload),
            "asset_digest": "sha256:" + digest,
            "asset_url": "https://github.com/LossInWind/PCL-Relay/releases/download/v2.6.0/app.zip",
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"PCL_RELAY_RELEASE_CACHE": temporary}
        ):
            part = Path(temporary) / "part"
            part.write_bytes(payload)
            promote_verified_release(status, part, digest, len(payload))
            with mock.patch("pcl_codex_bridge.release_updater._download") as download:
                result = cache_release_asset(status)
            self.assertFalse(result["downloaded"])
            download.assert_not_called()

    def test_unverified_or_tampered_cache_is_never_returned(self):
        payload = b"release"
        digest = hashlib.sha256(payload).hexdigest()
        status = {"latest_version": "2.6.0", "asset_name": MAC_ASSET_NAME}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"PCL_RELAY_RELEASE_CACHE": temporary}
        ):
            archive = Path(temporary) / "2.6.0" / MAC_ASSET_NAME
            archive.parent.mkdir(parents=True)
            archive.write_bytes(payload)
            with self.assertRaises(FileNotFoundError):
                verified_cached_release("2.6.0", MAC_ASSET_NAME)
            part = Path(temporary) / "part"
            part.write_bytes(payload)
            promote_verified_release(status, part, digest, len(payload))
            archive.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "no longer matches|does not match"):
                verified_cached_release("2.6.0", MAC_ASSET_NAME)

    def test_peer_promotion_rejects_wrong_digest_and_size(self):
        payload = b"release"
        status = {"latest_version": "2.6.0", "asset_name": MAC_ASSET_NAME}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"PCL_RELAY_RELEASE_CACHE": temporary}
        ):
            part = Path(temporary) / "part"
            part.write_bytes(payload)
            with self.assertRaisesRegex(RuntimeError, "size"):
                promote_verified_release(status, part, "a" * 64, len(payload) + 1)
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                promote_verified_release(status, part, "a" * 64, len(payload))


if __name__ == "__main__":
    unittest.main()
