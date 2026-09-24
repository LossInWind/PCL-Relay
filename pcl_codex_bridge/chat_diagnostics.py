"""Bounded, metadata-only observations; never controls or repairs a stream."""
import json
import threading
import time
from collections import deque


class ChatDiagnostics:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.active = {}
        self.recent = deque(maxlen=32)

    def start(self, request_id):
        with self.lock:
            if len(self.active) >= 256:
                return
            now = self.clock()
            self.active[request_id] = dict(request_id=request_id, started=now,
                last=now, bytes_received=0, first_byte_ms=None, streaming=False,
                done=False, finish=False, uncertain=False, phase="upstream_open")

    def phase(self, request_id, phase, streaming=None):
        with self.lock:
            row = self.active.get(request_id)
            if row is None:
                return
            row['phase'] = phase
            if streaming is not None:
                row['streaming'] = streaming

    def received(self, request_id, chunk):
        with self.lock:
            row = self.active.get(request_id)
            if row is None:
                return
            now = self.clock()
            row['last'] = now
            row['bytes_received'] += len(chunk)
            if row['first_byte_ms'] is None:
                row['first_byte_ms'] = int((now-row['started'])*1000)
            if not row['streaming'] or not chunk.startswith(b'data:'):
                return
            if len(chunk) > 1024*1024:
                row['uncertain'] = True
                return
            data = chunk[5:].strip()
            if data == b'[DONE]':
                row['done'] = True
                return
            try:
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError()
                choices = event.get('choices') or []
                row['finish'] |= any(isinstance(c, dict) and bool(c.get('finish_reason')) for c in choices)
            except (ValueError, TypeError, RecursionError):
                row['uncertain'] = True

    def finish(self, request_id, outcome, phase, error):
        with self.lock:
            row = self.active.pop(request_id, None)
            if row is None:
                return
            state = 'transport_error'
            if outcome == 'upstream_eof':
                state = 'transport_closed'
                if row['streaming']:
                    state = ('protocol_end_observed' if row['done'] and row['finish']
                             else 'unconfirmed_end' if row['uncertain'] else 'incomplete_stream')
            row.update(state=state, phase=phase, error=error, ended=self.clock())
            self.recent.append(row)
            return state

    def snapshot(self):
        with self.lock:
            now = self.clock()
            def public(row):
                age = max(0, int(now-row['last']))
                state = row.get('state', 'receiving')
                if 'ended' not in row:
                    state = ('waiting_upstream' if age >= 60 else
                             'connecting' if row['first_byte_ms'] is None else 'receiving')
                    if row['phase'] == 'downstream_write':
                        state = 'forwarding'
                return dict(request_id=row['request_id'], state=state,
                    duration_ms=int((row.get('ended',now)-row['started'])*1000),
                    last_data_age_seconds=age, bytes_received=row['bytes_received'],
                    first_byte_ms=row['first_byte_ms'], phase=row['phase'],
                    error=row.get('error','none'))
            return dict(scope='chat_completions_only', active=[public(r) for r in self.active.values()],
                        recent=[public(r) for r in reversed(self.recent)])


CHAT_DIAGNOSTICS = ChatDiagnostics()
