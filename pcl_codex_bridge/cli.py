#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from .client_config import (
    INSTALL_ROOT,
    UNSANDBOXED_MARKER,
    detect_models,
    discover_relays,
    discover_models,
    doctor,
    install_client_config,
    install_native_router_service,
    install_source_tree,
    choose_native_router_port,
    native_router_health,
    request_json,
    select_relay,
    uninstall_client_config,
    uninstall_native_router_service,
    write_native_catalog,
)
from .models import (
    AGENTS,
    DEFAULT_GATEWAY_URL,
    load_registry,
    model_alias,
    model_details,
    save_registry,
)
from .remote_clients import (
    discover_remote_clients,
    install_remote_client,
    remote_client_status,
    check_client_connectivity,
)
from .bridges import bridge_status, install_bridge, remove_bridge
from .direct_clients import install_local_direct


SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "pcl-codex-gateway.service"
GATEWAY_KEY = Path.home() / ".config" / "pcl-codex-bridge" / "api-key"
PORTAL_URL = "https://llmapi.pcl.ac.cn"


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run(command: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def install_gateway(args: argparse.Namespace) -> Dict[str, Any]:
    if sys.platform == "darwin":
        raise RuntimeError("Gateway installation is supported on Ubuntu/Linux, not macOS")
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
            "Description=PCL Codex Tailnet gateway",
            "After=network-online.target tailscaled.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"Environment=PYTHONPATH={INSTALL_ROOT}",
            f"Environment=PCL_LLM_API_KEY_FILE={GATEWAY_KEY}",
            f"Environment=PCL_CODEX_GATEWAY_PORT={args.port}",
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
    install_source_tree()
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
    save_registry(registry)
    port = choose_native_router_port()
    service = install_native_router_service(port)
    result = install_client_config(args.gateway_url, router_port=port)
    result["install_root"] = str(INSTALL_ROOT)
    result["main_provider_preserved"] = True
    result["official_route"] = "PCL Relay loopback passthrough"
    result["native_router_service"] = service
    result["native_router_health"] = native_router_health(port)
    result["unsandboxed_fallback"] = UNSANDBOXED_MARKER.exists()
    return result


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
    service = uninstall_native_router_service()
    config = uninstall_client_config()
    return {"service": service, "config": config}


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
    gateway.add_argument("--port", type=int, default=15722)
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

    relays = commands.add_parser("relays")
    relay_actions = relays.add_subparsers(dest="relays_action", required=True)
    relay_discover = relay_actions.add_parser("discover")
    relay_discover.add_argument("--port", type=int, default=15722)
    relay_discover.add_argument("--timeout", type=float, default=2.0)
    relay_discover.set_defaults(handler=lambda a: discover_relays(a.port, a.timeout))
    relay_select = relay_actions.add_parser("select")
    relay_select.add_argument("url")
    relay_select.set_defaults(handler=lambda a: select_relay(a.url))

    clients = commands.add_parser("clients")
    client_actions = clients.add_subparsers(dest="clients_action", required=True)
    client_discover = client_actions.add_parser("discover")
    client_discover.add_argument("--timeout", type=float, default=2.0)
    client_discover.set_defaults(handler=lambda a: discover_remote_clients(a.timeout))
    client_status = client_actions.add_parser("status")
    client_status.add_argument("ssh_target")
    client_status.set_defaults(handler=lambda a: remote_client_status(a.ssh_target, a.gateway_url))
    client_test = client_actions.add_parser("test")
    client_test.add_argument("ssh_target")
    client_test.add_argument("--node", default="")
    client_test.add_argument("--quick", action="store_true")
    client_test.set_defaults(
        handler=lambda a: check_client_connectivity(
            a.ssh_target,
            a.gateway_url,
            node_id=a.node,
            deep=not a.quick,
        )
    )
    client_install = client_actions.add_parser("install")
    client_install.add_argument("ssh_target")
    client_install.set_defaults(handler=lambda a: install_remote_client(a.ssh_target, a.gateway_url))
    client_update = client_actions.add_parser("update")
    client_update.add_argument("ssh_target")
    client_update.set_defaults(handler=lambda a: install_remote_client(a.ssh_target, a.gateway_url))

    bridges = commands.add_parser("bridges")
    bridge_actions = bridges.add_subparsers(dest="bridges_action", required=True)
    bridge_show = bridge_actions.add_parser("show")
    bridge_show.set_defaults(handler=lambda a: bridge_status())
    bridge_install = bridge_actions.add_parser("install")
    bridge_install.add_argument("ssh_target")
    bridge_install.add_argument("--remote-port", type=int, default=0)
    bridge_install.set_defaults(handler=lambda a: install_bridge(a.ssh_target, a.remote_port))
    bridge_remove = bridge_actions.add_parser("remove")
    bridge_remove.add_argument("ssh_target")
    bridge_remove.set_defaults(handler=lambda a: remove_bridge(a.ssh_target))

    direct = commands.add_parser("direct")
    direct_actions = direct.add_subparsers(dest="direct_action", required=True)
    direct_install = direct_actions.add_parser("install")
    direct_install.add_argument("ssh_target")
    direct_install.set_defaults(handler=lambda a: install_local_direct(a.ssh_target))

    native_router = commands.add_parser("native-router", help=argparse.SUPPRESS)
    native_router.add_argument("--port", type=int, default=15724)
    native_router.set_defaults(handler=serve_native_router)

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
