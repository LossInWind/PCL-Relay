from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

from .http_client import request_json
from .models import DEFAULT_GATEWAY_URL, configured_agents, load_registry, save_registry
from .opencodex_sidecar import installed_runtime, sidecar_health, switch_pcl_gateway


def normalize_gateway_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized.endswith("/v1"):
        normalized += "/v1"
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise RuntimeError("Gateway URL must look like http://<reachable-host>:<port>/v1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Gateway URL must not contain credentials, query parameters, or fragments")
    return normalized


def gateway_id(url: str) -> str:
    return hashlib.sha256(normalize_gateway_url(url).encode("utf-8")).hexdigest()[:16]


def probe_gateway(url: str, timeout: int = 15) -> Dict[str, Any]:
    normalized = normalize_gateway_url(url)
    started = time.monotonic()
    health = request_json(normalized.rsplit("/v1", 1)[0] + "/healthz", timeout=timeout)
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError("Endpoint did not return a healthy PCL gateway response")
    if health.get("service") not in {None, "pcl-codex-gateway"}:
        raise RuntimeError("Endpoint is not a PCL gateway")
    models = request_json(normalized + "/models", timeout=max(timeout, 30))
    entries = models.get("data") if isinstance(models, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("PCL gateway did not return a compatible model catalog")
    return {
        "url": normalized,
        "healthy": True,
        "model_count": len(entries),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "models": entries,
        "service": str(health.get("service") or "pcl-codex-gateway"),
        "version": str(health.get("version") or ""),
    }


def _gateway_records(registry: Dict[str, Any]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen = set()
    raw = registry.get("gateways")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            url = normalize_gateway_url(str(item.get("url") or ""))
        except RuntimeError:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(
            {
                "id": gateway_id(url),
                "name": str(item.get("name") or urlparse(url).hostname or "PCL gateway")[:80],
                "url": url,
                "added_at": str(item.get("added_at") or ""),
            }
        )
    selected = normalize_gateway_url(str(registry.get("gateway") or DEFAULT_GATEWAY_URL))
    if selected not in seen:
        result.append(
            {
                "id": gateway_id(selected),
                "name": str(urlparse(selected).hostname or "PCL gateway")[:80],
                "url": selected,
                "added_at": "",
            }
        )
    return result


def list_gateways(probe: bool = False, timeout: int = 15) -> Dict[str, Any]:
    registry = load_registry()
    selected = normalize_gateway_url(str(registry.get("gateway") or DEFAULT_GATEWAY_URL))
    records: List[Dict[str, Any]] = []
    for item in _gateway_records(registry):
        record: Dict[str, Any] = {
            **item,
            "selected": item["url"] == selected,
            "healthy": None,
            "model_count": None,
            "latency_ms": None,
            "error": "",
        }
        if probe:
            try:
                check = probe_gateway(item["url"], timeout)
                record.update({key: check[key] for key in ("healthy", "model_count", "latency_ms")})
            except Exception as exc:
                record["healthy"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return {
        "selected_gateway": selected,
        "gateways": records,
        "count": len(records),
        "network_managed": False,
    }


def add_gateway(url: str, name: str = "") -> Dict[str, Any]:
    check = probe_gateway(url)
    normalized = check["url"]
    registry = load_registry()
    records = _gateway_records(registry)
    label = name.strip()[:80] or str(urlparse(normalized).hostname or "PCL gateway")
    existing = next((item for item in records if item["url"] == normalized), None)
    if existing:
        existing["name"] = label
    else:
        records.append(
            {
                "id": gateway_id(normalized),
                "name": label,
                "url": normalized,
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
    registry["gateways"] = records
    from .topology_sync import mark_topology_changed

    mark_topology_changed(registry)
    save_registry(registry)
    return {"added": True, "gateway": {**existing, **check} if existing else {**records[-1], **check}}


def _resolve_gateway(registry: Dict[str, Any], target: str) -> Dict[str, str]:
    records = _gateway_records(registry)
    return next(
        (
            item
            for item in records
            if target in {item["id"], item["url"], item["url"].removesuffix("/v1")}
        ),
        {},
    )


def select_gateway(target: str) -> Dict[str, Any]:
    registry = load_registry()
    selected = _resolve_gateway(registry, target)
    if not selected:
        # A URL may be selected directly; it is validated and added atomically.
        check = probe_gateway(target)
        selected = {
            "id": gateway_id(check["url"]),
            "name": str(urlparse(check["url"]).hostname or "PCL gateway")[:80],
            "url": check["url"],
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    else:
        check = probe_gateway(selected["url"])

    previous = normalize_gateway_url(str(registry.get("gateway") or DEFAULT_GATEWAY_URL))
    runtime_applied = False
    runtime = None
    try:
        runtime = installed_runtime()
    except (FileNotFoundError, RuntimeError, OSError):
        runtime = None

    models = [str(value["model"]) for value in configured_agents(registry).values()]
    if runtime is not None:
        try:
            healthy = sidecar_health(runtime).get("ok") is True
        except Exception:
            healthy = False
        if healthy:
            try:
                switch_pcl_gateway(runtime, selected["url"], models)
                runtime_applied = True
            except Exception as switch_error:
                rollback_error = None
                if previous != selected["url"]:
                    try:
                        switch_pcl_gateway(runtime, previous, models)
                    except Exception as exc:
                        rollback_error = exc
                detail = f"; rollback failed: {rollback_error}" if rollback_error else ""
                raise RuntimeError(
                    f"OpenCodex rejected the PCL gateway change; registry was not changed{detail}: "
                    f"{switch_error}"
                ) from switch_error

    records = _gateway_records(registry)
    if not any(item["url"] == selected["url"] for item in records):
        records.append(selected)
    registry["gateways"] = records
    registry["gateway"] = selected["url"]
    registry["available_models"] = {
        str(item.get("id")): item for item in check["models"] if isinstance(item, dict) and item.get("id")
    }
    registry["gateway_selected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    from .topology_sync import mark_topology_changed

    mark_topology_changed(registry)
    save_registry(registry)
    return {
        "selected": True,
        "gateway": selected,
        "previous_gateway": previous,
        "model_count": check["model_count"],
        "runtime_applied": runtime_applied,
        "service_restarted": False,
        "official_route_changed": False,
    }


def remove_gateway(target: str) -> Dict[str, Any]:
    registry = load_registry()
    selected_url = normalize_gateway_url(str(registry.get("gateway") or DEFAULT_GATEWAY_URL))
    record = _resolve_gateway(registry, target)
    if not record:
        raise RuntimeError("Unknown PCL gateway")
    if record["url"] == selected_url:
        raise RuntimeError("Select another PCL gateway before removing the active one")
    records = [item for item in _gateway_records(registry) if item["url"] != record["url"]]
    registry["gateways"] = records
    from .topology_sync import mark_topology_changed

    mark_topology_changed(registry)
    save_registry(registry)
    return {"removed": True, "gateway": record, "selected_gateway": selected_url}
