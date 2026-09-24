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
