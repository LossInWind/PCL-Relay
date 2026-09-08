from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .models import DEFAULT_GATEWAY_URL, configured_agents, load_registry, save_registry


SYNC_PROTOCOL = "pcl-relay-topology/1"
SYNC_SCHEMA = 1
DEFAULT_SYNC_PORT = 15726
RELEASE_ROUTE_PREFIX = "/relay/v1/releases/"
STARTED_AT = time.time()
TAILNET_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


def _trusted_sync_source(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or any(address in network for network in TAILNET_NETWORKS)


def _token_optional_listen(host: str) -> bool:
    if host in {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}:
        return True
    try:
        return any(ipaddress.ip_address(host) in network for network in TAILNET_NETWORKS)
    except ValueError:
        return False


def normalize_control_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise RuntimeError("Relay control URL must look like http://<reachable-host>:<port>")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Relay control URL must not contain credentials, query parameters, or fragments")
    return candidate


def _sync_metadata(registry: Dict[str, Any]) -> Dict[str, Any]:
    raw = registry.get("relay_sync")
    metadata = dict(raw) if isinstance(raw, dict) else {}
    metadata.setdefault("node_id", str(uuid.uuid4()))
    metadata.setdefault("node_name", socket.gethostname())
    revision = metadata.get("revision")
    if not isinstance(revision, dict):
        revision = {"counter": 0, "origin": metadata["node_id"]}
    revision["counter"] = max(0, int(revision.get("counter") or 0))
    revision["origin"] = str(revision.get("origin") or metadata["node_id"])
    metadata["revision"] = revision
    peers = metadata.get("peers")
    metadata["peers"] = peers if isinstance(peers, list) else []
    return metadata


def mark_topology_changed(registry: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _sync_metadata(registry)
    previous = metadata["revision"]
    metadata["revision"] = {
        "counter": int(previous["counter"]) + 1,
        "origin": metadata["node_id"],
    }
    metadata["changed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    registry["relay_sync"] = metadata
    return registry


def _gateway_document(registry: Dict[str, Any]) -> List[Dict[str, str]]:
    from .routes import _gateway_records

    return [
        {key: str(item.get(key) or "") for key in ("id", "name", "url", "added_at")}
        for item in _gateway_records(registry)
    ]


def topology_document(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = registry if isinstance(registry, dict) else load_registry()
    definitions = configured_agents(data)
    selected_agents = data.get("selected_agents")
    if not isinstance(selected_agents, list):
        selected_agents = list(definitions)
    return {
        "schema": SYNC_SCHEMA,
        "gateway": str(data.get("gateway") or DEFAULT_GATEWAY_URL).rstrip("/"),
        "gateways": _gateway_document(data),
        "selected_agents": [str(value) for value in selected_agents],
        "agent_definitions": definitions,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def topology_envelope(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = registry if isinstance(registry, dict) else load_registry()
    metadata = _sync_metadata(data)
    if data.get("relay_sync") != metadata:
        data["relay_sync"] = metadata
        if registry is None:
            save_registry(data)
    document = topology_document(data)
    return {
        "protocol": SYNC_PROTOCOL,
        "schema": SYNC_SCHEMA,
        "node_id": metadata["node_id"],
        "node_name": metadata["node_name"],
        "revision": metadata["revision"],
        "digest": hashlib.sha256(_canonical(document)).hexdigest(),
        "topology": document,
    }


def _revision_key(value: Any) -> Tuple[int, str]:
    revision = value if isinstance(value, dict) else {}
    return max(0, int(revision.get("counter") or 0)), str(revision.get("origin") or "")


def _read_token(path: str) -> str:
    if not path:
        return ""
    token_path = Path(path).expanduser()
    try:
        metadata = token_path.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise RuntimeError("Relay sync token file must belong to the current user and use mode 600 or 400")
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read Relay sync token file {token_path}: {exc}") from exc
    if not token:
        raise RuntimeError(f"Relay sync token file is empty: {token_path}")
    return token


def _request(url: str, token_file: str = "", body: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Dict[str, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = _read_token(token_file)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    raw = _canonical(body) if body is not None else None
    request = urllib.request.Request(url, data=raw, headers=headers, method="POST" if raw is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Relay sync HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Relay sync endpoint returned a non-object document")
    return payload


def _release_route(version: str, asset_name: str) -> str:
    return RELEASE_ROUTE_PREFIX + "/".join(
        urllib.parse.quote(value, safe="") for value in (version, asset_name)
    )


def _release_route_parts(path: str) -> Optional[Tuple[str, str]]:
    if not path.startswith(RELEASE_ROUTE_PREFIX):
        return None
    parts = path[len(RELEASE_ROUTE_PREFIX):].split("/")
    if len(parts) != 2:
        return None
    version, asset_name = (urllib.parse.unquote(value) for value in parts)
    if not version or not asset_name:
        return None
    return version, asset_name


def fetch_release_from_peers(status: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    """Fetch an immutable, same-platform asset from a verified Tailnet cache."""
    from .release_updater import (
        MAX_ASSET_BYTES,
        normalize_sha256,
        promote_verified_release,
        release_cache_path,
        verified_cached_release,
    )

    version = str(status.get("latest_version") or "")
    asset_name = str(status.get("asset_name") or "")
    expected = normalize_sha256(str(status.get("asset_digest") or ""))
    expected_size = int(status.get("asset_size") or 0)
    if expected_size <= 0 or expected_size > MAX_ASSET_BYTES:
        raise RuntimeError("Release offer has an invalid asset size")
    try:
        return {**verified_cached_release(version, asset_name), "source": "local-verified-cache"}
    except (FileNotFoundError, RuntimeError):
        pass

    destination = release_cache_path(version, asset_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors: List[str] = []
    for peer in _peer_records(load_registry()):
        temporary = destination.with_name(f".{destination.name}.part-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            headers = {"Accept": "application/octet-stream", "User-Agent": f"PCL-Relay/{__version__}"}
            token = _read_token(peer["token_file"])
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(
                peer["url"] + _release_route(version, asset_name),
                headers=headers,
                method="GET",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            received = 0
            digest = hashlib.sha256()
            with opener.open(request, timeout=timeout) as response, temporary.open("wb") as output:
                remote_digest = normalize_sha256(response.headers.get("X-PCL-SHA256", ""))
                if not hmac.compare_digest(remote_digest, expected):
                    raise RuntimeError("Peer cache advertised a different SHA-256")
                content_length = int(response.headers.get("Content-Length", "0"))
                if content_length != expected_size:
                    raise RuntimeError("Peer cache advertised a different asset size")
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_size or received > MAX_ASSET_BYTES:
                        raise RuntimeError("Peer release asset exceeds its declared size")
                    digest.update(chunk)
                    output.write(chunk)
            if received != expected_size:
                raise RuntimeError("Peer release asset was truncated")
            if not hmac.compare_digest(digest.hexdigest(), expected):
                raise RuntimeError("Peer release asset SHA-256 verification failed")
            cached = promote_verified_release(status, temporary, expected, expected_size)
            return {**cached, "source": "tailnet-peer-cache", "peer_id": peer["id"], "peer_url": peer["url"]}
        except Exception as exc:
            errors.append(f"{peer['name']}: {type(exc).__name__}: {exc}")
        finally:
            if temporary.exists():
                temporary.unlink()
    detail = "; ".join(errors) if errors else "no configured Relay peers"
    raise RuntimeError(f"No verified peer cache could provide {version}/{asset_name}: {detail}")


def heartbeat(control_url: str, token_file: str = "", timeout: int = 10) -> Dict[str, Any]:
    started = time.monotonic()
    payload = _request(normalize_control_url(control_url) + "/relay/v1/heartbeat", token_file, timeout=timeout)
    if payload.get("protocol") != SYNC_PROTOCOL or payload.get("status") != "ok":
        raise RuntimeError("Endpoint is not a compatible PCL Relay sync node")
    payload["latency_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _peer_records(registry: Dict[str, Any]) -> List[Dict[str, str]]:
    metadata = _sync_metadata(registry)
    result: List[Dict[str, str]] = []
    seen = set()
    for raw in metadata["peers"]:
        if not isinstance(raw, dict):
            continue
        try:
            url = normalize_control_url(str(raw.get("url") or ""))
        except RuntimeError:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append({
            "id": str(raw.get("id") or hashlib.sha256(url.encode()).hexdigest()[:16]),
            "name": str(raw.get("name") or urllib.parse.urlparse(url).hostname or "Relay node")[:80],
            "url": url,
            "token_file": str(raw.get("token_file") or ""),
        })
    return result


def list_peers(probe: bool = False, timeout: int = 10) -> Dict[str, Any]:
    registry = load_registry()
    metadata = _sync_metadata(registry)
    if registry.get("relay_sync") != metadata:
        registry["relay_sync"] = metadata
        save_registry(registry)
    peers: List[Dict[str, Any]] = []
    for peer in _peer_records(registry):
        record: Dict[str, Any] = {**peer, "online": None, "latency_ms": None, "error": ""}
        # Token paths are local implementation details and must never be
        # exposed through UI JSON or synchronized to another node.
        token_file = record.pop("token_file")
        if probe:
            try:
                status = heartbeat(peer["url"], token_file, timeout)
                record.update({
                    "online": True,
                    "id": str(status.get("node_id") or record["id"]),
                    "remote_name": str(status.get("node_name") or ""),
                    "latency_ms": status.get("latency_ms"),
                    "revision": status.get("revision"),
                    "digest": str(status.get("digest") or ""),
                    "version": str(status.get("version") or ""),
                })
            except Exception as exc:
                record["online"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
        peers.append(record)
    return {
        "protocol": SYNC_PROTOCOL,
        "node_id": metadata["node_id"],
        "node_name": metadata["node_name"],
        "revision": metadata["revision"],
        "digest": topology_envelope(registry)["digest"],
        "peers": peers,
        "count": len(peers),
        "network_managed": False,
    }


def add_peer(control_url: str, name: str = "", token_file: str = "") -> Dict[str, Any]:
    url = normalize_control_url(control_url)
    status = heartbeat(url, token_file)
    registry = load_registry()
    metadata = _sync_metadata(registry)
    peers = _peer_records(registry)
    record = {
        "id": str(status["node_id"]),
        "name": name.strip()[:80] or str(status.get("node_name") or urllib.parse.urlparse(url).hostname),
        "url": url,
        "token_file": str(Path(token_file).expanduser()) if token_file else "",
    }
    peers = [peer for peer in peers if peer["url"] != url and peer["id"] != record["id"]]
    peers.append(record)
    metadata["peers"] = peers
    registry["relay_sync"] = metadata
    save_registry(registry)
    return {"added": True, "peer": {key: value for key, value in record.items() if key != "token_file"}, "heartbeat": status}


def remove_peer(target: str) -> Dict[str, Any]:
    registry = load_registry()
    metadata = _sync_metadata(registry)
    peers = _peer_records(registry)
    removed = next((peer for peer in peers if target in {peer["id"], peer["url"]}), None)
    if removed is None:
        raise RuntimeError("Unknown PCL Relay sync node")
    metadata["peers"] = [
        peer for peer in peers
        if peer["url"] != removed["url"] and peer["id"] != removed["id"]
    ]
    registry["relay_sync"] = metadata
    save_registry(registry)
    return {"removed": True, "peer": {key: value for key, value in removed.items() if key != "token_file"}}


def apply_topology_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    if envelope.get("protocol") != SYNC_PROTOCOL or envelope.get("schema") != SYNC_SCHEMA:
        raise RuntimeError("Unsupported PCL Relay topology protocol")
    document = envelope.get("topology")
    if not isinstance(document, dict) or document.get("schema") != SYNC_SCHEMA:
        raise RuntimeError("Invalid PCL Relay topology document")
    expected_digest = hashlib.sha256(_canonical(document)).hexdigest()
    if not hmac.compare_digest(str(envelope.get("digest") or ""), expected_digest):
        raise RuntimeError("PCL Relay topology digest does not match its document")

    registry = load_registry()
    local = topology_envelope(registry)
    remote_key = _revision_key(envelope.get("revision"))
    local_key = _revision_key(local.get("revision"))
    if remote_key < local_key:
        return {"applied": False, "reason": "local_newer", "local_revision": local["revision"]}
    if remote_key == local_key:
        if hmac.compare_digest(str(envelope["digest"]), str(local["digest"])):
            return {"applied": False, "reason": "already_current", "local_revision": local["revision"]}
        raise RuntimeError("Topology conflict: identical revision has different content")

    from .routes import normalize_gateway_url, probe_gateway
    from .opencodex_sidecar import installed_runtime, sidecar_health, switch_pcl_gateway

    selected_url = normalize_gateway_url(str(document.get("gateway") or ""))
    raw_gateways = document.get("gateways")
    if not isinstance(raw_gateways, list):
        raise RuntimeError("Synchronized topology is missing its gateway catalog")
    gateways: List[Dict[str, str]] = []
    for raw in raw_gateways:
        if not isinstance(raw, dict):
            raise RuntimeError("Synchronized gateway catalog contains an invalid record")
        url = normalize_gateway_url(str(raw.get("url") or ""))
        gateways.append({
            "id": str(raw.get("id") or ""),
            "name": str(raw.get("name") or urllib.parse.urlparse(url).hostname or "PCL gateway")[:80],
            "url": url,
            "added_at": str(raw.get("added_at") or ""),
        })
    if selected_url not in {item["url"] for item in gateways}:
        raise RuntimeError("Synchronized selected gateway is absent from its catalog")

    definitions = document.get("agent_definitions")
    selected_agents = document.get("selected_agents")
    if not isinstance(definitions, dict) or not isinstance(selected_agents, list):
        raise RuntimeError("Synchronized topology has an invalid model selection")
    models: List[str] = []
    clean_definitions: Dict[str, Dict[str, str]] = {}
    for alias in selected_agents:
        raw = definitions.get(str(alias))
        if not isinstance(raw, dict) or not str(raw.get("model") or "").strip():
            raise RuntimeError(f"Synchronized model selection is missing definition {alias}")
        model = str(raw["model"]).strip()
        models.append(model)
        clean_definitions[str(alias)] = {
            "model": model,
            "description": str(raw.get("description") or ""),
        }

    check = probe_gateway(selected_url)
    previous_url = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    runtime = None
    try:
        runtime = installed_runtime()
    except Exception:
        pass
    if runtime is not None:
        try:
            healthy = sidecar_health(runtime).get("ok") is True
        except Exception:
            healthy = False
        if healthy:
            try:
                switch_pcl_gateway(runtime, selected_url, models)
            except Exception as switch_error:
                try:
                    switch_pcl_gateway(runtime, previous_url, [str(info["model"]) for info in configured_agents(registry).values()])
                except Exception as rollback_error:
                    raise RuntimeError(f"Synchronized route failed and rollback failed: {rollback_error}") from switch_error
                raise RuntimeError(f"Synchronized route was rejected and rolled back: {switch_error}") from switch_error

    metadata = _sync_metadata(registry)
    metadata["revision"] = {"counter": remote_key[0], "origin": remote_key[1]}
    metadata["last_applied_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    metadata["last_applied_from"] = str(envelope.get("node_id") or "")
    registry.update({
        "gateway": selected_url,
        "gateways": gateways,
        "selected_agents": [str(value) for value in selected_agents],
        "agent_definitions": clean_definitions,
        "available_models": {
            str(item.get("id")): item for item in check["models"] if isinstance(item, dict) and item.get("id")
        },
        "relay_sync": metadata,
    })
    save_registry(registry)
    return {"applied": True, "revision": metadata["revision"], "gateway": selected_url, "model_count": len(models), "service_restarted": False}


def sync_once(timeout: int = 10) -> Dict[str, Any]:
    registry = load_registry()
    peers = _peer_records(registry)
    reports: List[Dict[str, Any]] = []
    for peer in peers:
        report: Dict[str, Any] = {"id": peer["id"], "name": peer["name"], "url": peer["url"], "ok": False, "action": "none", "error": ""}
        try:
            remote = _request(peer["url"] + "/relay/v1/topology", peer["token_file"], timeout=timeout)
            local = topology_envelope()
            remote_key = _revision_key(remote.get("revision"))
            local_key = _revision_key(local.get("revision"))
            if remote_key > local_key:
                result = apply_topology_envelope(remote)
                report["action"] = "pulled" if result.get("applied") else str(result.get("reason") or "none")
            elif local_key > remote_key:
                result = _request(peer["url"] + "/relay/v1/topology", peer["token_file"], local, timeout)
                report["action"] = "pushed" if result.get("applied") else str(result.get("reason") or "none")
            elif str(remote.get("digest") or "") != str(local.get("digest") or ""):
                raise RuntimeError("Topology conflict: identical revision has different content")
            else:
                report["action"] = "current"
            report["update_action"] = "none"
            report["update_error"] = ""
            try:
                update = _request(peer["url"] + "/relay/v1/update", peer["token_file"], timeout=timeout)
                if update.get("protocol") != SYNC_PROTOCOL:
                    raise RuntimeError("Peer returned an incompatible release campaign document")
                if update.get("available") is True:
                    remote_offer = update.get("offer")
                    if not isinstance(remote_offer, dict):
                        raise RuntimeError("Peer returned an invalid release campaign")
                    installed = install_release_offer(remote_offer)
                    report["update_action"] = str(installed.get("reason") or (
                        "installed" if installed.get("installed") is True else "accepted"
                    ))
            except Exception as update_error:
                # Topology synchronization remains compatible with nodes from
                # before durable release campaigns, and update failures never
                # break gateway/model synchronization.
                report["update_action"] = "unsupported" if "HTTP 404" in str(update_error) else "failed"
                report["update_error"] = f"{type(update_error).__name__}: {update_error}"
            campaign = release_campaign_document()
            local_offer = campaign.get("offer") if campaign.get("available") is True else None
            if isinstance(local_offer, dict) and not _release_was_delivered(peer["id"], local_offer):
                try:
                    delivered = _request(
                        peer["url"] + "/relay/v1/update",
                        peer["token_file"],
                        local_offer,
                        max(timeout, 120),
                    )
                    if delivered.get("accepted") is not True:
                        raise RuntimeError("Peer did not accept the persisted release campaign")
                    _mark_release_delivered(peer["id"], local_offer, delivered)
                    report["update_action"] = "pushed-persisted-campaign"
                    report["update_error"] = ""
                except Exception as delivery_error:
                    report["update_action"] = "delivery-failed"
                    report["update_error"] = f"{type(delivery_error).__name__}: {delivery_error}"
            report["ok"] = True
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        reports.append(report)
    return {"protocol": SYNC_PROTOCOL, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "peers": reports, "ok_count": sum(1 for item in reports if item["ok"]), "count": len(reports)}


def _validate_release_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    if offer.get("protocol") != SYNC_PROTOCOL or offer.get("action") != "install-latest-release":
        raise RuntimeError("Invalid PCL Relay release offer")
    requested = str(offer.get("version") or "")
    if not requested or Path(requested).name != requested:
        raise RuntimeError("PCL Relay release offer has no version")
    origin_node_id = str(offer.get("origin_node_id") or "")
    if not origin_node_id:
        raise RuntimeError("PCL Relay release offer has no origin node")
    from .release_updater import (
        MAX_ASSET_BYTES,
        RELEASE_ASSET_NAMES,
        REPOSITORY,
        normalize_sha256,
    )
    assets = offer.get("assets")
    if not isinstance(assets, dict) or set(assets) != RELEASE_ASSET_NAMES:
        raise RuntimeError("Release offer must contain exactly the three supported platform assets")
    normalized_assets: Dict[str, Dict[str, Any]] = {}
    for asset_name in sorted(RELEASE_ASSET_NAMES):
        raw_asset = assets.get(asset_name)
        if not isinstance(raw_asset, dict) or raw_asset.get("asset_name") != asset_name:
            raise RuntimeError(f"Release offer has no manifest entry for {asset_name}")
        asset_size = int(raw_asset.get("asset_size") or 0)
        if asset_size <= 0 or asset_size > MAX_ASSET_BYTES:
            raise RuntimeError("Release offer has an invalid asset size")
        asset_sha256 = normalize_sha256(str(raw_asset.get("asset_sha256") or ""))
        asset_url = str(raw_asset.get("asset_url") or "")
        parsed_url = urllib.parse.urlparse(asset_url)
        expected_prefix = f"/{REPOSITORY}/releases/download/"
        suffix = parsed_url.path[len(expected_prefix):] if parsed_url.path.startswith(expected_prefix) else ""
        tag, separator, offered_name = suffix.partition("/")
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "github.com"
            or parsed_url.query
            or parsed_url.fragment
            or not separator
            or tag not in {requested, "v" + requested}
            or urllib.parse.unquote(offered_name) != asset_name
        ):
            raise RuntimeError("Release offer asset is not an official GitHub release URL")
        normalized_assets[asset_name] = {
            "asset_name": asset_name,
            "asset_url": asset_url,
            "asset_size": asset_size,
            "asset_sha256": asset_sha256,
        }
    if str(offer.get("source") or "") != f"github:{REPOSITORY}":
        raise RuntimeError("Release offer has an unexpected release source")
    return {
        "protocol": SYNC_PROTOCOL,
        "action": "install-latest-release",
        "version": requested,
        "source": f"github:{REPOSITORY}",
        "release_url": str(offer.get("release_url") or ""),
        "origin_node_id": origin_node_id,
        "assets": normalized_assets,
    }


def _release_offer_digest(offer: Dict[str, Any]) -> str:
    identity = {key: value for key, value in offer.items() if key != "origin_node_id"}
    return hashlib.sha256(_canonical(identity)).hexdigest()


def remember_release_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    from .release_updater import _version_tuple

    normalized = _validate_release_offer(offer)
    registry = load_registry()
    state = registry.get("relay_update")
    state = dict(state) if isinstance(state, dict) else {}
    current = state.get("offer")
    if isinstance(current, dict):
        current = _validate_release_offer(current)
        incoming_key = _version_tuple(str(normalized["version"]))
        current_key = _version_tuple(str(current["version"]))
        if incoming_key < current_key:
            return {"stored": False, "reason": "newer_campaign_already_stored", "offer": current}
        if incoming_key == current_key:
            if not hmac.compare_digest(_release_offer_digest(normalized), _release_offer_digest(current)):
                raise RuntimeError("Release campaign conflict: same version has a different manifest")
            return {"stored": False, "reason": "campaign_already_stored", "offer": current}
    state.update({
        "offer": normalized,
        "offer_digest": _release_offer_digest(normalized),
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    registry["relay_update"] = state
    save_registry(registry)
    return {"stored": True, "reason": "campaign_stored", "offer": normalized}


def release_campaign_document() -> Dict[str, Any]:
    state = load_registry().get("relay_update")
    offer = state.get("offer") if isinstance(state, dict) else None
    if not isinstance(offer, dict):
        return {"protocol": SYNC_PROTOCOL, "available": False, "offer": None}
    normalized = _validate_release_offer(offer)
    return {
        "protocol": SYNC_PROTOCOL,
        "available": True,
        "offer_digest": _release_offer_digest(normalized),
        "offer": normalized,
    }


def _release_was_accepted(offer: Dict[str, Any]) -> bool:
    state = load_registry().get("relay_update")
    accepted = state.get("accepted") if isinstance(state, dict) else None
    return isinstance(accepted, dict) and hmac.compare_digest(
        str(accepted.get("offer_digest") or ""), _release_offer_digest(offer)
    )


def _mark_release_accepted(offer: Dict[str, Any], result: Dict[str, Any]) -> None:
    registry = load_registry()
    state = registry.get("relay_update")
    state = dict(state) if isinstance(state, dict) else {}
    state["accepted"] = {
        "version": str(offer["version"]),
        "offer_digest": _release_offer_digest(offer),
        "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "installed": result.get("installed") is True,
        "restart_required": result.get("restart_required") is True,
    }
    registry["relay_update"] = state
    save_registry(registry)


def _release_was_delivered(peer_id: str, offer: Dict[str, Any]) -> bool:
    state = load_registry().get("relay_update")
    deliveries = state.get("deliveries") if isinstance(state, dict) else None
    record = deliveries.get(peer_id) if isinstance(deliveries, dict) else None
    return isinstance(record, dict) and hmac.compare_digest(
        str(record.get("offer_digest") or ""), _release_offer_digest(offer)
    )


def _mark_release_delivered(peer_id: str, offer: Dict[str, Any], result: Dict[str, Any]) -> None:
    registry = load_registry()
    state = registry.get("relay_update")
    state = dict(state) if isinstance(state, dict) else {}
    deliveries = state.get("deliveries")
    deliveries = dict(deliveries) if isinstance(deliveries, dict) else {}
    deliveries[peer_id] = {
        "version": str(offer["version"]),
        "offer_digest": _release_offer_digest(offer),
        "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "installed": result.get("installed") is True,
        "reason": str(result.get("reason") or ""),
    }
    state["deliveries"] = deliveries
    registry["relay_update"] = state
    save_registry(registry)


def install_release_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Download from GitHub first, then use a verified Tailnet peer cache."""
    normalized = _validate_release_offer(offer)
    from .release_updater import (
        _version_tuple,
        cache_release_asset,
        install_cached_release,
        release_asset_name,
    )
    requested = str(normalized["version"])
    if _version_tuple(requested) < _version_tuple(__version__):
        raise RuntimeError("PCL Relay release offer would downgrade this node")
    remembered = remember_release_offer(normalized)
    normalized = remembered["offer"]
    requested = str(normalized["version"])
    if _release_was_accepted(normalized):
        return {
            "accepted": True, "version": requested, "installed": False,
            "staged": False, "restart_required": False,
            "service_restarted": False, "codex_config_changed": False,
            "reason": "campaign_already_accepted",
        }
    if _version_tuple(requested) == _version_tuple(__version__):
        result = {
            "accepted": True, "version": requested, "installed": False,
            "staged": False, "restart_required": False,
            "service_restarted": False, "codex_config_changed": False,
            "reason": "already_latest",
        }
        _mark_release_accepted(normalized, result)
        return result

    asset_name = release_asset_name()
    raw_asset = normalized["assets"][asset_name]
    asset_size = int(raw_asset["asset_size"])
    asset_sha256 = str(raw_asset["asset_sha256"])
    asset_url = str(raw_asset["asset_url"])
    status = {
        "available": True,
        "source": str(normalized["source"]),
        "current_version": __version__,
        "latest_version": requested,
        "update_available": True,
        "release_url": str(normalized.get("release_url") or ""),
        "asset_name": asset_name,
        "asset_url": asset_url,
        "asset_size": asset_size,
        "asset_digest": "sha256:" + asset_sha256,
        "checksum_url": "",
    }
    github_error = ""
    try:
        cached = cache_release_asset(status)
        artifact_source = "github-release"
    except Exception as exc:
        github_error = f"{type(exc).__name__}: {exc}"
        cached = fetch_release_from_peers(status)
        artifact_source = str(cached.get("source") or "tailnet-peer-cache")
    result = install_cached_release(status, Path(str(cached["path"])), asset_sha256)
    response = {
        "accepted": True,
        "version": requested,
        "installed": result.get("installed") is True,
        "staged": result.get("staged") is True,
        "restart_required": result.get("restart_required") is True,
        "service_restarted": result.get("service_restarted") is True,
        "codex_config_changed": result.get("codex_config_changed") is True,
        "reason": str(result.get("reason") or ""),
        "artifact_source": artifact_source,
        "github_error": github_error,
        "verified_sha256": asset_sha256,
    }
    _mark_release_accepted(normalized, response)
    return response


def push_latest_release(timeout: int = 600) -> Dict[str, Any]:
    """Ask configured Relay peers to fetch and verify their platform asset.

    Each peer first downloads its own platform artifact from GitHub. A peer
    cache is used only after that fails, and only with the same offered digest.
    """
    from .release_updater import latest_release_manifest

    latest = latest_release_manifest()
    registry = load_registry()
    metadata = _sync_metadata(registry)
    offer = {
        "protocol": SYNC_PROTOCOL,
        "action": "install-latest-release",
        "version": str(latest["latest_version"]),
        "source": str(latest["source"]),
        "release_url": str(latest["release_url"]),
        "origin_node_id": str(metadata["node_id"]),
        "assets": latest["assets"],
    }
    offer = remember_release_offer(offer)["offer"]
    peers = _peer_records(registry)
    reports: List[Dict[str, Any]] = []
    for peer in peers:
        report: Dict[str, Any] = {
            "id": peer["id"],
            "name": peer["name"],
            "url": peer["url"],
            "ok": False,
            "error": "",
        }
        try:
            result = _request(
                peer["url"] + "/relay/v1/update",
                peer["token_file"],
                offer,
                timeout,
            )
            report.update({"ok": result.get("accepted") is True, "result": result})
            if result.get("accepted") is True:
                _mark_release_delivered(peer["id"], offer, result)
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        reports.append(report)
    return {
        "protocol": SYNC_PROTOCOL,
        "version": offer["version"],
        "release_url": offer["release_url"],
        "peers": reports,
        "ok_count": sum(1 for item in reports if item["ok"]),
        "count": len(reports),
        "artifact_transfer": "github-first-then-tailnet-peer-cache",
        "model_data_plane_restarted": False,
    }


class RelaySyncHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        token = getattr(self.server, "relay_token", "")
        if not token:
            return _trusted_sync_source(self.client_address[0])
        expected = f"Bearer {token}"
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

    def _cached_release(self, version: str, asset_name: str) -> None:
        from .release_updater import verified_cached_release

        try:
            cached = verified_cached_release(version, asset_name)
        except FileNotFoundError:
            self._json(404, {"error": "verified_release_not_cached"})
            return
        except Exception as exc:
            self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
            return
        archive = Path(str(cached["path"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(cached["size"]))
        self.send_header("X-PCL-SHA256", str(cached["sha256"]))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with archive.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        release_parts = _release_route_parts(path)
        if path not in {"/relay/v1/heartbeat", "/relay/v1/topology", "/relay/v1/update"} and release_parts is None:
            self._json(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._json(401, {"error": "invalid_relay_sync_token"})
            return
        if release_parts is not None:
            self._cached_release(*release_parts)
            return
        if path == "/relay/v1/update":
            self._json(200, release_campaign_document())
            return
        envelope = topology_envelope()
        if path == "/relay/v1/heartbeat":
            self._json(200, {
                "status": "ok",
                "protocol": SYNC_PROTOCOL,
                "version": __version__,
                "node_id": envelope["node_id"],
                "node_name": envelope["node_name"],
                "revision": envelope["revision"],
                "digest": envelope["digest"],
                "uptime_seconds": max(0, int(time.time() - STARTED_AT)),
                "capabilities": [
                    "heartbeat", "topology-pull", "topology-push",
                    "release-update", "verified-release-cache",
                ],
            })
            return
        self._json(200, envelope)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in {"/relay/v1/topology", "/relay/v1/update"}:
            self._json(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._json(401, {"error": "invalid_relay_sync_token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise RuntimeError("Invalid topology document size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Topology envelope must be a JSON object")
            result = (
                install_release_offer(payload)
                if path == "/relay/v1/update"
                else apply_topology_envelope(payload)
            )
            self._json(200, result)
        except Exception as exc:
            self._json(409, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve_sync(host: str = "0.0.0.0", port: int = DEFAULT_SYNC_PORT, token_file: str = "", interval: int = 15) -> None:
    if not host:
        raise RuntimeError("Relay sync host must be explicit")
    token = _read_token(token_file)
    if not _token_optional_listen(host) and not token:
        raise RuntimeError("A token file is required outside loopback or the Tailscale address space")
    server = ThreadingHTTPServer((host, int(port)), RelaySyncHandler)
    server.relay_token = token  # type: ignore[attr-defined]
    stop = threading.Event()

    def worker() -> None:
        while not stop.wait(max(5, int(interval))):
            sync_once(timeout=min(10, max(2, int(interval) - 1)))

    thread = threading.Thread(target=worker, name="pcl-relay-sync", daemon=True)
    thread.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
