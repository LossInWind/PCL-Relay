import ast
import plistlib
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1] / "pcl_codex_bridge"


def internal_imports(module: str) -> set[str]:
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            result.add(node.module.split(".", 1)[0])
    return result


class ArchitectureTests(unittest.TestCase):
    def test_version_has_one_canonical_source(self):
        root = PACKAGE.parent
        expected = (PACKAGE / "VERSION").read_text(encoding="utf-8").strip()
        from pcl_codex_bridge import __version__

        self.assertEqual(__version__, expected)
        self.assertIn('dynamic = ["version"]', (root / "pyproject.toml").read_text())
        with (root / "macos" / "Info.plist").open("rb") as handle:
            source_plist = plistlib.load(handle)
        self.assertEqual(source_plist["CFBundleShortVersionString"], "0.0.0")
        build_script = (root / "scripts" / "build_macos_app.sh").read_text()
        self.assertIn("pcl_codex_bridge/VERSION", build_script)
        self.assertIn("Set :CFBundleShortVersionString $VERSION", build_script)
        self.assertIn('PCL_OPENCODEX_RUNTIME_BUN', build_script)
        self.assertIn('--version)" != "1.3.14"', build_script)
        self.assertIn('realpath "$OPENCODEX_RUNTIME_BUN"', build_script)

    def test_low_level_data_plane_does_not_depend_on_control_plane(self):
        control_plane = {
            "bridges",
            "cli",
            "client_config",
            "direct_clients",
            "model_detection",
            "release_updater",
            "relay_discovery",
            "remote_clients",
        }
        for module in ("http_client", "responses_protocol", "responses_stream", "gateway"):
            with self.subTest(module=module):
                self.assertFalse(internal_imports(module) & control_plane)

    def test_read_only_discovery_does_not_depend_on_configuration_writer(self):
        self.assertNotIn("client_config", internal_imports("relay_discovery"))
        self.assertNotIn("client_config", internal_imports("model_detection"))

    def test_public_cli_does_not_expose_network_or_file_management(self):
        from pcl_codex_bridge.cli import parser

        root = parser()
        subparsers = next(
            action for action in root._actions if action.__class__.__name__ == "_SubParsersAction"
        )
        commands = set(subparsers.choices)
        self.assertTrue({"routes", "models", "sidecar", "integration", "sync"} <= commands)
        self.assertFalse({"relays", "clients", "bridges", "direct", "native-router"} & commands)
        self.assertFalse(
            internal_imports("cli")
            & {"relay_discovery", "remote_clients", "bridges", "direct_clients"}
        )

    def test_gateway_does_not_discover_or_manage_network(self):
        source = (PACKAGE / "gateway.py").read_text(encoding="utf-8")
        for forbidden in (
            'import subprocess',
            '"tailscale"',
            "discover_tailscale_ip",
            "/admin/topology",
            "TOPOLOGY_REPORTS",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn('PCL_CODEX_GATEWAY_HOST", "127.0.0.1"', source)

    def test_relay_apps_have_no_cross_application_state_contract(self):
        official = (PACKAGE / "official_network.py").read_text(encoding="utf-8")
        routes = (PACKAGE / "routes.py").read_text(encoding="utf-8")
        sync = (PACKAGE / "topology_sync.py").read_text(encoding="utf-8")
        for source in (official, routes, sync):
            self.assertNotIn("official-proxy.json", source)
            self.assertNotIn("Haichen Services", source)
        self.assertFalse(
            internal_imports("topology_sync")
            & {"relay_discovery", "remote_clients", "bridges", "direct_clients"}
        )

    def test_macos_target_excludes_legacy_network_ui(self):
        package = (PACKAGE.parent / "Package.swift").read_text(encoding="utf-8")
        for path in (
            "State/AppModel+Topology.swift",
            "Views/DeviceManagementComponents.swift",
            "Views/NetworkView.swift",
            "Views/TopologyComponents.swift",
        ):
            self.assertIn(path, package)
        shell = (
            PACKAGE.parent
            / "macos"
            / "Sources"
            / "PCLCodexManager"
            / "Views"
            / "AppShellView.swift"
        ).read_text(encoding="utf-8")
        self.assertIn('case routing = "路由"', shell)
        self.assertNotIn('case network = "网络"', shell)

    def test_linux_bundle_contains_complete_pinned_runtime_contract(self):
        script = (PACKAGE.parent / "scripts" / "build_linux_bundle.sh").read_text()
        self.assertIn("PCL-Relay-linux-$ARCH", script)
        self.assertIn("$OPENCODEX_SOURCE/node_modules", script)
        self.assertIn("$RUNTIME/bin/bun", script)
        self.assertIn("1.3.14", script)
        self.assertIn("opencodex.UPSTREAM.json", script)

    def test_release_bundles_exclude_quarantined_network_modules(self):
        root = PACKAGE.parent
        for script_name in ("build_macos_app.sh", "build_linux_bundle.sh"):
            script = (root / "scripts" / script_name).read_text()
            for module in (
                "relay_discovery.py",
                "remote_clients.py",
                "direct_clients.py",
                "bridges.py",
            ):
                with self.subTest(script=script_name, module=module):
                    self.assertIn(f"--exclude={module}", script.replace("--exclude ", "--exclude="))

    def test_release_updates_never_target_codex_history_or_credentials(self):
        for module in ("release_updater", "topology_sync"):
            source = (PACKAGE / f"{module}.py").read_text(encoding="utf-8").lower()
            for forbidden in ("history.jsonl", "archived_sessions", ".codex/sessions", ".codex/auth"):
                with self.subTest(module=module, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
