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

## Evidence

- Local native smoke child `01a0cd3e-2577-7b11-9210-79e578b0f6b4` completed with
  CHILD_OK. Its own turn context records model `gpt-6-sol`, effort `low`, and
  read-only sandbox. No project files were read or modified by the child.
- 11 current catalog models projected. This is configuration coverage, not
  eleven independent live-model availability tests.
- Python regression and Swift/UI/deployment acceptance are recorded below as
  completed. Offline nodes must not be counted as installed or verified.

## Deployment gates

Pending package, UI copy checks and device synchronization. Do not treat this
source record as a completed release acceptance.
