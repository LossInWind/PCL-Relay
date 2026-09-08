import unittest
from pathlib import Path
from unittest import mock

from pcl_codex_bridge import routes


MODEL_REPLY = {
    "url": "http://relay-b:15722/v1",
    "healthy": True,
    "model_count": 1,
    "latency_ms": 12,
    "models": [{"id": "GLM-5.2", "owned_by": "pcl"}],
    "service": "pcl-codex-gateway",
    "version": "2.5.5",
}


class GatewayRouteTests(unittest.TestCase):
    def test_normalizes_only_explicit_http_endpoint(self):
        self.assertEqual(
            routes.normalize_gateway_url(" http://relay:15722 "),
            "http://relay:15722/v1",
        )
        for invalid in ("https://relay:15722/v1", "http://relay/v1", "http://user@relay:15722/v1"):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                routes.normalize_gateway_url(invalid)

    def test_add_validates_and_deduplicates_gateway(self):
        registry = {"gateway": "http://relay-a:15722/v1", "gateways": []}
        with (
            mock.patch.object(routes, "load_registry", return_value=registry),
            mock.patch.object(routes, "probe_gateway", return_value=MODEL_REPLY) as probe,
            mock.patch.object(routes, "save_registry") as save,
        ):
            first = routes.add_gateway("http://relay-b:15722", "Relay B")
            routes.add_gateway("http://relay-b:15722/v1", "Renamed")
        self.assertTrue(first["added"])
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(len(save.call_args.args[0]["gateways"]), 2)
        self.assertEqual(save.call_args.args[0]["gateways"][-1]["name"], "Renamed")

    def test_select_saves_when_sidecar_is_not_installed(self):
        registry = {
            "gateway": "http://relay-a:15722/v1",
            "gateways": [
                {"name": "A", "url": "http://relay-a:15722/v1"},
                {"name": "B", "url": "http://relay-b:15722/v1"},
            ],
        }
        with (
            mock.patch.object(routes, "load_registry", return_value=registry),
            mock.patch.object(routes, "probe_gateway", return_value=MODEL_REPLY),
            mock.patch.object(routes, "installed_runtime", side_effect=FileNotFoundError()),
            mock.patch.object(routes, "save_registry") as save,
        ):
            result = routes.select_gateway("http://relay-b:15722/v1")
        self.assertFalse(result["runtime_applied"])
        self.assertEqual(save.call_args.args[0]["gateway"], "http://relay-b:15722/v1")

    def test_live_switch_failure_rolls_back_and_does_not_commit_registry(self):
        registry = {
            "gateway": "http://relay-a:15722/v1",
            "gateways": [
                {"name": "A", "url": "http://relay-a:15722/v1"},
                {"name": "B", "url": "http://relay-b:15722/v1"},
            ],
        }
        runtime = mock.MagicMock()
        with (
            mock.patch.object(routes, "load_registry", return_value=registry),
            mock.patch.object(routes, "probe_gateway", return_value=MODEL_REPLY),
            mock.patch.object(routes, "installed_runtime", return_value=runtime),
            mock.patch.object(routes, "sidecar_health", return_value={"ok": True}),
            mock.patch.object(
                routes,
                "switch_pcl_gateway",
                side_effect=[RuntimeError("invalid provider"), {"configured": True}],
            ) as switch,
            mock.patch.object(routes, "save_registry") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "registry was not changed"):
                routes.select_gateway("http://relay-b:15722/v1")
        self.assertEqual(switch.call_count, 2)
        self.assertEqual(switch.call_args_list[1].args[1], "http://relay-a:15722/v1")
        save.assert_not_called()

    def test_active_gateway_cannot_be_removed(self):
        registry = {
            "gateway": "http://relay-a:15722/v1",
            "gateways": [{"name": "A", "url": "http://relay-a:15722/v1"}],
        }
        with mock.patch.object(routes, "load_registry", return_value=registry):
            with self.assertRaisesRegex(RuntimeError, "before removing"):
                routes.remove_gateway("http://relay-a:15722/v1")

    def test_route_module_does_not_import_network_management(self):
        source = Path(routes.__file__).read_text(encoding="utf-8")
        for forbidden in ("tailscale", "clash", "ssh", "official-proxy.json"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
