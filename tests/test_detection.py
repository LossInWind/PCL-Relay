import unittest
from unittest import mock

from pcl_codex_bridge import model_detection
from pcl_codex_bridge.models import AGENTS


class DetectionTests(unittest.TestCase):
    def test_discover_models_records_agent_and_non_agent_details(self):
        payload = {
            "data": [
                {"id": "Qwen3.6-35B", "owned_by": "openai"},
                {"id": "bge-m3", "owned_by": "openai"},
            ]
        }
        with (
            mock.patch.object(model_detection, "request_json", return_value=payload),
            mock.patch.object(model_detection, "load_registry", return_value={}),
            mock.patch.object(model_detection, "save_registry") as save,
        ):
            report = model_detection.discover_models("http://tailnet:15722/v1")
        self.assertTrue(report["available_models"]["Qwen3.6-35B"]["agent_eligible"])
        self.assertEqual(report["available_models"]["Qwen3.6-35B"]["family"], "Qwen")
        self.assertFalse(report["available_models"]["bge-m3"]["agent_eligible"])
        self.assertEqual(report["available_models"]["bge-m3"]["category"], "embedding")
        save.assert_called_once()
    def test_detect_models_records_stream_capability(self):
        advertised = {"data": [{"id": item["model"]} for item in AGENTS.values()]}
        chat = {"choices": [{"message": {"content": "PCL_OK"}}]}
        tool = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "pcl_probe", "arguments": '{"value":"PCL_TOOL_OK"}'}}
                        ]
                    }
                }
            ]
        }

        def fake_json(url, body=None, timeout=60):
            if url.endswith("/models"):
                return advertised
            return tool if body and body.get("tools") else chat

        with (
            mock.patch.object(model_detection, "request_json", side_effect=fake_json),
            mock.patch.object(model_detection, "request_stream_probe", return_value=True),
            mock.patch.object(model_detection, "load_registry", return_value={"selected_agents": ["pcl_glm"]}),
            mock.patch.object(model_detection, "save_registry"),
        ):
            report = model_detection.detect_models("http://tailnet:15722/v1")

        self.assertTrue(report["all_chat_ready"])
        self.assertTrue(report["all_stream_ready"])
        self.assertTrue(report["all_tool_compatible"])
        self.assertTrue(report["all_native_tools"])
        self.assertTrue(all(item["execution_ready"] for item in report["models"].values()))
        self.assertEqual(report["selected_agents"], ["pcl_glm"])

    def test_detect_models_uses_responses_fallback_when_native_tools_are_missing(self):
        advertised = {"data": [{"id": item["model"]} for item in AGENTS.values()]}
        chat = {"choices": [{"message": {"content": "PCL_OK"}}]}

        def fake_json(url, body=None, timeout=60):
            if url.endswith("/models"):
                return advertised
            return chat

        with (
            mock.patch.object(model_detection, "request_json", side_effect=fake_json),
            mock.patch.object(model_detection, "request_stream_probe", return_value=True),
            mock.patch.object(model_detection, "request_responses_tool_probe", return_value=True),
            mock.patch.object(model_detection, "load_registry", return_value={}),
            mock.patch.object(model_detection, "save_registry"),
        ):
            report = model_detection.detect_models("http://tailnet:15722/v1")

        self.assertFalse(report["all_native_tools"])
        self.assertTrue(report["all_tool_compatible"])
        self.assertTrue(all(item["tool_call_mode"] == "json_fallback" for item in report["models"].values()))
        self.assertTrue(all(item["execution_ready"] for item in report["models"].values()))


if __name__ == "__main__":
    unittest.main()
