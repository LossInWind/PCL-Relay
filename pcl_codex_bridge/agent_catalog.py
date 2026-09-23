"""Project the upstream catalog into native roles, without a second model catalog.

The five featured spawn overrides are not an exhaustive capability list. Native
custom roles give each eligible catalog entry an exact model binding. Effort and
sandbox are deliberately inherited, not pinned by the generated role.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
from pathlib import Path

from .client_config import AGENT_ROLE_MARKER, codex_home

MARKER = "# >>> pcl-relay catalog native role v1 >>>"
ALIASES = {"pcl/DeepSeek-V4-Pro": "pcl-deepseek-pro",
           "pcl/DeepSeek-V4-Flash-0731": "pcl-deepseek-flash",
           "pcl/GLM-5.2": "pcl-glm", "pcl/Kimi-K3": "pcl-kimi"}


def sync_catalog_roles(home: Path | None = None) -> dict:
    home = home or codex_home()
    catalog = home / "opencodex-catalog.json"
    # Missing/invalid catalogs must never delete working roles.
    try:
        entries = json.loads(catalog.read_text())["models"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("empty catalog")
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "roles": [], "reload_required": False}
    directory = home / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    desired, roles, changed = set(), [], False
    def owned(path: Path) -> bool:
        return path.read_text().startswith((MARKER + "\n", AGENT_ROLE_MARKER + "\n"))
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("slug")
        if (not isinstance(model, str) or not model or model in seen
                or entry.get("visibility") != "list"
                or entry.get("multi_agent_version") == "disabled"):
            continue
        seen.add(model)
        stem = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
        name = ALIASES.get(model, "relay-" + stem)
        path = directory / (name + ".toml")
        if path in desired or (path.exists() and not owned(path)):
            name = "relay-" + stem + "-" + hashlib.sha256(model.encode()).hexdigest()[:8]
            path = directory / (name + ".toml")
        if path.exists() and not owned(path):
            continue
        levels = entry.get("supported_reasoning_levels") or []
        efforts = [item["effort"] for item in levels if isinstance(levels, list)
                   if isinstance(item, dict) and isinstance(item.get("effort"), str)]
        description = f"Native subagent using {model}. "
        description += ("Catalog-supported reasoning efforts: " + ", ".join(efforts) + ". " if efforts else
                        "Reasoning effort support is not reported by the catalog. ")
        description += "Specify reasoning_effort when delegating; otherwise inherit the parent. Availability requires a successful invocation."
        instructions = (f"Use {model} for this bounded delegated task. Preserve unrelated changes and follow the parent's permissions and workspace. "
                        "Report evidence and verification results; do not use pcl_delegate as a fallback.")
        content = MARKER + "\n" + "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in
                    {"name": name, "description": description, "model": model,
                     "developer_instructions": instructions}.items()) + "\n"
        if not path.exists() or path.read_text() != content:
            temporary = path.with_suffix(".toml.tmp")
            temporary.write_text(content)
            os.replace(temporary, path)
            changed = True
        desired.add(path)
        roles.append({"name": name, "model": model, "reasoning_efforts": efforts})
    if roles:
        for path in directory.glob("*.toml"):
            if path not in desired and owned(path):
                path.unlink()
                changed = True
    return {"status": "success", "roles": roles, "reload_required": changed}


def maintain_catalog_roles(config_home: Path | None = None, home: Path | None = None) -> dict:
    """Existing control-service tick: follow upstream changes, no network/timer.

    Read-only page requests never call this. Disabled integration is respected.
    No credentials, catalog, routing or model process is changed.
    """
    from .opencodex_sidecar import OPENCODEX_CONFIG_HOME
    config_home = config_home or OPENCODEX_CONFIG_HOME
    home = home or codex_home()
    try:
        config = json.loads((config_home / "config.json").read_text())
        # Upstream desired-state: absent means enabled; only explicit false is off.
        if (config.get("clientIntegrations", {}).get("codex") is False
                or (config.get("runtimeRole") == "hub"
                    and config.get("unauthenticatedLoopbackListener", {}).get("enabled") is not True)):
            return {"status": "disabled"}
        digest = hashlib.sha256((home / "opencodex-catalog.json").read_bytes()).hexdigest()
        stamp = config_home / "relay-agent-catalog.sha256"
        if stamp.exists() and stamp.read_text().strip() == digest:
            return {"status": "unchanged"}
        with (config_home / "relay-agent-catalog.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            result = sync_catalog_roles(home)
            # If upstream replaced the file during projection, retry next tick.
            if result["status"] == "success" and hashlib.sha256((home / "opencodex-catalog.json").read_bytes()).hexdigest() == digest:
                stamp.write_text(digest + "\n")
            return result
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"status": "unavailable", "error": type(exc).__name__}
