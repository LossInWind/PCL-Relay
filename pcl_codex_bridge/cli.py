#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from .client_config import (
    BIN_PATH,
    INTEGRATION_DISABLED_MARKER,
    INSTALL_ROOT,
    UNSANDBOXED_MARKER,
    doctor,
    install_source_tree,
    migrate_thread_provider_index,
    prepare_legacy_opencodex_handoff,
    restore_legacy_opencodex_handoff,
    uninstall_client_config,
    uninstall_native_router_service,
    write_native_catalog,
)
from .http_client import request_json
from .model_detection import detect_models, discover_models
from .models import (
    AGENTS,
    DEFAULT_GATEWAY_URL,
    configured_agents,
    load_registry,
    model_alias,
    model_details,
    save_registry,
    codex_home,
)
from .routes import add_gateway, list_gateways, remove_gateway, select_gateway
from .topology_sync import (
    add_peer,
    list_peers,
    push_latest_release,
    remove_peer,
    serve_sync,
    sync_once,
)
from .sync_service import install_sync_service, sync_service_status, uninstall_sync_service
from .deployment import add_target, deploy_all, deploy_target, import_ssh_targets, list_targets, remove_target
from .opencodex_sidecar import (
    OPENCODEX_DEFAULT_PORT,
    activate_sidecar,
    apply_pending_opencodex_proxy_policy,
    configure_opencodex_proxy_policy,
    deactivate_sidecar,
    installed_runtime,
    opencodex_proxy_policy,
    prepare_sidecar,
    sidecar_health,
    sidecar_integration_status,
)
from .release_updater import install_latest_release, latest_release_status


SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "pcl-codex-gateway.service"
GATEWAY_KEY = Path.home() / ".config" / "pcl-codex-bridge" / "api-key"
PORTAL_URL = "https://llmapi.pcl.ac.cn"


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run(command: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def selected_sidecar_models() -> List[str]:
    return [info["model"] for info in configured_agents(load_registry()).values()]


def stage_opencodex_sidecar(_args: argparse.Namespace) -> Dict[str, Any]:
    """Stage the signed App runtime without starting or reconfiguring a service."""
    install_source_tree()
    runtime = installed_runtime()
    return {
        "staged": True,
        "runtime": str(runtime.root),
        "version": runtime.version,
        "commit": runtime.commit,
        "service_restarted": False,
        "codex_config_changed": False,
        "transport_implementation": "upstream-opencodex",
    }


def opencodex_sidecar_status(_args: argparse.Namespace) -> Dict[str, Any]:
    runtime = installed_runtime()
    health = sidecar_health(runtime)
    integration = (
        sidecar_integration_status(runtime)
        if health.get("ok") is True
        else {"ok": False, "codex": {}}
    )
    codex = integration.get("codex")
    codex = codex if isinstance(codex, dict) else {}
    enabled = codex.get("desiredEnabled") is True
    active = enabled and codex.get("state") == "current" and health.get("ok") is True
    return {
        "enabled": enabled,
        "active": active,
        "config_managed": codex.get("state") == "current",
        "official_codex_restored": codex.get("state") == "absent",
        "restart_codex_required": enabled != active,
        "runtime": str(runtime.root),
        "version": runtime.version,
        "commit": runtime.commit,
        "health": health,
        "integration": integration,
        "transport_implementation": "upstream-opencodex",
    }


def prepare_opencodex_sidecar(args: argparse.Namespace) -> Dict[str, Any]:
    install_source_tree()
    runtime = installed_runtime()
    registry = load_registry()
    prepared = prepare_sidecar(
        runtime,
        args.gateway_url,
        selected_sidecar_models(),
        port=int(args.port),
    )
    registry.pop("official_proxy", None)
    registry["opencodex_port"] = int(args.port)
    save_registry(registry)
    return {**prepared, "official_network_owner": "upstream-codex-environment"}


def activate_opencodex_sidecar(args: argparse.Namespace) -> Dict[str, Any]:
    runtime = installed_runtime()
    handoff = prepare_legacy_opencodex_handoff()
    try:
        activated = activate_sidecar(runtime, port=int(args.port))
    except Exception as exc:
        upstream_restore: Dict[str, Any] = {"restored": False}
        try:
            upstream_restore = deactivate_sidecar(runtime)
        except Exception as restore_exc:
            upstream_restore["error"] = f"{type(restore_exc).__name__}: {restore_exc}"
        legacy_restore = restore_legacy_opencodex_handoff(handoff)
        raise RuntimeError(
            "OpenCodex activation failed; pre-handoff Codex config was restored "
            f"(OpenCodex restore: {upstream_restore}, legacy restore: {legacy_restore}): {exc}"
        ) from exc
    INTEGRATION_DISABLED_MARKER.unlink(missing_ok=True)
    # OpenCodex intentionally leaves foreign provider histories untouched.
    # These records belong to our retired legacy provider, so the handoff owns
    # their one-time migration after (never before) successful activation.
    history = migrate_thread_provider_index(codex_home(), "pcl_relay_official", "openai")
    return {
        **activated,
        "legacy_handoff": handoff,
        "history_migration": history,
        "legacy_router_stopped": False,
    }


def deactivate_opencodex_sidecar(_args: argparse.Namespace) -> Dict[str, Any]:
    result = deactivate_sidecar(installed_runtime())
    return {**result, "legacy_router_stopped": False}


def enable_opencodex_integration(args: argparse.Namespace) -> Dict[str, Any]:
    prepared = prepare_opencodex_sidecar(args)
    activated = activate_opencodex_sidecar(args)
    return {**activated, "prepared": prepared}


def install_gateway(args: argparse.Namespace) -> Dict[str, Any]:
    if sys.platform == "darwin":
        raise RuntimeError("Gateway installation is supported on Ubuntu/Linux, not macOS")
    if not re.fullmatch(r"[A-Za-z0-9.:-]+", args.host):
        raise RuntimeError("Gateway listen host must be an explicit IP address or hostname")
    try:
        admin_networks = [
            str(ipaddress.ip_network(value.strip(), strict=False))
            for value in args.admin_cidrs.split(",")
            if value.strip()
        ]
    except ValueError as exc:
        raise RuntimeError(f"Invalid gateway admin CIDR: {exc}") from exc
    if not admin_networks:
        raise RuntimeError("At least one gateway admin CIDR is required")
    args.admin_cidrs = ",".join(admin_networks)
    install_source_tree()
    key_source = Path(args.key_file).expanduser() if args.key_file else None
    if key_source:
        if not key_source.is_file() or not key_source.stat().st_size:
            raise RuntimeError(f"API key file is missing or empty: {key_source}")
        GATEWAY_KEY.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(key_source, GATEWAY_KEY)
    if not GATEWAY_KEY.is_file() or not GATEWAY_KEY.stat().st_size:
        raise RuntimeError(f"Install the PCL API key at {GATEWAY_KEY} or pass --key-file")
    os.chmod(GATEWAY_KEY, 0o600)
    SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
    standalone = bool(getattr(sys, "frozen", False))
    exec_start = f"{BIN_PATH} gateway-server" if standalone else f"{sys.executable} -m pcl_codex_bridge.gateway"
    unit = "\n".join(
        [
            "[Unit]",
            "Description=PCL Relay model protocol gateway",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"Environment=PYTHONPATH={INSTALL_ROOT}",
            f"Environment=PCL_LLM_API_KEY_FILE={GATEWAY_KEY}",
            f"Environment=PCL_CODEX_GATEWAY_HOST={args.host}",
            f"Environment=PCL_CODEX_GATEWAY_PORT={args.port}",
            f"Environment=PCL_RELAY_ADMIN_CIDRS={args.admin_cidrs}",
            f"ExecStart={exec_start}",
            "Restart=on-failure",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            f"ReadOnlyPaths={GATEWAY_KEY}",
            f"ReadWritePaths={Path.home() / '.local' / 'state' / 'pcl-codex-bridge'}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    SYSTEMD_UNIT.write_text(unit, encoding="utf-8")
    state = Path.home() / ".local" / "state" / "pcl-codex-bridge"
    state.mkdir(parents=True, exist_ok=True)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT.name])
    run(["systemctl", "--user", "restart", SYSTEMD_UNIT.name])
    status = run(["systemctl", "--user", "is-active", SYSTEMD_UNIT.name], check=False)
    return {
        "installed": str(INSTALL_ROOT),
        "unit": str(SYSTEMD_UNIT),
        "key": str(GATEWAY_KEY),
        "key_mode": oct(GATEWAY_KEY.stat().st_mode & 0o777),
        "listen_host": args.host,
        "admin_cidrs": args.admin_cidrs,
        "service": status.stdout.strip(),
    }


def uninstall_gateway() -> Dict[str, Any]:
    run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT.name], check=False)
    removed = []
    if SYSTEMD_UNIT.exists():
        SYSTEMD_UNIT.unlink()
        removed.append(str(SYSTEMD_UNIT))
    run(["systemctl", "--user", "daemon-reload"], check=False)
    return {"removed": removed, "key_preserved": str(GATEWAY_KEY)}


def install_client(args: argparse.Namespace) -> Dict[str, Any]:
    """Stage the cross-platform control plane without starting a router.

    Explicit ``integration enable`` owns sidecar preparation and Codex
    activation. Installation itself must not touch an active data plane.
    """
    install_source_tree()
    INTEGRATION_DISABLED_MARKER.unlink(missing_ok=True)
    if getattr(args, "allow_unsandboxed_fallback", False):
        UNSANDBOXED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        UNSANDBOXED_MARKER.write_text(
            "Explicit opt-in for Linux hosts where the Codex bwrap sandbox is unavailable.\n",
            encoding="utf-8",
        )
        os.chmod(UNSANDBOXED_MARKER, 0o600)
    registry = load_registry()
    registry["gateway"] = args.gateway_url
    registry.setdefault("models", {})
    from .topology_sync import mark_topology_changed

    mark_topology_changed(registry)
    save_registry(registry)
    runtime = installed_runtime()
    return {
        "installed": True,
        "enabled": False,
        "install_root": str(INSTALL_ROOT),
        "gateway": args.gateway_url,
        "runtime": str(runtime.root),
        "runtime_version": runtime.version,
        "runtime_commit": runtime.commit,
        "main_provider_preserved": True,
        "service_started": False,
        "codex_config_changed": False,
        "unsandboxed_fallback": UNSANDBOXED_MARKER.exists(),
    }


def select_models(values: List[str]) -> Dict[str, Any]:
    registry = load_registry()
    available = registry.get("available_models") if isinstance(registry, dict) else None
    available = available if isinstance(available, dict) else {}
    existing = registry.get("agent_definitions") if isinstance(registry, dict) else None
    existing = existing if isinstance(existing, dict) else dict(AGENTS)
    requested = values or list(AGENTS)
    definitions: Dict[str, Dict[str, str]] = {}
    selected: List[str] = []
    unknown: List[str] = []

    for value in requested:
        alias = value if value in existing or value in AGENTS else ""
        model_id = ""
        record: Dict[str, Any] = {}
        if alias:
            info = existing.get(alias) or AGENTS.get(alias) or {}
            model_id = str(info.get("model") or "")
            raw_record = available.get(model_id)
            record = raw_record if isinstance(raw_record, dict) else model_details(model_id)
        elif value in available and isinstance(available[value], dict):
            record = available[value]
            if not record.get("agent_eligible", False):
                raise RuntimeError(f"Model cannot be used as a Codex text agent: {value}")
            model_id = value
            alias = str(record.get("alias") or model_alias(model_id))
        else:
            unknown.append(value)
            continue
        if not model_id:
            unknown.append(value)
            continue
        if alias not in selected:
            selected.append(alias)
        definitions[alias] = {
            "model": model_id,
            "description": str(
                record.get("description")
                or (existing.get(alias) or AGENTS.get(alias) or {}).get("description")
                or model_details(model_id)["description"]
            ),
        }

    if unknown:
        raise RuntimeError(
            "Unknown agent aliases or model IDs (run `pcl-codex models discover` first): "
            + ", ".join(unknown)
        )
    if not selected:
        raise RuntimeError("Select at least one PCL text model")
    registry["selected_agents"] = selected
    registry["agent_definitions"] = definitions
    from .topology_sync import mark_topology_changed

    mark_topology_changed(registry)
    save_registry(registry)
    catalog = write_native_catalog(registry)
    return {
        "selected_agents": selected,
        "models": {name: definitions[name]["model"] for name in selected},
        "catalog": str(catalog),
        "codex_reload_required": True,
        "delegation": "native_spawn_agent",
    }


def uninstall_client() -> Dict[str, Any]:
    INTEGRATION_DISABLED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    INTEGRATION_DISABLED_MARKER.write_text(
        "PCL Relay Codex integration is disabled by the user.\n",
        encoding="utf-8",
    )
    os.chmod(INTEGRATION_DISABLED_MARKER, 0o600)
    service = uninstall_native_router_service()
    config = uninstall_client_config()
    return {
        "enabled": False,
        "official_codex_restored": True,
        "service": service,
        "config": config,
    }


def integration_status() -> Dict[str, Any]:
    return opencodex_sidecar_status(argparse.Namespace())


def serve_native_router(args: argparse.Namespace) -> Dict[str, Any]:
    from .native_router import serve

    serve("127.0.0.1", int(args.port))
    return {"stopped": True}


def admin_root(gateway_url: str) -> str:
    return gateway_url.rstrip("/").rsplit("/v1", 1)[0]


def server_status(gateway_url: str) -> Dict[str, Any]:
    return request_json(admin_root(gateway_url) + "/admin/status", timeout=15)


def server_logs(gateway_url: str) -> Dict[str, Any]:
    return request_json(admin_root(gateway_url) + "/admin/logs", timeout=15)


def server_restart(gateway_url: str) -> Dict[str, Any]:
    before = server_status(gateway_url)
    old_pid = before.get("pid") if isinstance(before, dict) else None
    accepted = request_json(admin_root(gateway_url) + "/admin/restart", {}, timeout=15)
    deadline = time.monotonic() + 30
    last_error = ""
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            current = server_status(gateway_url)
            if current.get("status") == "active" and current.get("pid") != old_pid:
                return {
                    "accepted": accepted,
                    "before_pid": old_pid,
                    "status": current,
                }
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"Gateway did not return with a new process within 30 seconds: {last_error}")


def portal_status(gateway_url: str) -> Dict[str, Any]:
    proxy_url = admin_root(gateway_url)
    started = time.monotonic()
    try:
        probe = subprocess.run(
            [
                "curl",
                "--noproxy",
                "",
                "--proxy",
                proxy_url,
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                "20",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}\n%{content_type}\n%{time_total}",
                PORTAL_URL + "/",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(probe.stderr.strip() or f"curl exited {probe.returncode}")
        lines = probe.stdout.splitlines()
        status = int(lines[0])
        content_type = lines[1] if len(lines) > 1 else ""
    except Exception as exc:
        return {
            "available": False,
            "portal_url": PORTAL_URL,
            "proxy_url": proxy_url,
            "pac_url": proxy_url + "/admin/portal.pac",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    available = status == 200 and "text/html" in content_type.lower()
    return {
        "available": available,
        "portal_url": PORTAL_URL,
        "proxy_url": proxy_url,
        "pac_url": proxy_url + "/admin/portal.pac",
        "latency_ms": int((time.monotonic() - started) * 1000),
        "http_status": status,
        "content_type": content_type,
        "error": "" if available else "PCL portal did not return an HTML page",
    }


def portal_open(gateway_url: str, path: str = "/") -> Dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("Opening the PCL portal is currently supported on macOS")
    allowed_paths = {"/", "/keys", "/wallet", "/playground", "/models"}
    if path not in allowed_paths:
        raise RuntimeError(f"Unsupported PCL portal path: {path}")
    status = portal_status(gateway_url)
    if not status.get("available"):
        raise RuntimeError("PCL portal forwarding is unavailable: " + str(status.get("error") or "unknown"))
    browser = next(
        (
            name
            for name in ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"]
            if Path(f"/Applications/{name}.app").exists()
        ),
        "",
    )
    if not browser:
        raise RuntimeError("Install Google Chrome, Microsoft Edge, Brave, or Chromium to use the isolated portal browser")
    profile = Path.home() / "Library" / "Application Support" / "PCL Relay" / "Portal Browser"
    profile.mkdir(parents=True, exist_ok=True)
    target = PORTAL_URL + path
    command = [
        "open",
        "-na",
        browser,
        "--args",
        f"--user-data-dir={profile}",
        f"--proxy-pac-url={status['pac_url']}",
        "--no-first-run",
        target,
    ]
    launched = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if launched.returncode != 0:
        raise RuntimeError(launched.stderr.strip() or "Could not launch the portal browser")
    return {
        **status,
        "opened": True,
        "browser": browser,
        "target": target,
        "profile": str(profile),
        "system_proxy_changed": False,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="pcl-codex")
    root.add_argument("--gateway-url", default=None)
    commands = root.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install")
    targets = install.add_subparsers(dest="target", required=True)
    client = targets.add_parser("client")
    client.add_argument(
        "--allow-unsandboxed-fallback",
        action="store_true",
        help="On Linux only, allow danger-full-access when the bwrap workspace sandbox cannot start.",
    )
    client.set_defaults(handler=install_client)
    gateway = targets.add_parser("gateway")
    gateway.add_argument("--key-file")
    gateway.add_argument("--host", default=os.environ.get("PCL_CODEX_GATEWAY_HOST", "127.0.0.1"))
    gateway.add_argument("--port", type=int, default=15722)
    gateway.add_argument(
        "--admin-cidrs",
        default=os.environ.get("PCL_RELAY_ADMIN_CIDRS", "127.0.0.0/8,::1/128"),
        help="Explicit comma-separated management CIDRs supplied by the network owner.",
    )
    gateway.set_defaults(handler=install_gateway)

    models = commands.add_parser("models")
    actions = models.add_subparsers(dest="models_action", required=True)
    detect = actions.add_parser("detect")
    detect.add_argument("--timeout", type=int, default=120)
    detect.set_defaults(handler=lambda a: detect_models(a.gateway_url, a.timeout))
    discover = actions.add_parser("discover")
    discover.set_defaults(handler=lambda a: discover_models(a.gateway_url))
    select = actions.add_parser("select")
    select.add_argument("agents", nargs="*")
    select.set_defaults(handler=lambda a: select_models(a.agents))
    show = actions.add_parser("show")
    show.set_defaults(handler=lambda a: load_registry())

    diagnosis = commands.add_parser("doctor")
    diagnosis.set_defaults(handler=lambda a: doctor(a.gateway_url))

    server = commands.add_parser("server")
    server_actions = server.add_subparsers(dest="server_action", required=True)
    status = server_actions.add_parser("status")
    status.set_defaults(handler=lambda a: server_status(a.gateway_url))
    logs = server_actions.add_parser("logs")
    logs.set_defaults(handler=lambda a: server_logs(a.gateway_url))
    restart = server_actions.add_parser("restart")
    restart.set_defaults(handler=lambda a: server_restart(a.gateway_url))

    portal = commands.add_parser("portal")
    portal_actions = portal.add_subparsers(dest="portal_action", required=True)
    portal_check = portal_actions.add_parser("status")
    portal_check.set_defaults(handler=lambda a: portal_status(a.gateway_url))
    portal_launch = portal_actions.add_parser("open")
    portal_launch.add_argument("--path", default="/")
    portal_launch.set_defaults(handler=lambda a: portal_open(a.gateway_url, a.path))

    routes = commands.add_parser("routes")
    route_actions = routes.add_subparsers(dest="routes_action", required=True)
    from .runtime_snapshot import runtime_snapshot
    route_actions.add_parser("snapshot").set_defaults(handler=lambda a: runtime_snapshot())
    route_list = route_actions.add_parser("list")
    route_list.add_argument("--probe", action="store_true")
    route_list.add_argument("--timeout", type=int, default=15)
    route_list.set_defaults(handler=lambda a: list_gateways(a.probe, a.timeout))
    route_add = route_actions.add_parser("add")
    route_add.add_argument("url")
    route_add.add_argument("--name", default="")
    route_add.set_defaults(handler=lambda a: add_gateway(a.url, a.name))
    route_select = route_actions.add_parser("select")
    route_select.add_argument("gateway")
    route_select.set_defaults(handler=lambda a: select_gateway(a.gateway))
    route_remove = route_actions.add_parser("remove")
    route_remove.add_argument("gateway")
    route_remove.set_defaults(handler=lambda a: remove_gateway(a.gateway))
    route_proxy = route_actions.add_parser("proxy")
    route_proxy_actions = route_proxy.add_subparsers(dest="route_proxy_action", required=True)
    route_proxy_show = route_proxy_actions.add_parser("show")
    route_proxy_show.set_defaults(handler=lambda a: opencodex_proxy_policy(installed_runtime()))
    route_proxy_set = route_proxy_actions.add_parser("set")
    route_proxy_set.add_argument("--proxy", default=None)
    route_proxy_set.add_argument("--no-proxy", nargs="*", default=None)
    route_proxy_set.set_defaults(
        handler=lambda a: configure_opencodex_proxy_policy(
            installed_runtime(), proxy=a.proxy, no_proxy=a.no_proxy
        )
    )
    route_proxy_clear = route_proxy_actions.add_parser("clear")
    route_proxy_clear.set_defaults(
        handler=lambda a: configure_opencodex_proxy_policy(installed_runtime(), clear=True)
    )
    route_proxy_apply = route_proxy_actions.add_parser("apply")
    route_proxy_apply.set_defaults(
        handler=lambda a: {
            **opencodex_proxy_policy(installed_runtime()),
            **apply_pending_opencodex_proxy_policy(installed_runtime()),
        }
    )

    sync = commands.add_parser("sync")
    sync_actions = sync.add_subparsers(dest="sync_action", required=True)
    sync_status = sync_actions.add_parser("status")
    sync_status.add_argument("--probe", action="store_true")
    sync_status.add_argument("--timeout", type=int, default=10)
    sync_status.add_argument("--peer", default="")
    sync_status.set_defaults(handler=lambda a: list_peers(a.probe, a.timeout, a.peer))
    sync_now = sync_actions.add_parser("now")
    sync_now.add_argument("--timeout", type=int, default=10)
    sync_now.set_defaults(handler=lambda a: sync_once(a.timeout))
    sync_peers = sync_actions.add_parser("peers")
    sync_peer_actions = sync_peers.add_subparsers(dest="sync_peer_action", required=True)
    sync_peer_list = sync_peer_actions.add_parser("list")
    sync_peer_list.add_argument("--probe", action="store_true")
    sync_peer_list.add_argument("--timeout", type=int, default=10)
    sync_peer_list.set_defaults(handler=lambda a: list_peers(a.probe, a.timeout))
    sync_peer_add = sync_peer_actions.add_parser("add")
    sync_peer_add.add_argument("url")
    sync_peer_add.add_argument("--name", default="")
    sync_peer_add.add_argument("--token-file", default="")
    sync_peer_add.set_defaults(handler=lambda a: add_peer(a.url, a.name, a.token_file))
    sync_peer_remove = sync_peer_actions.add_parser("remove")
    sync_peer_remove.add_argument("peer")
    sync_peer_remove.set_defaults(handler=lambda a: remove_peer(a.peer))
    sync_serve = sync_actions.add_parser("serve")
    sync_serve.add_argument("--host", default="0.0.0.0")
    sync_serve.add_argument("--port", type=int, default=15726)
    sync_serve.add_argument("--token-file", default="")
    sync_serve.add_argument("--interval", type=int, default=15)
    sync_serve.set_defaults(
        handler=lambda a: serve_sync(a.host, a.port, a.token_file, a.interval)
    )
    sync_service = sync_actions.add_parser("service")
    sync_service_actions = sync_service.add_subparsers(dest="sync_service_action", required=True)
    sync_service_status_parser = sync_service_actions.add_parser("status")
    sync_service_status_parser.set_defaults(handler=lambda a: sync_service_status())
    sync_service_install = sync_service_actions.add_parser("install")
    sync_service_install.add_argument("--host", default="0.0.0.0")
    sync_service_install.add_argument("--port", type=int, default=15726)
    sync_service_install.add_argument("--token-file", default="")
    sync_service_install.add_argument("--interval", type=int, default=15)
    sync_service_install.set_defaults(
        handler=lambda a: install_sync_service(a.host, a.port, a.token_file, a.interval)
    )
    sync_service_uninstall = sync_service_actions.add_parser("uninstall")
    sync_service_uninstall.set_defaults(handler=lambda a: uninstall_sync_service())

    updates = commands.add_parser("updates")
    update_actions = updates.add_subparsers(dest="updates_action", required=True)
    update_status = update_actions.add_parser("status")
    update_status.set_defaults(handler=lambda a: latest_release_status())
    update_install = update_actions.add_parser("install")
    update_install.add_argument("--force", action="store_true")
    update_install.set_defaults(handler=lambda a: install_latest_release(a.force))
    update_push = update_actions.add_parser("push")
    update_push.add_argument("--timeout", type=int, default=600)
    update_push.set_defaults(handler=lambda a: push_latest_release(a.timeout))

    deploy = commands.add_parser("deploy")
    deploy_actions = deploy.add_subparsers(dest="deploy_action", required=True)
    deploy_status = deploy_actions.add_parser("status")
    deploy_status.add_argument("--probe", action="store_true")
    deploy_status.add_argument("--timeout", type=int, default=20)
    deploy_status.add_argument("--target", default="")
    deploy_status.set_defaults(handler=lambda a: list_targets(a.probe, a.timeout, a.target))
    deploy_targets = deploy_actions.add_parser("targets")
    deploy_target_actions = deploy_targets.add_subparsers(dest="deploy_target_action", required=True)
    deploy_target_add = deploy_target_actions.add_parser("add")
    deploy_target_add.add_argument("--ssh-target", required=True)
    deploy_target_add.add_argument("--control-url", default="")
    deploy_target_add.add_argument("--name", default="")
    deploy_target_add.set_defaults(
        handler=lambda a: add_target(a.ssh_target, a.control_url, a.name)
    )
    deploy_target_remove = deploy_target_actions.add_parser("remove")
    deploy_target_remove.add_argument("target_id")
    deploy_target_remove.set_defaults(handler=lambda a: remove_target(a.target_id))
    deploy_target_import = deploy_target_actions.add_parser("import-ssh")
    deploy_target_import.set_defaults(handler=lambda a: import_ssh_targets())
    deploy_one = deploy_actions.add_parser("one")
    deploy_one.add_argument("target_id")
    deploy_one.add_argument("--timeout", type=int, default=900)
    deploy_one.set_defaults(handler=lambda a: deploy_target(a.target_id, timeout=a.timeout))
    deploy_everywhere = deploy_actions.add_parser("all")
    deploy_everywhere.add_argument("--timeout", type=int, default=900)
    deploy_everywhere.set_defaults(handler=lambda a: deploy_all(a.timeout))

    integration = commands.add_parser("integration")
    integration_actions = integration.add_subparsers(dest="integration_action", required=True)
    integration_check = integration_actions.add_parser("status")
    integration_check.set_defaults(handler=lambda a: integration_status())
    integration_enable = integration_actions.add_parser("enable")
    integration_enable.add_argument("--port", type=int, default=OPENCODEX_DEFAULT_PORT)
    integration_enable.set_defaults(handler=enable_opencodex_integration)
    integration_disable = integration_actions.add_parser("disable")
    integration_disable.set_defaults(handler=deactivate_opencodex_sidecar)

    sidecar = commands.add_parser("sidecar")
    sidecar_actions = sidecar.add_subparsers(dest="sidecar_action", required=True)
    sidecar_stage = sidecar_actions.add_parser("stage")
    sidecar_stage.set_defaults(handler=stage_opencodex_sidecar)
    sidecar_status = sidecar_actions.add_parser("status")
    sidecar_status.set_defaults(handler=opencodex_sidecar_status)
    sidecar_prepare = sidecar_actions.add_parser("prepare")
    sidecar_prepare.add_argument("--port", type=int, default=OPENCODEX_DEFAULT_PORT)
    sidecar_prepare.set_defaults(handler=prepare_opencodex_sidecar)
    sidecar_activate = sidecar_actions.add_parser("activate")
    sidecar_activate.add_argument("--port", type=int, default=OPENCODEX_DEFAULT_PORT)
    sidecar_activate.set_defaults(handler=activate_opencodex_sidecar)
    sidecar_deactivate = sidecar_actions.add_parser("deactivate")
    sidecar_deactivate.set_defaults(handler=deactivate_opencodex_sidecar)

    uninstall = commands.add_parser("uninstall")
    uninstall.add_argument("--gateway", action="store_true")
    uninstall.set_defaults(
        handler=lambda a: uninstall_gateway() if a.gateway else uninstall_client()
    )
    return root


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "mcp-server":
        from .mcp_server import main as mcp_main

        mcp_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "gateway-server":
        from .gateway import main as gateway_main

        gateway_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "native-router":
        # Compatibility entrypoint for an already-running pre-OpenCodex
        # launchd service. It stays outside the advertised parser so a staged
        # upgrade cannot break that service before explicit sidecar activation.
        legacy = argparse.ArgumentParser(add_help=False)
        legacy.add_argument("--port", type=int, default=15724)
        serve_native_router(legacy.parse_args(sys.argv[2:]))
        return
    args = parser().parse_args()
    if args.gateway_url is None:
        registry = load_registry()
        args.gateway_url = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    try:
        emit(args.handler(args))
    except Exception as exc:
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
