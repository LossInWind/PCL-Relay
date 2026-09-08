# GUI reliability and compact settings acceptance

## Boundaries

This change builds on routing dashboard commit `4c2dca5`. OpenCodex transport,
credentials, history, user projects and Haichen Services are outside the change.
App start and refresh only read state. Login-item registration is an explicit
advanced action. Paid capability tests require a confirmation sheet from every
UI entry point, including the menu bar and keyboard shortcut.

## Architecture

- `CheckEvidence` retains observation time, previous success and failure, and
  ignores stale completion generations. Concurrent readers of a section join
  one in-flight check; a full refresh awaits all sections without fail-fast.
- `AgentSelectionState` keeps confirmed and pending intent separate. Writes
  serialize, coalesce rapid changes, read back persisted aliases and expose an
  explicit retry. A readback failure is unconfirmed, not a claimed disk rollback.
- One `RoutingSnapshot` provides device rows and the upgrade preview. Installed
  SSH targets are not conflated with synchronized update recipients. Update
  notifications are not presented as verified runtime upgrades.
- The three pages share system appearance and persistent operation feedback.
  The model list is not duplicated; flow diagrams do not contain sync edges.
- `SettingsWindowPresenter` owns one retained window. Closing it does not quit;
  reopening raises that window. Explicit quit does not restart the application.

## Tests and local packaging

Run `swift test` and `python3 -m unittest discover -s tests -q`. Tests inject
commands and temporary registries; they do not intentionally fail real servers.
The DEBUG `--render-routing-preview` fixture supports `--preview-section models`,
`--preview-section portal`, `--narrow` and `--dark` without live services.

`zsh scripts/update_macos_gui.sh /unused/path/PCL-Relay.app` creates a signed
candidate from the installed bundle. It updates the UI and its matching control
CLI (the old installed CLI lacks `routes snapshot`), and verifies that the entire
OpenCodex and embedded Python runtime trees remain unchanged. It does not install
user services, start the candidate or replace the installed application.

Before installing, save the old app, record model-service PID/start identity and
hash config/auth files without printing their contents. After GUI-only replacement,
check the build identity, all three pages, confirmation cancellation, window close
and reopen, and read-only refresh. Compare the same hashes and service identity.
Do not claim remote upgrades or fresh paid model E2E acceptance from this UI test.

## Rollout

Local acceptance only. Keep the previous signed application for rollback; rollback
must not restore old user preferences or overwrite user projects. Do not push the
new UI/control package to other devices automatically. A later full release should
use the canonical component version and include `PCLBuildCommit` in its app bundle.

## Local acceptance record — 2026-09-08

- Installed `/Applications/PCL Relay.app`: component version `2.5.12`, GUI build
  `7f457a0`, GUI-only update. Reliability commit: `cd67d26`; UI commit: `7f457a0`.
- Previous signed app retained at
  `.build/app-install-backups/PCL Relay.app.before-ui-7f457a0`.
- Passed: 31 Swift tests, 205 Python tests, shell syntax, whitespace checks,
  release build and strict/deep code-signature verification. Packaged OpenCodex
  and Python runtime trees were unchanged.
- Inspected isolated light/dark previews at 960×760. Live checks covered all
  three pages, model search, capability-test confirmation and cancellation
  (including keyboard shortcut), portal connectivity feedback, upgrade recipient
  preview, full refresh completion, path-to-device-detail navigation, window close
  and Finder reopen. No real model-selection writes or remote upgrades were made.
- Config fingerprints remained identical for Codex config/auth, model registry,
  OpenCodex config and SSH config. Model-service PID `49816` and its start time
  `2026-09-08 13:13:08` remained unchanged. Local `/healthz` reported OpenCodex
  `2.46.0` healthy after GUI replacement and refresh.
- The snapshot showed five device rows, four online, two pending runtime-version
  upgrades and BUPT unreachable. No synchronized update peers were registered;
  the upgrade preview correctly listed zero notification recipients and explained
  the missing synchronization relationship instead of treating SSH installation
  as upgrade eligibility.

### Initial acceptance gate (20:24; follow-up below supersedes status)

The configured gateway's model-catalog probe returned HTTP 500 with **both** the
original and new bundled control CLI. This is an existing endpoint failure, not
a demonstrated difference introduced by the GUI update. The GUI exposes it rather
than equating gateway reachability with model-generation health.

No fresh paid generation requests were sent. Historical capability results are
not fresh end-to-end evidence. Therefore fresh official/PCL generation, streaming,
tool calls and native-agent traffic are not claimed as accepted in this local UI
run. Remote deployment and GitHub release remain withheld; investigate the
catalog endpoint separately before extending rollout. Remote component versions
and the offline BUPT node were not changed.

## Completion follow-up — 2026-09-08 21:24

Installed and manually verified final local GUI build **`243bdf6`** (component
version remains `2.5.12`). The previous `7f457a0` app is retained at
`.build/app-install-backups/PCL Relay.app.before-ui-243bdf6` in addition to the
original backup. No remote app or service was upgraded, and no GitHub release
was published; those actions remain outside this local-only delivery.

### Additional root-cause findings and fixes

- Catalog shrink exposed a real save bug: choices were serialized only from the
  latest discovered list, so an already selected model missing upstream could be
  omitted from another save. The displayed/serializable list now includes saved
  definitions and selected built-in roles. Missing definitions stop a save before
  any write rather than silently dropping aliases. Latest-catalog absence is
  explicitly labelled and never automatically disables a user's selection.
- HTTP diagnostics now retain bounded, redacted structured upstream errors and
  status codes. Raw bodies/HTML and headers are not exposed. No new request retry
  or data-plane behavior was added.
- 33 Swift and 208 Python tests passed, including catalog shrink, saved custom
  aliases, unknown definitions, redaction and non-retry tests. Final release build
  and strict/deep signature verification passed; bundled OpenCodex and Python
  runtime trees remain identical to the previous app.

### Live request evidence

| Check | Actual outcome |
| --- | --- |
| Gateway catalog, IP and configured MagicDNS URL | HTTP 200; 10 advertised models; final GUI shows zero endpoint-check failures |
| Official `gpt-5.6-sol`, ephemeral read-only Codex request | Returned `OFFICIAL_OK`; no model-generated tool use |
| `pcl/GLM-5.2` through local OpenCodex | Streaming, v2 compaction, remembered-checkpoint tool/file operation and continuation passed; 26.60 s |
| `pcl/DeepSeek-V4-Flash-0731` through local OpenCodex | Same four checks passed; 20.29 s |
| `pcl/DeepSeek-V4-Pro` through local OpenCodex | Same four checks passed on follow-up; 59.14 s |
| `pcl/Kimi-K3` | HTTP 503; upstream code `model_not_found`: no available channel under group `pcl` |

The first DeepSeek Pro run produced a valid tool result but failed the exact final
acknowledgement criterion. The test previously gave an underspecified checkpoint
task before compaction. Its instructions now define the bounded verification task
and explicitly acknowledge the completed tool result; the strict result criterion
was retained. The follow-up passed. This is not recorded as a protocol repair.

The earlier HTTP 500 stopped reproducing without any gateway or model-service
restart. Existing access logs confirm the historical failures but do not retain
their upstream body, so the upstream internal cause cannot be established
retrospectively. Kimi's current rejection was confirmed independently on both the
local OpenCodex route and the gateway Chat Completions endpoint. Restoring that
channel requires upstream service/account administration; the GUI cannot do it.

### Final no-side-effect and UI checks

Before the explicit model-directory refresh, all five configuration fingerprints
and model-service process identity matched the follow-up baseline. The subsequent
GUI directory refresh updated only catalog metadata in the registry: all four
selected aliases were retained. Codex config/auth, OpenCodex config and SSH config
fingerprints stayed identical. Local model PID remains `49816`, started
`2026-09-08 13:13:08`; remote gateway PID remains `2402222`, started September 7.

The installed app was reopened through Finder. Its routing page shows build
`243bdf6`, five unique rows, four online devices and zero endpoint-check failures.
The refreshed model page visibly retains Kimi with the missing-catalog warning and
four enabled choices. Historical capability times remain labelled as historical;
the external verification script intentionally does not forge or overwrite GUI
capability-test records. Save failure/rapid-toggle behavior is tested against
isolated fixtures, not live user configuration. Native-agent configuration
generation is covered by the suite; a fresh native spawned-child run is not claimed
by this follow-up. Kimi is explicitly excluded from successful live acceptance.
