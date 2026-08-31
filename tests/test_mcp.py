import unittest

from pcl_codex_bridge.mcp_server import tools
from pcl_codex_bridge.models import AGENTS


class McpTests(unittest.TestCase):
    def test_exposes_generic_and_named_agent_tools(self):
        names = {tool["name"] for tool in tools()}
        self.assertIn("pcl_models", names)
        self.assertIn("pcl_delegate", names)
        self.assertTrue(set(AGENTS).issubset(names))

    def test_named_tools_require_task_and_workspace(self):
        by_name = {tool["name"]: tool for tool in tools()}
        for name in AGENTS:
            self.assertEqual(by_name[name]["inputSchema"]["required"], ["task", "workspace"])


if __name__ == "__main__":
    unittest.main()
