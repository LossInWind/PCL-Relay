# PCL Relay maintenance boundaries

- Before diagnosing or changing OpenCodex behavior, inspect the pinned version,
  upstream issues/PRs, and newer stable releases. Prefer an upstream fix over a
  local implementation. An open PR is not a shipped fix.
- Keep `vendor/opencodex` byte-for-byte equal to its declared upstream tree.
  Upgrade the complete pin and provenance together; do not maintain local edits
  to OpenCodex transport, retries, continuation, auth, or agent protocols.
- Put Relay-owned diagnostics, UI, configuration validation, and update logic in
  the scaffolding. Do not add another request interceptor to work around upstream.
- A source upgrade, a passing test, a built package, and a running-device upgrade
  are distinct outcomes. Report each accurately.
- Validate in isolation first. Never restart an active data plane, rewrite login
  or history, or modify SSH/Clash merely to update the GUI. Stage new resources
  until an appropriate safe runtime activation window is available.
