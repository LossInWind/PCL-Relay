import unittest

from pcl_codex_bridge.mcp_server import tools
class McpTests(unittest.TestCase):
    def test_mcp_is_management_only(self):
        names = {tool["name"] for tool in tools()}
        self.assertEqual(names, {"pcl_models", "pcl_native_status"})
        self.assertNotIn("pcl_delegate", names)
        descriptions = " ".join(tool["description"] for tool in tools())
        self.assertIn("pcl_deepseek_pro=pcl/DeepSeek-V4-Pro", descriptions)


if __name__ == "__main__":
    unittest.main()
