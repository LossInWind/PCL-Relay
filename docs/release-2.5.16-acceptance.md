# Relay 2.5.16 — native model roles and API connection export

## Design

- OpenCodex remains pinned, unmodified at 2.63.0. Its five featured spawn model
  overrides are not an exhaustive agent roster. Relay projects all visible,
  non-disabled models from the upstream catalog into native custom roles.
- Role files pin only the exact model, not effort or sandbox. Delegation can
  specify a supported `reasoning_effort`; otherwise Codex inherits the parent.
  Catalog-reported effort levels are metadata, not proof of upstream behavior.
- Activation and explicit catalog refresh reconcile roles. The existing control
  heartbeat follows later upstream catalog changes while integration is enabled;
  no new timer, network discovery or request interception is added. Opening or
  refreshing a status page does not write roles. Reopen Codex to load new roles.
- API connection export supplies the configured PCL gateway and raw model ID
  for Chat Completions clients. No upstream API key is read or exported. The
  gateway currently relies on network access control, not client bearer auth;
  optional client placeholder text is explicitly labeled as non-secret.

Release source: `b32e3cb`. Published release:
https://github.com/LossInWind/PCL-Relay/releases/tag/v2.5.16

## Evidence

- Local native smoke child `01a0cd3e-2577-7b11-9210-79e578b0f6b4` completed with
  CHILD_OK. Its own turn context records model `gpt-6-sol`, effort `low`, and
  read-only sandbox. No project files were read or modified by the child.
- 11 current catalog models projected. This is configuration coverage, not
  eleven independent live-model availability tests.
- Second child `01a0cd89-ffe6-7ee3-b701-5084d7cbdd26` completed with CHILD_OK;
  its context records `pcl/Kimi-K3`, `low`, read-only.
- Clean release checkout: 230 Python tests passed. XCTest runner: 36 tests
  passed. Swift release build and macOS signature verification passed.
- Final API sheet inspected visually; full addresses and credential explanation
  wrap without truncation. OpenCode copy action reports success. Generated
  OpenCode/Pi JSON tests cover all-model export and one provider, independent of
  the Codex selection. Neither third-party client was installed/run for this test.
- Gateway `/v1/models` returned 16 IDs. Text-agent config exports 8 catalog text
  models, excluding embedding/reranking. A real Flash Chat Completions stream
  completed through the exported gateway with the non-secret placeholder key.
- Format references: https://opencode.ai/docs/providers/ and
  https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md

## Artifacts

- macOS SHA256: `c8af0cdc8019dcdda71282df6f34d8d2f7dbd61549c7b5fd3f612587afd24d34`
- Linux x86_64 SHA256: `4425f37110b541faf2d8486384c8ba54184427512a729a4619fdcce363422745`
- Linux bundle built on the Pod with verified upstream tree; final Python-only
  desired-state correction applied before deterministic repack. No runtime or
  vendored upstream changes. No Linux ARM artifact is claimed.
- GitHub release-assets endpoint reports all four files uploaded. The embedded
  assets array on release-detail temporarily returned stale empty data; direct
  asset-list verification was used instead of declaring missing files complete.

## Deployment evidence and remaining gates

- Local Mac: installed signed 2.5.16 / b32e3cb, CLI staged, control service
  restarted independently and active. 11 roles generated. OpenCodex PID 2003
  unchanged; no model-service restart, credential/history or route rewrite.
- 3070Ti: recovered after its reboot. GitHub download failed; verified matching
  Mac cache used. Installed version and control heartbeat both 2.5.16. 11 roles
  generated, including exact `gpt-6-sol` without pinned effort. Only control
  service restarted; OpenCodex PID 1747 and gateway PID 1749 unchanged.
- A6000: verified 2.5.16 package staged at
  `/home/zhc/.cache/pcl-relay/releases/2.5.16/expanded/PCL-Relay-linux-x86_64`.
  No active Relay installation exists in root or zhc; official Codex was not
  silently rerouted. This is staged, not activated.
- Kai Mac: SSH recovered on retry. GitHub download failed, matching verified Mac
  fallback installed 2.5.16; 11 roles generated. Existing control service alone
  restarted. Tailnet heartbeat reports 2.5.16. GUI was not running and was not
  remotely forced open. Model service was not restarted.
- BUPT shared and 10.112.205.245: SSH timeout, pending; not counted successful.

Existing Codex windows may need reopening to load new native roles. Catalog
eligibility is not proof every model/effort combination works at the provider.
Static third-party configs need regenerating for future new model IDs; the
gateway itself does not restrict requests to Codex's selected four models.

Final heartbeat readback: local Mac, 3070Ti and Kai Mac all report 2.5.16.
GitHub asset digests match both local package checksums above. Temporary
loopback SOCKS transport through 3070Ti was used solely for GitHub publishing;
no persistent SSH/Clash settings were changed.
