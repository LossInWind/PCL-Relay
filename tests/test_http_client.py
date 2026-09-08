import io
import json
import urllib.error
import unittest
from unittest import mock

from pcl_codex_bridge.http_client import request_json, safe_http_error


class HTTPDiagnosticsTests(unittest.TestCase):
    def error(self, body):
        return urllib.error.HTTPError("http://relay/v1/models", 503, "Unavailable", {}, io.BytesIO(body))

    def test_gateway_wrapped_message_preserves_code_and_redacts_secrets(self):
        detail = {"error": {"message": "No channel sk-secret123 Bearer private123 token=abc https://host/?key=def",
                            "code": "model_not_found"}}
        result = safe_http_error(self.error(json.dumps({"detail": json.dumps(detail)}).encode()))
        self.assertEqual(result.code, 503)
        self.assertIn("model_not_found", str(result))
        for value in ("sk-secret123", "private123", "abc", "def"):
            self.assertNotIn(value, str(result))

    def test_unstructured_body_is_not_exposed(self):
        result = safe_http_error(self.error(b"<html>private upstream debug dump</html>"))
        self.assertNotIn("private", str(result))

    def test_diagnostics_are_bounded_and_not_retried(self):
        body = json.dumps({"error": {"message": "x" * 2000}}).encode()
        opener = mock.Mock()
        opener.open.side_effect = self.error(body)
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request_json("http://relay/v1/models")
        self.assertLessEqual(len(raised.exception.reason), 600)
        self.assertEqual(opener.open.call_count, 1)
