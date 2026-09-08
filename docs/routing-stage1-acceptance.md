# Routing simplification: stage 1 acceptance record

Date: 2026-09-08. Status: **implementation draft; release blocked**.
Installed version remains 2.5.12. No local production application replacement,
remote deployment, release tag or version bump was performed for this change.

## Implemented scope

- Compact overview, configured model paths, one identity-aware device list.
- Stable scrollable layout, system typography/colors, light and dark appearance.
- Separate device/control reachability, endpoint reachability and model-call evidence.
- Unknown path/runtime remains unknown; installed version never substitutes for runtime.
- Duplicate verified aliases merge by node identity; unverified same-name registrations do not.
- Single-device probes; existing deployment, update, synchronization, removal,
  route application and proxy controls retained under details/advanced controls.
- Refresh no longer stages local components. Preparation is an explicit action.
- Optional runtime metadata is read-only and backward-compatible with older peers.
- Endpoint evidence must match the configured URL; hostname/IP equivalence is not guessed.

The diagram reports configured endpoints, not an observed packet trace. It does not
infer additional relay/bridge hops from heartbeat or deployment relationships.
Last-success endpoint timestamps currently survive failed checks within the running
app, not application relaunch. End-to-end remote path evidence and durable historical
probe records still require acceptance work. This is not a claim that stage 1 is finished.

## Verified evidence

- Swift package: 20 tests passed, including identity, unknown-runtime, stable-order
  and endpoint-evidence regressions.
- Python suite: 204 tests passed; changed Python files pass Ruff.
- Offline light/dark previews rendered with local/Kai/3070Ti/A6000/BUPT fixtures,
  including offline and installed-versus-running version discrepancy scenarios.
- Real local OpenCodex requests: GLM-5.2, DeepSeek-V4-Pro,
  DeepSeek-V4-Flash-0731 and Kimi-K3 each passed streaming text, compaction,
  checkpoint/file fixture and tool round-trip checks.
- Read-only route/runtime/peer/deployment probes left protected Codex authentication,
  Codex configuration and OpenCodex configuration hashes unchanged. The model-service
  process identity before and after was unchanged.

Reproduction:

```sh
swift test
python3 -m unittest discover -s tests
python3 scripts/verify_live_models.py --compaction-version 2
.build/debug/PCLCodexManager --render-routing-preview /tmp/pcl-routing-light.png
.build/debug/PCLCodexManager --render-routing-preview /tmp/pcl-routing-dark.png --dark
```

Preview mode is DEBUG-only and does not start the installed application's lifecycle.
Live model verification consumes API tokens and should be run deliberately.
Raw local evidence is kept under ignored `.build/routing-*` files; private device
addresses and configuration hashes are not published in this record.

## Failed release gate

A real native `pcl-deepseek-pro` subagent launch failed before execution with HTTP 400,
`unreadable_encrypted_agent_task`: the V2 worker task was encrypted for the native
ChatGPT backend and unavailable to the selected provider. This is not a successful
native-agent test and is not repaired by changing the routing page.

The error exists in the unchanged vendored OpenCodex response path. No vendor protocol
code was edited. Resolving that compatibility issue requires a separately scoped
investigation, consistent with the prohibition on protocol changes in this UI plan.

Official GPT standalone requests and desktop/Remote-SSH checks of login, history,
model selection persistence and native-agent behavior have not all been completed.
Keyboard interaction, narrow-window and installed-app UI acceptance also remain.

## Release decision and stage 2

**Do not publish or expand deployment.** No failure injection, process termination,
network changes or upgrades were performed on research servers.

Stage 2 (fixed-version persistent update campaign, verified cache fallback, safe
restart, runtime verification and rollback) has not started. The stage-1 upgrade
entry point still uses existing mechanisms and explicitly does not claim installation
success proves a completed runtime upgrade.

Resume only after resolving the native-agent gate and completing remaining local
acceptance; then follow the requested local → 3070Ti → A6000/Kai rollout order.
