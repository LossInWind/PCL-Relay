import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import topology_sync


def digest(document):
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def release_assets(version="2.6.0", payload=b"release"):
    checksum = hashlib.sha256(payload).hexdigest()
    names = (
        "PCL-Relay-macOS.zip",
        "PCL-Relay-linux-x86_64.tar.gz",
        "PCL-Relay-linux-aarch64.tar.gz",
    )
    return {
        name: {
            "asset_name": name,
            "asset_url": f"https://github.com/LossInWind/PCL-Relay/releases/download/v{version}/{name}",
            "asset_size": len(payload),
            "asset_sha256": checksum,
        }
        for name in names
    }


class TopologySyncTests(unittest.TestCase):
    def remote_envelope(self):
        document = {
            "schema": 1,
            "gateway": "http://relay-b:15722/v1",
            "gateways": [
                {"id": "b", "name": "Relay B", "url": "http://relay-b:15722/v1", "added_at": ""}
            ],
            "selected_agents": ["pcl_glm"],
            "agent_definitions": {"pcl_glm": {"model": "GLM-5.2", "description": "GLM"}},
        }
        return {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "schema": 1,
            "node_id": "remote",
            "revision": {"counter": 2, "origin": "remote"},
            "digest": digest(document),
            "topology": document,
        }

    def test_change_revision_is_monotonic_and_owned_by_local_node(self):
        registry = {}
        topology_sync.mark_topology_changed(registry)
        first = registry["relay_sync"]["revision"]
        topology_sync.mark_topology_changed(registry)
        second = registry["relay_sync"]["revision"]
        self.assertEqual(second["counter"], first["counter"] + 1)
        self.assertEqual(second["origin"], registry["relay_sync"]["node_id"])

    def test_peer_token_path_is_local_and_not_returned_or_synchronized(self):
        registry = {
            "gateway": "http://relay:15722/v1",
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [{"id": "peer", "name": "Linux", "url": "http://linux:15726", "token_file": "/secret/token"}],
            },
        }
        with mock.patch.object(topology_sync, "load_registry", return_value=registry):
            status = topology_sync.list_peers()
            envelope = topology_sync.topology_envelope(registry)
        self.assertNotIn("token_file", status["peers"][0])
        self.assertNotIn("relay_sync", envelope["topology"])
        self.assertNotIn("/secret/token", json.dumps(envelope))

    def test_add_peer_requires_protocol_heartbeat_before_saving(self):
        registry = {}
        heartbeat = {
            "status": "ok",
            "protocol": topology_sync.SYNC_PROTOCOL,
            "node_id": "linux",
            "node_name": "Linux",
        }
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / "token"
            token.write_text("secret", encoding="utf-8")
            token.chmod(0o600)
            with (
                mock.patch.object(topology_sync, "heartbeat", return_value=heartbeat) as probe,
                mock.patch.object(topology_sync, "load_registry", return_value=registry),
                mock.patch.object(topology_sync, "save_registry") as save,
            ):
                result = topology_sync.add_peer("http://linux:15726", "Server", str(token))
        probe.assert_called_once()
        self.assertTrue(result["added"])
        self.assertEqual(save.call_args.args[0]["relay_sync"]["peers"][0]["id"], "linux")

    def test_token_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / "token"
            token.write_text("secret", encoding="utf-8")
            token.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "mode 600 or 400"):
                topology_sync._read_token(str(token))
            token.chmod(0o600)
            self.assertEqual(topology_sync._read_token(str(token)), "secret")

    def test_default_sync_transport_accepts_only_loopback_or_tailnet_sources(self):
        self.assertTrue(topology_sync._trusted_sync_source("127.0.0.1"))
        self.assertTrue(topology_sync._trusted_sync_source("100.123.137.49"))
        self.assertTrue(topology_sync._trusted_sync_source("fd7a:115c:a1e0::1"))
        self.assertFalse(topology_sync._trusted_sync_source("192.168.1.20"))
        self.assertFalse(topology_sync._trusted_sync_source("203.0.113.20"))

    def test_apply_rejects_bad_digest_before_runtime_or_registry_change(self):
        document = {
            "schema": 1,
            "gateway": "http://relay:15722/v1",
            "gateways": [],
            "selected_agents": [],
            "agent_definitions": {},
        }
        envelope = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "schema": 1,
            "revision": {"counter": 2, "origin": "remote"},
            "digest": "wrong",
            "topology": document,
        }
        with mock.patch.object(topology_sync, "save_registry") as save:
            with self.assertRaisesRegex(RuntimeError, "digest"):
                topology_sync.apply_topology_envelope(envelope)
        save.assert_not_called()

    def test_newer_topology_applies_gateway_and_models_without_running_sidecar(self):
        local = {
            "gateway": "http://relay-a:15722/v1",
            "agent_definitions": {"pcl_glm": {"model": "GLM-Old", "description": "old"}},
            "selected_agents": ["pcl_glm"],
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [],
            },
        }
        check = {"models": [{"id": "GLM-5.2"}]}
        with (
            mock.patch.object(topology_sync, "load_registry", return_value=local),
            mock.patch("pcl_codex_bridge.routes.probe_gateway", return_value=check),
            mock.patch("pcl_codex_bridge.opencodex_sidecar.installed_runtime", side_effect=FileNotFoundError()),
            mock.patch.object(topology_sync, "save_registry") as save,
        ):
            result = topology_sync.apply_topology_envelope(self.remote_envelope())
        self.assertTrue(result["applied"])
        self.assertEqual(save.call_args.args[0]["gateway"], "http://relay-b:15722/v1")
        self.assertEqual(save.call_args.args[0]["agent_definitions"]["pcl_glm"]["model"], "GLM-5.2")

    def test_live_sync_failure_rolls_back_provider_and_does_not_commit(self):
        local = {
            "gateway": "http://relay-a:15722/v1",
            "agent_definitions": {"pcl_glm": {"model": "GLM-Old", "description": "old"}},
            "selected_agents": ["pcl_glm"],
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [],
            },
        }
        runtime = mock.MagicMock()
        with (
            mock.patch.object(topology_sync, "load_registry", return_value=local),
            mock.patch("pcl_codex_bridge.routes.probe_gateway", return_value={"models": [{"id": "GLM-5.2"}]}),
            mock.patch("pcl_codex_bridge.opencodex_sidecar.installed_runtime", return_value=runtime),
            mock.patch("pcl_codex_bridge.opencodex_sidecar.sidecar_health", return_value={"ok": True}),
            mock.patch(
                "pcl_codex_bridge.opencodex_sidecar.switch_pcl_gateway",
                side_effect=[RuntimeError("rejected"), {"configured": True}],
            ) as switch,
            mock.patch.object(topology_sync, "save_registry") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "rolled back"):
                topology_sync.apply_topology_envelope(self.remote_envelope())
        self.assertEqual(switch.call_count, 2)
        self.assertEqual(switch.call_args_list[1].args[1], "http://relay-a:15722/v1")
        save.assert_not_called()

    def test_release_offer_is_verified_against_peer_release_index(self):
        payload = b"mac release"
        checksum = hashlib.sha256(payload).hexdigest()
        offer = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.6.0",
            "assets": release_assets(payload=payload),
        }
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "asset.zip"
            archive.write_bytes(payload)
            with (
                mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value="PCL-Relay-macOS.zip"),
                mock.patch.object(topology_sync, "load_registry", return_value={}),
                mock.patch.object(topology_sync, "save_registry"),
                mock.patch(
                    "pcl_codex_bridge.release_updater.cache_release_asset",
                    return_value={"path": str(archive), "sha256": checksum, "size": len(payload)},
                ) as download,
                mock.patch(
                    "pcl_codex_bridge.release_updater.install_cached_release",
                return_value={
                    "installed": True,
                    "staged": True,
                    "restart_required": False,
                    "service_restarted": False,
                    "codex_config_changed": False,
                    },
                ) as install,
                mock.patch.object(topology_sync, "fetch_release_from_peers") as fallback,
            ):
                result = topology_sync.install_release_offer(offer)
        download.assert_called_once()
        fallback.assert_not_called()
        install.assert_called_once_with(mock.ANY, archive, checksum)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["artifact_source"], "github-release")
        self.assertFalse(result["service_restarted"])
        self.assertFalse(result["codex_config_changed"])

    def test_push_sends_only_release_offer_and_peer_downloads_its_asset(self):
        registry = {
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [{"id": "peer", "name": "Linux", "url": "http://linux:15726", "token_file": "/local/token"}],
            }
        }
        latest = {
            "available": True,
            "latest_version": "2.6.0",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.test/releases/v2.6.0",
            "assets": release_assets(),
        }
        with (
            mock.patch.object(topology_sync, "load_registry", return_value=registry),
            mock.patch.object(topology_sync, "save_registry"),
            mock.patch("pcl_codex_bridge.release_updater.latest_release_manifest", return_value=latest),
            mock.patch.object(
                topology_sync,
                "_request",
                return_value={"accepted": True, "installed": True},
            ) as request,
        ):
            result = topology_sync.push_latest_release()
        payload = request.call_args.args[2]
        self.assertEqual(payload["version"], "2.6.0")
        self.assertEqual(payload["origin_node_id"], "local")
        self.assertIn("PCL-Relay-macOS.zip", payload["assets"])
        self.assertEqual(result["artifact_transfer"], "github-first-then-tailnet-peer-cache")
        self.assertEqual(result["ok_count"], 1)

    def test_github_failure_falls_back_to_peer_before_install(self):
        payload = b"mac release"
        checksum = hashlib.sha256(payload).hexdigest()
        offer = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.6.0",
            "assets": release_assets(payload=payload),
        }
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "asset.zip"
            archive.write_bytes(payload)
            with (
                mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value="PCL-Relay-macOS.zip"),
                mock.patch.object(topology_sync, "load_registry", return_value={}),
                mock.patch.object(topology_sync, "save_registry"),
                mock.patch(
                    "pcl_codex_bridge.release_updater.cache_release_asset",
                    side_effect=TimeoutError("github unavailable"),
                ),
                mock.patch.object(
                    topology_sync,
                    "fetch_release_from_peers",
                    return_value={"path": str(archive), "source": "tailnet-peer-cache"},
                ) as fallback,
                mock.patch(
                    "pcl_codex_bridge.release_updater.install_cached_release",
                    return_value={"installed": True, "staged": False},
                ) as install,
            ):
                result = topology_sync.install_release_offer(offer)
        fallback.assert_called_once()
        install.assert_called_once_with(mock.ANY, archive, checksum)
        self.assertEqual(result["artifact_source"], "tailnet-peer-cache")
        self.assertIn("github unavailable", result["github_error"])

    def test_release_offer_rejects_platform_mismatch_and_bad_digest(self):
        base = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "assets": {},
        }
        with mock.patch("pcl_codex_bridge.release_updater.release_asset_name", return_value="PCL-Relay-macOS.zip"):
            with self.assertRaisesRegex(RuntimeError, "exactly the three"):
                topology_sync.install_release_offer(base)
            base["source"] = "github:LossInWind/PCL-Relay"
            base["assets"] = release_assets()
            base["assets"]["PCL-Relay-macOS.zip"]["asset_sha256"] = "not-a-digest"
            with self.assertRaisesRegex(RuntimeError, "digest is invalid"):
                topology_sync.install_release_offer(base)

    def test_peer_download_streams_and_promotes_only_matching_asset(self):
        payload = b"peer release" * 1024
        checksum = hashlib.sha256(payload).hexdigest()
        status = {
            "latest_version": "2.6.0",
            "asset_name": "PCL-Relay-macOS.zip",
            "asset_size": len(payload),
            "asset_digest": "sha256:" + checksum,
        }

        class Response(io.BytesIO):
            def __init__(self, data, headers):
                super().__init__(data)
                self.headers = headers

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        response = Response(payload, {"X-PCL-SHA256": checksum, "Content-Length": str(len(payload))})
        opener = mock.MagicMock()
        opener.open.return_value = response
        registry = {
            "relay_sync": {
                "peers": [{"id": "peer", "name": "Peer", "url": "http://100.64.0.2:15726", "token_file": ""}]
            }
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(os.environ, {"PCL_RELAY_RELEASE_CACHE": temporary}),
            mock.patch.object(topology_sync, "load_registry", return_value=registry),
            mock.patch("urllib.request.build_opener", return_value=opener),
        ):
            result = topology_sync.fetch_release_from_peers(status)
            self.assertEqual(Path(result["path"]).read_bytes(), payload)
        self.assertEqual(result["source"], "tailnet-peer-cache")
        self.assertEqual(result["sha256"], checksum)

    def test_release_campaign_is_persistent_and_rejects_same_version_conflict(self):
        registry = {}
        offer = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.6.0",
            "assets": release_assets(),
        }
        with (
            mock.patch.object(topology_sync, "load_registry", return_value=registry),
            mock.patch.object(topology_sync, "save_registry") as save,
        ):
            stored = topology_sync.remember_release_offer(offer)
            document = topology_sync.release_campaign_document()
            repeated = topology_sync.remember_release_offer(offer)
            same_release_other_origin = json.loads(json.dumps(offer))
            same_release_other_origin["origin_node_id"] = "other-source"
            other_origin = topology_sync.remember_release_offer(same_release_other_origin)
            conflict = json.loads(json.dumps(offer))
            conflict["assets"]["PCL-Relay-macOS.zip"]["asset_sha256"] = "a" * 64
            with self.assertRaisesRegex(RuntimeError, "campaign conflict"):
                topology_sync.remember_release_offer(conflict)
        self.assertTrue(stored["stored"])
        self.assertTrue(document["available"])
        self.assertEqual(document["offer"]["version"], "2.6.0")
        self.assertEqual(repeated["reason"], "campaign_already_stored")
        self.assertEqual(other_origin["reason"], "campaign_already_stored")
        save.assert_called_once()

    def test_offline_node_pulls_persisted_campaign_on_next_sync(self):
        registry = {
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [{"id": "peer", "name": "Peer", "url": "http://100.64.0.2:15726", "token_file": ""}],
            }
        }
        envelope = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "revision": {"counter": 1, "origin": "local"},
            "digest": "same",
        }
        offer = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.6.0",
            "assets": release_assets(),
        }

        def request(url, *_args, **_kwargs):
            if url.endswith("/relay/v1/update"):
                return {"protocol": topology_sync.SYNC_PROTOCOL, "available": True, "offer": offer}
            return envelope

        with (
            mock.patch.object(topology_sync, "load_registry", return_value=registry),
            mock.patch.object(topology_sync, "topology_envelope", return_value=envelope),
            mock.patch.object(topology_sync, "_request", side_effect=request),
            mock.patch.object(
                topology_sync,
                "install_release_offer",
                return_value={"accepted": True, "installed": True, "reason": ""},
            ) as install,
        ):
            result = topology_sync.sync_once()
        install.assert_called_once_with(offer)
        self.assertEqual(result["peers"][0]["update_action"], "installed")
        self.assertEqual(result["ok_count"], 1)

    def test_persisted_campaign_reaches_pre_campaign_peer_after_it_returns(self):
        offer = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "action": "install-latest-release",
            "version": "2.6.0",
            "origin_node_id": "source",
            "source": "github:LossInWind/PCL-Relay",
            "release_url": "https://github.com/LossInWind/PCL-Relay/releases/tag/v2.6.0",
            "assets": release_assets(),
        }
        registry = {
            "relay_sync": {
                "node_id": "local",
                "node_name": "Mac",
                "revision": {"counter": 1, "origin": "local"},
                "peers": [{"id": "old-peer", "name": "Old Mac", "url": "http://100.64.0.3:15726", "token_file": ""}],
            },
            "relay_update": {"offer": offer},
        }
        envelope = {
            "protocol": topology_sync.SYNC_PROTOCOL,
            "revision": {"counter": 1, "origin": "local"},
            "digest": "same",
        }

        def request(url, _token="", body=None, timeout=10):
            if url.endswith("/relay/v1/topology"):
                return envelope
            if body is None:
                raise RuntimeError("Relay sync HTTP 404: old node")
            return {"accepted": True, "installed": True, "reason": ""}

        with (
            mock.patch.object(topology_sync, "load_registry", return_value=registry),
            mock.patch.object(topology_sync, "save_registry"),
            mock.patch.object(topology_sync, "topology_envelope", return_value=envelope),
            mock.patch.object(topology_sync, "_request", side_effect=request) as relay_request,
        ):
            result = topology_sync.sync_once()
        self.assertEqual(relay_request.call_count, 3)
        self.assertEqual(result["peers"][0]["update_action"], "pushed-persisted-campaign")
        delivery = registry["relay_update"]["deliveries"]["old-peer"]
        self.assertEqual(delivery["version"], "2.6.0")

    def test_cached_release_endpoint_requires_auth_and_streams_binary(self):
        from pcl_codex_bridge.release_updater import MAC_ASSET_NAME, promote_verified_release, release_cache_path

        payload = b"cached binary"
        checksum = hashlib.sha256(payload).hexdigest()
        status = {"latest_version": "2.6.0", "asset_name": MAC_ASSET_NAME}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"PCL_RELAY_RELEASE_CACHE": temporary}
        ):
            unverified = release_cache_path("2.6.0", MAC_ASSET_NAME)
            unverified.parent.mkdir(parents=True)
            unverified.write_bytes(payload)
            server = ThreadingHTTPServer(("127.0.0.1", 0), topology_sync.RelaySyncHandler)
            server.relay_token = "secret"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}" + topology_sync._release_route("2.6.0", MAC_ASSET_NAME)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    opener.open(url, timeout=2)
                self.assertEqual(rejected.exception.code, 401)
                request = urllib.request.Request(url, headers={"Authorization": "Bearer secret"})
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    opener.open(request, timeout=2)
                self.assertEqual(missing.exception.code, 404)
                part = Path(temporary) / "part"
                part.write_bytes(payload)
                promote_verified_release(status, part, checksum, len(payload))
                with opener.open(request, timeout=2) as response:
                    self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
                    self.assertEqual(response.headers["X-PCL-SHA256"], checksum)
                    self.assertEqual(response.read(), payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_sync_protocol_has_no_network_discovery_or_other_app_contract(self):
        source = Path(topology_sync.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("tailscale status", "clash", "haichen services", "official-proxy.json", "subprocess"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
