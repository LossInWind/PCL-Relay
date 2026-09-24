import io
import http.client
import threading
from http.server import ThreadingHTTPServer
import unittest
import urllib.error
from unittest import mock

from pcl_codex_bridge.gateway import GatewayHandler


class Sink(io.BytesIO):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure

    def write(self, data):
        if self.failure:
            raise self.failure
        return super().write(data)


class Handler:
    _proxy_chat = GatewayHandler._proxy_chat
    _json = GatewayHandler._json

    def __init__(self, failure=None):
        self.wfile = Sink(failure)
        self.statuses = []
        self.headers = []

    def _body(self):
        return b'{"messages":[{"content":"SECRET_PROMPT"}]}'

    def send_response(self, status):
        self.statuses.append(status)

    def send_header(self, *pair):
        self.headers.append(pair)

    def end_headers(self):
        pass


class Upstream:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, events):
        self.events = iter(events)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def readline(self):
        item = next(self.events, b"")
        if isinstance(item, Exception):
            raise item
        return item

    read1 = lambda self, size: self.readline()


class ChatStreamFailureTests(unittest.TestCase):
    def test_real_http_stream_has_no_embedded_second_status(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        partial = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        upstream = Upstream([partial, ConnectionResetError()])
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            with mock.patch("pcl_codex_bridge.gateway.open_chat_completion_resilient", return_value=upstream) as opened, mock.patch("pcl_codex_bridge.gateway.log"):
                connection.request("POST", "/v1/chat/completions", b"{}")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(len(response.getheader("X-Relay-Request-ID")), 32)
                self.assertEqual(response.read(), partial)
                self.assertEqual(opened.call_count, 1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def run_case(self, events, failure=None, content_type="text/event-stream"):
        upstream = Upstream(events)
        upstream.headers = {"Content-Type": content_type}
        handler = Handler(failure)
        with mock.patch("pcl_codex_bridge.gateway.open_chat_completion_resilient", return_value=upstream) as opened, mock.patch("pcl_codex_bridge.gateway.log") as log:
            handler._proxy_chat()
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(upstream.closed)
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.statuses, [200])
        self.assertNotIn("SECRET_PROMPT", str(log.call_args))
        return handler, str(log.call_args)

    def test_partial_stream_reset_never_sends_second_response(self):
        chunk = b'data: {"choices":[{"delta":{"reasoning_content":"hello"}}]}\n\n'
        h, log = self.run_case([chunk, ConnectionResetError("SECRET_ERROR")])
        self.assertEqual(h.wfile.getvalue(), chunk)
        self.assertIn("phase=upstream_read", log)
        self.assertIn("error=ConnectionResetError", log)
        self.assertNotIn("SECRET_ERROR", log)

    def test_timeout_after_headers_does_not_fabricate_completion(self):
        h, log = self.run_case([TimeoutError()])
        self.assertEqual(h.wfile.getvalue(), b"")
        self.assertIn("error=TimeoutError", log)

    def test_downstream_disconnect_is_not_upstream_failure(self):
        _, log = self.run_case([b"data: x\n\n"], BrokenPipeError())
        self.assertIn("phase=downstream_write", log)

    def test_valid_stream_is_byte_identical(self):
        chunks = [b": heartbeat\n\n", b'data: {"choices":[{"finish_reason":"stop"}]}\n\n', b"data: [DONE]\n\n"]
        h, log = self.run_case(chunks)
        self.assertEqual(h.wfile.getvalue(), b"".join(chunks))
        self.assertIn("outcome=upstream_eof", log)

    def test_empty_eof_not_reported_as_model_success(self):
        h, log = self.run_case([])
        self.assertEqual(h.wfile.getvalue(), b"")
        self.assertIn("outcome=upstream_eof", log)
        self.assertNotIn("success", log)

    def test_non_stream_response_preserved(self):
        h, _ = self.run_case([b'{"ok":true}'], content_type="application/json")
        self.assertEqual(h.wfile.getvalue(), b'{"ok":true}')

    def test_preheader_failures_return_one_error_without_leaking_body(self):
        for exc, status in [(TimeoutError("SECRET"), 502), (urllib.error.HTTPError("https://example.test", 429, "SECRET", {}, io.BytesIO(b"SECRET")), 429)]:
            h = Handler()
            with mock.patch("pcl_codex_bridge.gateway.open_chat_completion_resilient", side_effect=exc), mock.patch("pcl_codex_bridge.gateway.log"):
                h._proxy_chat()
            self.assertEqual(h.statuses, [status])
            self.assertNotIn(b"SECRET", h.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
