import io
import json
import unittest
import urllib.error
from unittest import mock

from pcl_codex_bridge.upstream_errors import public_upstream_error, MAX_ERROR_BYTES
from test_chat_stream_failure import Handler


class UpstreamErrorsTests(unittest.TestCase):
    def error(self, body, status=400):
        return urllib.error.HTTPError("https://private.invalid", status, "private", {}, io.BytesIO(body))

    def test_known_input_error_retains_status_and_request_id_not_secrets(self):
        error = self.error(json.dumps({"error": {"message": "glm-52 is not a multimodal model", "key": "SECRET"}}).encode())
        handler = Handler()
        with mock.patch("pcl_codex_bridge.gateway.open_chat_completion_resilient", side_effect=error), mock.patch("pcl_codex_bridge.gateway.log") as log:
            handler._proxy_chat()
        result = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.statuses, [400])
        self.assertEqual(result["error"]["code"], "unsupported_image_input")
        self.assertIn("不支持图片", result["error"]["message"])
        self.assertEqual(len(result["request_id"]), 32)
        self.assertNotIn("SECRET", str(result) + str(log.call_args_list))

    def test_untrusted_error_text_never_echoes_private_content(self):
        for body in [b"<html>SECRET</html>", b"null", b"[]", b"x" * (MAX_ERROR_BYTES + 1),
                     b'{"error":{"message":"SECRET_PROMPT sk-secret"}}',
                     b'{"error":{"message":"glm-52 is not a multimodal model SECRET"}}']:
            result = public_upstream_error(self.error(body), "id")
            self.assertEqual(result["error"]["code"], "upstream_error")
            self.assertNotIn("SECRET", str(result))

    def test_body_read_is_bounded_and_closed(self):
        error = self.error(b"{}")
        with mock.patch.object(error, "read", wraps=error.read) as read:
            public_upstream_error(error, "id")
            read.assert_called_once_with(MAX_ERROR_BYTES + 1)
        self.assertTrue(error.closed)

    def test_read_failure_keeps_original_error(self):
        error = self.error(b"", 503)
        with mock.patch.object(error, "read", side_effect=TimeoutError()):
            result = public_upstream_error(error, "id")
        self.assertIn("不可用", result["error"]["message"])
        self.assertTrue(error.closed)

    def test_request_is_not_rewritten(self):
        handler = Handler()
        body = b'{"messages":[{"content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]}],"reasoning_effort":"high","tools":[]}'
        handler._body = lambda: body
        with mock.patch("pcl_codex_bridge.gateway.open_chat_completion_resilient", side_effect=self.error(b"{}")) as upstream, mock.patch("pcl_codex_bridge.gateway.log"):
            handler._proxy_chat()
        upstream.assert_called_once_with(body)
