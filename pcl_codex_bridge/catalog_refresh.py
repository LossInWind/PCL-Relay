"""Schedule upstream catalog maintenance; never inject config or restart Codex.

OpenCodex remains the sole catalog writer and model metadata authority. Relay
only provides bounded scheduling, cross-process exclusion and visible evidence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from .client_config import codex_home, find_codex
from .opencodex_sidecar import OPENCODEX_CONFIG_HOME, installed_runtime, invoke_sidecar

INTERVAL = 3600
RETRY_INTERVAL = 300


def _read(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog_status(home: Path = OPENCODEX_CONFIG_HOME) -> Dict[str, Any]:
    state = _read(home / "relay-catalog-refresh.json")
    auto = _read(home / "config.json").get("catalogAutoRefresh")
    auto = auto if isinstance(auto, dict) else {}
    return {"status": "never_checked", **state,
            "auto_refresh_enabled": auto.get("enabled") is True,
            "auto_refresh_interval_minutes": auto.get("intervalMinutes", 60),
            "stale": time.time() - state.get("last_success", 0) >= INTERVAL}


def _identity(runtime: Any) -> str:
    # Do not read/log credentials. Replacement or rotation invalidates the TTL.
    paths = [codex_home() / "auth.json", codex_home() / "config.toml",
             OPENCODEX_CONFIG_HOME / "config.json"]
    binary = find_codex()
    if binary:
        paths.append(Path(binary))
    stamps = []
    for path in paths:
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((str(path), None))
    return hashlib.sha256(json.dumps([runtime.commit, stamps]).encode()).hexdigest()


def refresh_catalog(*, if_due: bool = False, home: Path = OPENCODEX_CONFIG_HOME,
                    now: float | None = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    home.mkdir(parents=True, exist_ok=True)
    with (home / "relay-catalog-refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**catalog_status(home), "status": "busy"}
        previous = _read(home / "relay-catalog-refresh.json")
        runtime = installed_runtime()
        identity = _identity(runtime)
        if if_due and previous.get("identity") == identity:
            interval = INTERVAL if previous.get("status") == "success" else RETRY_INTERVAL
            if now - previous.get("checked_at", 0) < interval:
                return {**catalog_status(home), "due": False}
        # This upstream entry only writes catalog/cache. Do NOT use `sync`,
        # integration on/off, or /api/sync: those also reconcile login/history.
        script = '''
import {loadConfig} from "./src/config.ts";
import {shouldSyncCodexOnStart} from "./src/codex/desired-state.ts";
import {refreshCodexModelCatalog} from "./src/codex/refresh.ts";
const config=loadConfig();
if (!shouldSyncCodexOnStart(config)) {
  console.log(JSON.stringify({status:"disabled"}));
} else {
  const result=await refreshCodexModelCatalog(config);
  console.log(JSON.stringify({status:result.skippedReason ? "disabled" :
    result.catalogExists && result.refreshOutcome !== "refused" ? "success" : "failed",
    catalog_written:result.catalogWritten, cache_synced:result.cacheSynced}));
}
'''
        state = {**previous, "checked_at": now, "identity": identity,
                 "upstream_version": runtime.version, "status": "failed",
                 "error": None, "reload_required": False}
        try:
            result = invoke_sidecar(runtime, ["--eval", script], home, 120)
            if result.returncode:
                raise RuntimeError("upstream_catalog_refresh_failed")
            result_data = json.loads(result.stdout.strip().splitlines()[-1])
            if result_data.get("status") not in {"success", "disabled"}:
                raise RuntimeError("upstream_catalog_refresh_incomplete")
            state.update(result_data)
            if state["status"] == "success":
                state["last_success"] = now
                state["reload_required"] = bool(result_data.get("catalog_written"))
        except Exception as exc:
            # Upstream stderr may contain provider URLs or account identifiers.
            state["error"] = (str(exc) if isinstance(exc, RuntimeError)
                              else type(exc).__name__)
        target = home / "relay-catalog-refresh.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, target)
        return state
