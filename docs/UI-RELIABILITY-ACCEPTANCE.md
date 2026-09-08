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

### Remaining acceptance gate

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
