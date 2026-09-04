import ast
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


if __name__ == "__main__":
    unittest.main()
