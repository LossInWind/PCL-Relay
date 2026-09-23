# Relay 2.5.15 acceptance — 2026-09-23

Release source: `2f49135`; complete upstream OpenCodex 2.63.0, commit
`96b1406cb63e429cec8d2e3914af4ba99f2e37b9`. The vendored tree matches upstream.
Release: https://github.com/LossInWind/PCL-Relay/releases/tag/v2.5.15

## Verified

- Relay tests: 228 passed in the working checkout (including pre-existing,
  uncommitted loopback-policy tests; those changes are NOT packaged).
- Upstream focused catalog/scheduler/refresh tests: 366 passed; typecheck passed.
- Swift release build passed. Packages built from a clean `2f49135` worktree.
- Isolated GPT-6 Sol and Luna requests returned HTTP 200 and response.completed.
- All four PCL models passed streaming, compaction, checkpoint/file and tool
  round-trip acceptance against an isolated new runtime.
- Local catalog contains GPT-6 Sol/Luna plus the four selected PCL models.
  Local Codex config/auth contents were unchanged by catalog refresh.
- Local GUI visibly reports 2.5.15 / 2f49135.

## Artifact checksums

- macOS: `933ce79f8adee04e9a70e946145bba80fcb2d43ae4fda3a6d33a6adfc6a3d8be`
- Linux x86_64: `d831692c9b1153fc97596261c0ca0c149d854a92d4781bc3b85ab6634ec0c531`

## Deployment evidence and remaining gates

- Local Mac: GUI/client 2.5.15 installed; after explicit user approval, runtime
  2.63.0 activated with pinned Bun 1.3.14 (verified live PID 2003). Codex config
  and auth hashes were unchanged across activation. Both GPT-6 Sol and PCL
  DeepSeek Flash returned HTTP 200 with response.completed against the actual
  port 15725 service. Hourly upstream catalog refresh is enabled. Current windows
  may still need to reopen after work finishes because the catalog is startup-only.
- Kai Mac: 2.5.15 downloaded from GitHub, verified and installed; model runtime
  2.63.0 verified live; new catalog refreshed; a PCL streaming request completed.
  A pre-existing loopback native-router on 15726 overlaps the control listener;
  it was not killed. Remote GUI reopening is not verified.
- 3070Ti: GitHub download failed, so the identical verified Mac cache was used.
  Client 2.5.15 installed and model runtime 2.63.0 verified live; catalog refreshed.
  Initial live PCL requests returned 502 although old/new isolated runtimes and
  direct gateway requests succeeded. Pinning Bun 1.3.14 alone did not resolve it;
  neither did provider direct mode plus adding the MagicDNS host to noProxy.
  Reusing the verified local gateway at `127.0.0.1:15722/v1` resolved the live
  failure (HTTP 200, response.completed). The exact supervised MagicDNS transport
  difference remains unproven; do not attribute it solely to Bun. The PCL provider
  is direct and GPT's proxy is unchanged. The actual process uses pinned Bun
  1.3.14, not the build dependency. This manual deployment initially omitted the
  runtime override already supplied by Relay's invoke_sidecar; that was corrected.
- A6000 Pod: package verified and staged under the persistent `/home/zhc` tree.
  No existing Relay/OpenCodex installation or listener was found. No new model
  route, authentication or supervisor was installed implicitly.
- BUPT shared and 10.112.205.245: SSH timed out; shared fixed-IP recovery also
  timed out. Not upgraded and not counted as successful.

No ChatGPT/VS Code process was terminated, and no SSH, Clash, SMB or project
files were changed. Package installation is not proof of runtime activation.
