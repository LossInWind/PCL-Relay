# PCL Relay 2.5.17 acceptance

Source: `c77cc03` on `opencodex-sidecar`. Published release:
https://github.com/LossInWind/PCL-Relay/releases/tag/v2.5.17

## Final user-facing scope

The final requirement supersedes the client-specific export experiment:
show only **OpenAI-compatible provider** connection fields and instructions.
No OpenCode/Pi export buttons, no legacy-version choices, and no claim that one
client's JSON schema is universal. The working OpenCode configuration already
installed locally is retained and not overwritten.

The page provides one endpoint and placeholder credential for all compatible
models, copyable raw model IDs, setup steps, error guidance, and a clearly
optional request example. Tailnet authorization is distinct from the dummy key.

## Verification

- 36 XCTest tests passed, including universal guide/model deduplication and URL safety.
- 232 Python tests passed in the clean release checkout.
- Release macOS build and strict deep signature verification passed.
- Installed Mac GUI opened and inspected with screenshot and accessibility tree:
  title, full wrapped address, dummy-key explanation, eight model IDs, generic
  instructions; no application-specific or old-version entry remains.
- OpenCode's actual PCL request returned `PCL_OK` before packaging; no data-plane
  code changed after that acceptance.
- Vendored upstream tree remains `22639518db6eab88525f7f0932cc4a81a7573b6d`.
- Linux runtime and Python code are byte-identical to the verified 2.5.16 package
  except VERSION. Repacked on Linux from the checksum-verified prior bundle.
- GitHub reports both archives uploaded with matching SHA-256; metadata and
  checksum sidecars uploaded as well.

## Artifacts

- macOS: `e45fb93e6834d8c52d3d426b148f23689aacc5fad66bb21fee248e1b007bdba5`
- Linux x86_64: `d24f3b46954535c964261c34d948d387d55ea7de453148bbe2407665b9459c81`

## Devices

- Local Mac: installed GUI 2.5.17 / c77cc03, control heartbeat 2.5.17.
  OpenCodex PID 2003 unchanged; previous app retained in local backup.
- 3070Ti: GitHub download failed; identical checked Mac asset used. Installed
  CLI and control heartbeat 2.5.17; only control service restarted. OpenCodex
  PID 1747 unchanged. Existing gateway process still reports 2.5.15 deliberately;
  it was not restarted to change a version label.
- Kai Mac: GitHub download succeeded; app installed and control heartbeat 2.5.17.
  GUI was not running, so next user launch loads new GUI. Model process untouched.
- A6000: verified package under PVC `/home/zhc/.cache/pcl-relay/releases/2.5.17`.
  No prior active Relay found; install resources with HOME=/home/zhc, not ephemeral
  root storage. Do not activate routes, create a daemon or change Codex configuration.
- BUPT A100 and 10.112.205.245: SSH connection timeouts; pending, not upgraded.

These checks validate this UI-only release and preserve the running data plane;
they are not a fresh all-model, all-effort, compaction/concurrency certification.
