# OpenCodex 2.63.0 / Relay 2.5.15

Upstream-first adoption of the full v2.63.0 release, commit
`96b1406cb63e429cec8d2e3914af4ba99f2e37b9`, tree
`22639518db6eab88525f7f0932cc4a81a7573b6d`.

Evidence: https://github.com/lidge-jun/opencodex/releases/tag/v2.63.0
The shipped `gpt6-native-rows.test.ts` verifies Sol low..ultra and Luna low..max;
`catalog-auto-refresh.ts` implements issue #3630 as an opt-in hourly scheduler.
Relay enables that scheduler at provisioning, rather than adding a second daemon.
Manual refresh calls the upstream catalog-only implementation, not broad `ocx sync`
which also reconciles configuration/history. Passive App refresh remains read-only.

The old 2.48.0 runtime and Codex 0.155.0-alpha.9.2 bundled catalog lacked the new
Sol/Luna rows. PCL discovery alone never refreshed the official catalog. Both
causes must be addressed; changing only an on-disk model name is insufficient.

Validation is recorded separately from deployment. Existing sessions must not be
terminated to update their startup-only model catalog. No other device is upgraded
implicitly as part of local acceptance.
