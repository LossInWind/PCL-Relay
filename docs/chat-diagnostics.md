# Passive Chat request diagnostics

This change observes Relay's existing `/v1/chat/completions` handler only.
It does not modify OpenCodex, outgoing request bytes, response bytes, retries,
timeouts, route selection, credentials, or session history.

`/admin/status.chat_diagnostics` is optional for backward compatibility.
The App shows it under server diagnostics after an explicit status check.
It is not a live OpenCode progress indicator or an execution-success signal.

- `waiting_upstream`: no complete received line/chunk for 60 seconds. This is
  an observation, not a confirmed failure; partial-line network traffic may exist.
- `protocol_end_observed`: both finish_reason and DONE were observed. Task
  completion and tool execution are deliberately not inferred.
- `incomplete_stream`: EOF without both terminal indicators.
- `unconfirmed_end`: malformed or oversized events prevent reliable observation.
- `transport_error`: retain phase and exception type, not exception contents.

The registry retains at most 256 active and 32 completed requests, in memory
only. It stores no prompt, generated content, tool arguments, model identifier,
authorization header, or key. Oversized data lines are not parsed. Existing
transport framing is unchanged. Process restarts clear this recent history.

No daemon, timer, background reconnect or replay was added. The status snapshot
computes waiting duration when read. Logs add only a protocol-state enum.

Verification (2026-09-24): 248 Python unittest cases passed in the current
working tree; 14 focused chat diagnostics/byte-preservation cases passed;
Swift release build passed. This is source/build verification, not installed
production acceptance. Existing unrelated loopback-policy edits were excluded
from this change. No live gateway restart or client configuration rewrite.
