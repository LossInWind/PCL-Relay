# PCL Relay 2.5.18: Chat stream failure boundary

Scope: Relay-owned `gateway.py` only; pinned OpenCodex remains unchanged.

After Chat response headers are committed, a read/write failure closes the
connection instead of attempting a second HTTP response. Partial output is never
replayed; no finish reason or DONE marker is synthesized. Pre-header failures
retain their HTTP status and expose a correlation ID, not raw upstream text.
Diagnostics include phase, exception class, bytes forwarded and elapsed time;
EOF is not labelled model success. Prompts, tokens and exception text are omitted.

Tests cover pre-header HTTP and transport errors, post-header resets/timeouts,
downstream disconnect, byte-identical SSE and JSON forwarding, incomplete EOF,
resource closure and a real loopback HTTP stream. No production failure injected.

This fixes the confirmed double-response defect. It does NOT establish or fix
the original roughly five-minute disconnect, OpenCode retry policy, incompatible
oh-my-openagent plugin or models.dev connectivity. Those remain separate gates.

Deployment must retain backups, verify each platform archive and preserve healthy
OpenCodex processes and all credentials/routes. An active gateway must be drained
or left pending; installed version is not evidence of running activation.

## Verified release results (2026-09-24)

- Fix commit `d696e96`; 240 Python tests passed in the clean detached checkout.
- Eight focused gateway tests also passed on Linux. Real isolated Kimi request
  returned OK, finish_reason=stop and DONE; repeated successfully from Mac through
  the activated production Tailnet gateway.
- macOS release compilation and strict App signature verification passed. XCTest
  was not a valid new acceptance result: the available SDK first failed to load
  XCTest, and explicit framework paths discovered zero tests. GUI binary is the
  unchanged previously verified 2.5.17 binary, not a newly tested UI build.
- Packages reuse checksum-verified 2.5.17 resources, updating gateway.py, VERSION
  and macOS bundle version/build metadata; no OpenCodex or GUI code changes.
- macOS SHA256: `48bfdea36d27cbc9bd00ea31ebeb9859b4cd691377944d9176ed133ec6ba62e1`.
- Linux SHA256: `b7cf5da830550b49b6b2f79ac82a573fbc75b87bdc3bd6e15db5ceb650112d3c`.
- GitHub asset digests matched local digests before publication of v2.5.18.
- Local Mac: GUI installed/reopened and control service reports 2.5.18;
  OpenCodex PID 2003 preserved.
- 3070Ti: package installed; loopback and Tailnet gateways now report 2.5.18.
  Tailnet activation waited until actual model connections drained. An offline
  Kai TCP socket with no payload was not counted as an active model request.
  Control service reports 2.5.18; OpenCodex PID 1747 preserved.
- Kai and A6000: Tailnet offline, SSH timed out; A6000 bootstrap SSH refused.
  They are pending, not upgraded. No forced network or daemon changes.
- BUPT A100 (`bupt-vpn-recovery`): verified GitHub archive installed/staged at
  2.5.18. No prior Relay installation was found for this user; no route/service
  was enabled. One mixed/resumed transfer failed checksum and was rejected;
  a separate exclusive GitHub download matched the published digest.
- `10.112.205.245`: GitHub access failed, verified identical Mac cache installed
  at 2.5.18; inactive control service left inactive. Existing OpenCodex 2.48.0
  PID 1457 preserved, 2.63.0 runtime only staged (not activated).
- Both BUPT installations passed before/after credential and Codex configuration
  hash comparison. Mac and 3070Ti upgrades did not invoke integration enable.
- OpenCode's six recent production assistant steps after activation ended with
  tool-calls/stop and no stored error. This bounded observation does not close
  the separate long-stream timeout investigation.
