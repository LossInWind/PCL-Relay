import json
import unittest
from pcl_codex_bridge.chat_diagnostics import ChatDiagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.d = ChatDiagnostics(lambda: self.now)
        self.d.start('test')
        self.d.phase('test', 'upstream_read', True)

    def test_wait_is_observation_not_failure(self):
        self.now = 61
        self.assertEqual(self.d.snapshot()['active'][0]['state'], 'waiting_upstream')
        self.d.received('test', b': heartbeat\n')
        self.assertEqual(self.d.snapshot()['active'][0]['state'], 'receiving')
        self.assertEqual(len(self.d.active), 1)

    def test_complete_not_task_success(self):
        self.d.received('test', b'data: {"choices":[{"finish_reason":"stop"}]}\n')
        self.d.received('test', b'data: [DONE]\n')
        self.d.finish('test', 'upstream_eof', 'upstream_read', 'none')
        self.assertEqual(self.d.snapshot()['recent'][0]['state'], 'protocol_end_observed')
        self.assertFalse(self.d.snapshot()['active'])

    def test_partial_eof_and_privacy(self):
        self.d.received('test', b'data: {"choices":[{"delta":{"content":"SECRET"}}]}\n')
        self.d.finish('test', 'upstream_eof', 'upstream_read', 'none')
        result = self.d.snapshot()
        self.assertEqual(result['recent'][0]['state'], 'incomplete_stream')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_malformed_and_oversized_observations_are_uncertain(self):
        for chunk in (b'data: null\n', b'data: {broken\n', b'data: '+b'x'*(1024*1024+1)):
            self.d.start('test')
            self.d.phase('test', 'upstream_read', True)
            self.d.received('test', chunk)
            self.d.finish('test', 'upstream_eof', 'upstream_read', 'none')
            self.assertEqual(self.d.snapshot()['recent'][0]['state'], 'unconfirmed_end')

    def test_downstream_error_is_distinct(self):
        self.d.finish('test', 'error', 'downstream_write', 'BrokenPipeError')
        row = self.d.snapshot()['recent'][0]
        self.assertEqual(row['state'], 'transport_error')
        self.assertEqual(row['phase'], 'downstream_write')

    def test_bounded_and_concurrent_safe_snapshots(self):
        for n in range(300): self.d.start(str(n))
        self.assertEqual(len(self.d.snapshot()['active']), 256)
        for n in range(300): self.d.finish(str(n), 'error', 'upstream_open', 'TimeoutError')
        self.assertEqual(len(self.d.snapshot()['recent']), 32)

if __name__ == '__main__': unittest.main()
