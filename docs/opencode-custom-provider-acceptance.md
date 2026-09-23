# OpenCode custom-provider acceptance — 2026-09-23

## Observed failure and boundary

The installed OpenCode desktop and bundled CLI report 2.0.15. In the installed
app.asar, the custom-provider form's mutation function directly throws the
localized `provider.custom.unavailable` error. Filling all fields and submitting
therefore cannot save this provider through this UI. This observation applies to
this installed build, not all OpenCode versions or alternative integration APIs.

Using the same gateway Base URL and placeholder Bearer `pcl-tailnet`, a bounded
DeepSeek-V4-Flash-0731 streaming Chat Completions request returned HTTP 200,
content and `[DONE]` (40 total tokens). This verifies that request path, not every
model, tool-calling behavior, or the OpenCode end-to-end integration.

The local OpenCode server's OpenAPI schema also differs from the public
`provider` JSON example. Do not blindly write that legacy/documented shape into
this installation or claim a file-based workaround without testing it.

Reference: https://opencode.ai/docs/providers/#custom-provider

## Relay-owned correction

| Before | After | Why |
| --- | --- | --- |
| Address and export buttons without form mapping | Provider ID/name, shared fields, expandable copyable model IDs | Users can map values to actual fields |
| Export looked universally usable | Version-scoped warning beside OpenCode export | Documentation-format export is not client compatibility proof |
| No completion criterion | Provider visible, models visible, explicitly selected model returns a short reply | Copy/save success is not inference success |
| No error guidance | Client limitation vs network/auth/model-ID errors separated | Avoid unrelated proxy or server changes |

No network policy, OpenCode binary, credentials, sessions, gateway runtime or
upstream OpenCodex source was changed. Release build and 37 XCTest tests passed.
These UI changes are source/build verified; they are not yet a published or
installed release, and OpenCode integration remains blocked at its save entry.
