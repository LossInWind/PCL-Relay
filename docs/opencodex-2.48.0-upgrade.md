# OpenCodex 2.48.0 integration (pending activation)

This source update adopts the complete upstream v2.48.0 tree, without local
OpenCodex patches. Relay's source version is 2.5.14; this is not a claim that a
2.5.14 package has been published or installed.

## Reason and upstream evidence

- [Release](https://github.com/lidge-jun/opencodex/releases/tag/v2.48.0)
- [Merged PR #3937](https://github.com/lidge-jun/opencodex/pull/3937): expired or
  missing canonical forward replay state now returns `previous_response_not_found`.
  WebSocket clients can reconnect with their retained full history. HTTP callers
  still must supply full context explicitly. Cache retention and fail-closed
  rejection of incomplete history are unchanged.
- [Open PR #3389](https://github.com/lidge-jun/opencodex/pull/3389) concerns
  zero-output *mid-stream* resets. It is not merged and is not included as a
  downstream patch. It does not establish the cause of a pre-header fetch 502.

## Verification and remaining gates

The unmodified upstream checkout passed 300 tests covering issue-702 replay
recovery, connect errors, reset retries, response-state storage, and WebSocket
upstream handling, using the existing Bun 1.3.14 runtime and runtime dependencies.
Tests used loopback fixtures and isolated homes, not paid model requests.

The Relay scaffolding suite passed 208 tests before the added pin-consistency
regression. The vendored Git tree was verified equal to upstream tree
`a5148ad2f35df6d8ff1e1802e8aac6902f0a5e36`.

The earlier local candidate patch was withdrawn and is not part of this tree.
Its 302-test result is not used as acceptance evidence for upstream 2.48.0.

Still required before release/activation: package validation, isolated real GPT
and PCL routes (including compaction/tools/native agents), safe runtime activation,
and per-device installed/running-version verification. Do not restart a healthy
shared service merely to make its displayed version match.
