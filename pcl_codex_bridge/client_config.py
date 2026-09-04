from __future__ import annotations

import json
import concurrent.futures
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__
from .models import (
    AGENTS,
    DEFAULT_GATEWAY_URL,
    available_model_records,
    codex_home,
    configured_agents,
    load_registry,
    model_catalog,
    model_details,
    save_registry,
)
from .native_router import DEFAULT_PORT as NATIVE_ROUTER_DEFAULT_PORT
from .native_router import PCL_MODEL_PREFIX, SERVICE_NAME as NATIVE_ROUTER_SERVICE
from .zstd_codec import library_source as zstd_library_source


BEGIN = "# >>> pcl-codex-bridge managed block >>>"
END = "# <<< pcl-codex-bridge managed block <<<"
ROOT_BEGIN = "# >>> pcl-relay native router root >>>"
ROOT_END = "# <<< pcl-relay native router root <<<"
INSTALL_ROOT = Path.home() / ".local" / "share" / "pcl-codex-bridge"
BIN_PATH = Path.home() / ".local" / "bin" / "pcl-codex"
UNSANDBOXED_MARKER = Path.home() / ".config" / "pcl-codex-bridge" / "allow-unsandboxed-fallback"
NATIVE_CATALOG_NAME = "pcl-native-models.json"
NATIVE_BASE_CATALOG_NAME = "pcl-native-base-models.json"
NATIVE_STATE_ROOT = Path.home() / ".local" / "state" / "pcl-codex-bridge"
NATIVE_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "cn.haichen.pcl-relay-router.plist"
NATIVE_SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "pcl-relay-router.service"
AGENT_ROLE_MARKER = "# >>> pcl-relay managed native agent role v2 >>>"
TAILSCALE_CANDIDATES = (
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    Path.home() / "Applications" / "Tailscale.app" / "Contents" / "MacOS" / "Tailscale",
    Path("/opt/homebrew/bin/tailscale"),
    Path("/usr/local/bin/tailscale"),
    Path("/usr/bin/tailscale"),
)


def gateway_root(url: str = DEFAULT_GATEWAY_URL) -> str:
    return url.rstrip("/")


def _make_tree_owner_writable(root: Path) -> None:
    """Normalize files copied from the signed, read-only macOS app bundle."""
    if not root.exists():
        return
    paths = [root, *root.rglob("*")]
    for path in paths:
        if path.is_symlink():
            continue
        try:
            os.chmod(path, path.stat().st_mode | 0o200)
        except FileNotFoundError:
            continue


def install_source_tree(source_root: Optional[Path] = None) -> Path:
    if getattr(sys, "frozen", False):
        BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        executable = Path(sys.executable).resolve()
        if executable != BIN_PATH.resolve():
            shutil.copy2(executable, BIN_PATH)
        os.chmod(BIN_PATH, 0o755)
        return INSTALL_ROOT
    if source_root is None:
        source_root = Path(__file__).resolve().parents[1]
    package_source = source_root / "pcl_codex_bridge"
    if not package_source.is_dir():
        raise RuntimeError(f"Package source not found: {package_source}")
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
    destination = INSTALL_ROOT / "pcl_codex_bridge"
    if package_source.resolve() != destination.resolve():
        _make_tree_owner_writable(destination)
        shutil.copytree(package_source, destination, dirs_exist_ok=True)
        _make_tree_owner_writable(destination)
    license_source = source_root / "LICENSE"
    notice_source = source_root / "NOTICE"
    if license_source.exists():
        shutil.copy2(license_source, INSTALL_ROOT / "LICENSE")
    if notice_source.exists():
        shutil.copy2(notice_source, INSTALL_ROOT / "NOTICE")
    (INSTALL_ROOT / "VERSION").write_text(__version__ + "\n", encoding="utf-8")
    zstd_source = zstd_library_source()
    if zstd_source is not None:
        zstd_target = INSTALL_ROOT / "lib" / ("libzstd.1.dylib" if sys.platform == "darwin" else "libzstd.so.1")
        zstd_target.parent.mkdir(parents=True, exist_ok=True)
        if zstd_source.resolve() != zstd_target.resolve():
            shutil.copy2(zstd_source, zstd_target)
    BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    wrapper = (
        "#!/bin/sh\n"
        f"export PYTHONPATH={shell_quote(str(INSTALL_ROOT))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f"exec {shell_quote(sys.executable)} -m pcl_codex_bridge.cli \"$@\"\n"
    )
    BIN_PATH.write_text(wrapper, encoding="utf-8")
    os.chmod(BIN_PATH, 0o755)
    return INSTALL_ROOT


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def managed_block(
    gateway_url: str,
    executable: str,
    standalone: bool = False,
    include_agents_table: bool = True,
    include_v2_table: bool = True,
    registry: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the non-root TOML owned by PCL Relay.

    Delegation is intentionally absent: MCP exposes only management/status.
    PCL execution uses Codex's native custom-role ``spawn_agent`` surface.
    """
    mcp_module_root = str(INSTALL_ROOT)
    args = '["mcp-server"]' if standalone else '["-m", "pcl_codex_bridge.mcp_server"]'
    lines = [
            BEGIN,
            "[mcp_servers.pcl_relay]",
            f"command = {json.dumps(executable)}",
            f"args = {args}",
            "startup_timeout_sec = 15",
            "tool_timeout_sec = 300",
            "enabled = true",
            'default_tools_approval_mode = "approve"',
            "",
            "[mcp_servers.pcl_relay.env]",
            'PYTHONDONTWRITEBYTECODE = "1"',
            f"PCL_CODEX_GATEWAY_URL = {json.dumps(gateway_url.rstrip('/'))}",
            END,
        ]
    if not standalone:
        lines.insert(lines.index('[mcp_servers.pcl_relay.env]') + 1, f"PYTHONPATH = {json.dumps(mcp_module_root)}")
    if include_agents_table:
        insert_at = lines.index(END)
        default = next(iter(configured_agents(registry).values()), {"model": "DeepSeek-V4-Pro"})["model"]
        lines[insert_at:insert_at] = [
            "",
            "[agents]",
            "enabled = true",
            f"default_subagent_model = {json.dumps(PCL_MODEL_PREFIX + default)}",
            'default_subagent_reasoning_effort = "high"',
        ]
    if include_v2_table:
        insert_at = lines.index(END)
        lines[insert_at:insert_at] = [
            "",
            "[features.multi_agent_v2]",
            "enabled = true",
            "hide_spawn_agent_metadata = true",
            'tool_namespace = "agents"',
        ]
    return "\n".join(lines)


def strip_managed_block(text: str) -> str:
    pattern = re.compile(r"\n?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    return pattern.sub("\n", text).rstrip() + ("\n" if text.strip() else "")


def strip_native_root_block(text: str) -> str:
    pattern = re.compile(r"\n?" + re.escape(ROOT_BEGIN) + r".*?" + re.escape(ROOT_END) + r"\n?", re.S)
    return pattern.sub("\n", text).rstrip() + ("\n" if text.strip() else "")


def native_root_block(port: int, catalog: Path) -> str:
    return "\n".join(
        [
            ROOT_BEGIN,
            f"openai_base_url = {json.dumps(f'http://127.0.0.1:{port}/v1')}",
            f"model_catalog_json = {json.dumps(str(catalog))}",
            ROOT_END,
        ]
    )


def _root_key_conflicts(text: str) -> List[str]:
    root = text.split("\n[", 1)[0]
    conflicts = []
    for key in ("openai_base_url", "model_catalog_json"):
        if re.search(rf"(?m)^\s*{re.escape(key)}\s*=", root):
            conflicts.append(key)
    return conflicts


def _native_base_catalog_path() -> Path:
    return codex_home() / NATIVE_BASE_CATALOG_NAME


def _native_catalog_path() -> Path:
    return codex_home() / NATIVE_CATALOG_NAME


def _native_agents_dir() -> Path:
    return codex_home() / "agents"


def _agent_role_name(alias: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", alias.lower()).strip("-")
    if value.startswith("pcl-"):
        return value
    if value.startswith("pcl_"):
        value = "pcl-" + value[4:]
    return value or "pcl-worker"


def _agent_role_text(role: str, model: str, description: str) -> str:
    instructions = (
        f"You are the PCL Relay native Codex subagent `{role}`, pinned to `{PCL_MODEL_PREFIX + model}`. "
        "Stay within the delegated boundary, use the current parent workspace, and report concrete file paths, commands, and verification results. "
        "Preserve unrelated user changes and return risky or cross-boundary decisions to the parent agent. "
        "Repository reading, local document search, code search, edits, builds, and tests are allowed when the parent task permits them; never fall back to pcl_delegate."
    )
    return "\n".join(
        [
            AGENT_ROLE_MARKER,
            f"name = {json.dumps(role)}",
            f"description = {json.dumps(description)}",
            f"developer_instructions = {json.dumps(instructions)}",
            f"model = {json.dumps(PCL_MODEL_PREFIX + model)}",
            'model_reasoning_effort = "high"',
            'sandbox_mode = "workspace-write"',
            "",
        ]
    )


def write_native_agent_roles(registry: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Project selected PCL models into Codex-native custom agent roles.

    Only files with our first-line marker are updated or removed.  A user-owned
    same-name role is preserved and receives a namespaced PCL Relay sibling.
    """
    directory = _native_agents_dir()
    directory.mkdir(parents=True, exist_ok=True)
    desired_paths = set()
    roles: List[Dict[str, str]] = []
    for alias, info in configured_agents(registry).items():
        model = str(info["model"])
        description = str(info.get("description") or model_details(model)["description"])
        base_role = _agent_role_name(alias)
        path = directory / f"{base_role}.toml"
        if path.exists() and not path.read_text(encoding="utf-8", errors="replace").startswith(AGENT_ROLE_MARKER + "\n"):
            base_role = "pcl-relay-" + base_role.removeprefix("pcl-")
            path = directory / f"{base_role}.toml"
            if path.exists() and not path.read_text(encoding="utf-8", errors="replace").startswith(AGENT_ROLE_MARKER + "\n"):
                continue
        content = _agent_role_text(base_role, model, description)
        if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != content:
            path.write_text(content, encoding="utf-8")
        desired_paths.add(path)
        roles.append({"name": base_role, "model": PCL_MODEL_PREFIX + model, "path": str(path)})

    for path in directory.glob("*.toml"):
        if path in desired_paths:
            continue
        try:
            managed = path.read_text(encoding="utf-8", errors="replace").startswith(AGENT_ROLE_MARKER + "\n")
        except OSError:
            managed = False
        if managed:
            path.unlink()
    return roles


def _catalog_models(path: Path) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("models") if isinstance(payload, dict) else None
        return [dict(item) for item in entries if isinstance(item, dict) and item.get("slug")]
    except (OSError, ValueError, TypeError):
        return []


def _fallback_native_catalog() -> List[Dict[str, Any]]:
    definitions = {
        "gpt_5_6_sol": {"model": "gpt-5.6-sol", "description": "Latest frontier agentic coding model."},
        "gpt_5_6_terra": {"model": "gpt-5.6-terra", "description": "Balanced agentic coding model for everyday work."},
        "gpt_5_6_luna": {"model": "gpt-5.6-luna", "description": "Fast and affordable agentic coding model."},
        "gpt_5_5": {"model": "gpt-5.5", "description": "Frontier model for complex coding and research."},
        "gpt_5_4": {"model": "gpt-5.4", "description": "Strong model for everyday coding."},
        "gpt_5_4_mini": {"model": "gpt-5.4-mini", "description": "Small, fast, and cost-efficient coding model."},
        "gpt_5_3_spark": {"model": "gpt-5.3-codex-spark", "description": "Ultra-fast coding model."},
    }
    entries = model_catalog(definitions)["models"]
    for item in entries:
        item["display_name"] = str(item["slug"]).replace("gpt-", "GPT-").replace("codex-", "Codex ").title()
        if item["slug"] in {"gpt-5.6-sol", "gpt-5.6-terra"}:
            item["multi_agent_version"] = "v2"
        elif item["slug"] == "gpt-5.6-luna":
            item["multi_agent_version"] = "v1"
        item["node_repl_disabled"] = False
        item["include_skills_usage_instructions"] = True
        item["include_plugin_usage_instructions"] = True
        item["include_apps_usage_instructions"] = True
    return entries


def native_base_catalog() -> List[Dict[str, Any]]:
    base_path = _native_base_catalog_path()
    existing = _catalog_models(base_path)
    if existing:
        return existing
    cache = _catalog_models(codex_home() / "models_cache.json")
    native = [item for item in cache if str(item.get("slug", "")).startswith("gpt-")]
    if not native:
        native = _fallback_native_catalog()
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_path.write_text(json.dumps({"models": native}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return native


def combined_catalog(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = registry if isinstance(registry, dict) else load_registry()
    pcl_entries = model_catalog(configured_agents(data))["models"]
    combined: List[Dict[str, Any]] = []
    for priority, item in enumerate(pcl_entries, 1):
        routed = dict(item)
        routed["slug"] = PCL_MODEL_PREFIX + str(item["slug"])
        routed["display_name"] = "PCL · " + str(item.get("display_name") or item["slug"])
        routed["priority"] = priority
        routed["multi_agent_version"] = "v2"
        routed["supports_websockets"] = False
        routed["supports_search_tool"] = True
        routed["tool_mode"] = "code_mode_only"
        combined.append(routed)
    native_priority = len(combined) + 1
    for offset, item in enumerate(native_base_catalog()):
        native = dict(item)
        # The integrated loopback router is HTTP/SSE only.  Disabling the
        # optimistic websocket prewarm prevents five avoidable retries before
        # Codex falls back to the supported transport.
        native["supports_websockets"] = False
        native["priority"] = native_priority + offset
        combined.append(native)
    return {"models": combined}


def write_native_catalog(registry: Optional[Dict[str, Any]] = None) -> Path:
    path = _native_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(combined_catalog(registry), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, target)
    return target


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _router_port_from_config_text(content: str) -> Optional[int]:
    managed = re.search(
        re.escape(ROOT_BEGIN) + r"(?P<body>.*?)" + re.escape(ROOT_END),
        content,
        flags=re.DOTALL,
    )
    if not managed:
        return None
    match = re.search(
        r'(?m)^\s*openai_base_url\s*=\s*"http://127\.0\.0\.1:(\d+)/v1"\s*$',
        managed.group("body"),
    )
    return int(match.group(1)) if match else None


def _managed_router_service_port() -> Optional[int]:
    if sys.platform == "darwin" and NATIVE_LAUNCH_AGENT.exists():
        try:
            with NATIVE_LAUNCH_AGENT.open("rb") as handle:
                payload = plistlib.load(handle)
            environment = payload.get("EnvironmentVariables") or {}
            if environment.get("PCL_RELAY_NATIVE_PORT"):
                return int(environment["PCL_RELAY_NATIVE_PORT"])
            arguments = payload.get("ProgramArguments") or []
            if "--port" in arguments:
                return int(arguments[arguments.index("--port") + 1])
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            return None
    if sys.platform.startswith("linux") and NATIVE_SYSTEMD_UNIT.exists():
        try:
            content = NATIVE_SYSTEMD_UNIT.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"--port\s+(\d+)", content)
            return int(match.group(1)) if match else None
        except (OSError, ValueError):
            return None
    return None


def configured_native_router_port() -> int:
    config = codex_home() / "config.toml"
    try:
        configured = _router_port_from_config_text(config.read_text(encoding="utf-8"))
    except OSError:
        configured = None
    if configured:
        return configured
    managed = _managed_router_service_port()
    if managed:
        return managed
    registry = load_registry()
    return int(registry.get("native_router_port") or NATIVE_ROUTER_DEFAULT_PORT)


def install_client_config(
    gateway_url: str = DEFAULT_GATEWAY_URL,
    router_port: Optional[int] = None,
) -> Dict[str, Any]:
    legacy_isolated_home = INSTALL_ROOT / "agent-codex-home"
    if legacy_isolated_home.exists():
        shutil.rmtree(legacy_isolated_home)
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    previous_port = _router_port_from_config_text(original)
    base = strip_native_root_block(strip_managed_block(original))
    conflicts = _root_key_conflicts(base)
    if conflicts:
        raise RuntimeError(
            "PCL Relay cannot safely own Codex native routing while these user-managed root keys exist: "
            + ", ".join(conflicts)
        )
    registry = load_registry()
    registry["gateway"] = gateway_url.rstrip("/")
    port = int(router_port or registry.get("native_router_port") or NATIVE_ROUTER_DEFAULT_PORT)
    registry["native_router_port"] = port
    save_registry(registry)
    catalog = write_native_catalog(registry)
    roles = write_native_agent_roles(registry)
    standalone = bool(getattr(sys, "frozen", False))
    has_agents_table = bool(re.search(r"(?m)^\s*\[agents\]\s*(?:#.*)?$", base))
    has_v2_table = bool(re.search(r"(?m)^\s*\[features\.multi_agent_v2\]\s*(?:#.*)?$", base))
    block = managed_block(
        gateway_url,
        str(BIN_PATH) if standalone else sys.executable,
        standalone,
        include_agents_table=not has_agents_table,
        include_v2_table=not has_v2_table,
        registry=registry,
    )
    updated = native_root_block(port, catalog) + "\n\n" + base.strip() + "\n\n" + block + "\n"
    backup_path = backup(config) if updated != original else None
    if updated != original:
        _atomic_write_text(config, updated)

    # Remove files from the former out-of-process/MCP delegation design after
    # the native catalog has been written successfully.
    for legacy in (home / "pcl-agent.config.toml", home / "pcl-models.json"):
        if legacy.exists():
            legacy.unlink()

    return {
        "config": str(config),
        "catalog": str(catalog),
        "router": f"http://127.0.0.1:{port}/v1",
        "multi_agent_surface": "v2_custom_roles",
        "delegation": "native_spawn_agent",
        "native_roles": roles,
        "backup": str(backup_path) if backup_path else "",
        "previous_router_port": previous_port,
        "router_port_changed": previous_port is not None and previous_port != port,
        "codex_reload_required": previous_port is None or previous_port != port,
    }


def uninstall_client_config() -> Dict[str, Any]:
    home = codex_home()
    config = home / "config.toml"
    changed = False
    backup_path: Optional[Path] = None
    if config.exists():
        original = config.read_text(encoding="utf-8")
        updated = strip_native_root_block(strip_managed_block(original))
        if updated != original:
            backup_path = backup(config)
            config.write_text(updated, encoding="utf-8")
            changed = True
    removed: List[str] = []
    for path in [
        home / "pcl-agent.config.toml",
        home / "pcl-models.json",
        home / NATIVE_CATALOG_NAME,
        UNSANDBOXED_MARKER,
    ]:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    roles_dir = _native_agents_dir()
    if roles_dir.exists():
        for path in roles_dir.glob("*.toml"):
            try:
                managed = path.read_text(encoding="utf-8", errors="replace").startswith(AGENT_ROLE_MARKER + "\n")
            except OSError:
                managed = False
            if managed:
                path.unlink()
                removed.append(str(path))
    return {"config_changed": changed, "backup": str(backup_path or ""), "removed": removed}


def _port_is_bindable(port: int) -> bool:
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        candidate.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        candidate.close()


def native_router_health(port: Optional[int] = None, timeout: float = 3.0) -> Dict[str, Any]:
    registry = load_registry()
    selected_port = int(port or registry.get("native_router_port") or NATIVE_ROUTER_DEFAULT_PORT)
    try:
        payload = request_json(f"http://127.0.0.1:{selected_port}/healthz", timeout=max(1, int(timeout)))
        if isinstance(payload, dict) and payload.get("service") == NATIVE_ROUTER_SERVICE:
            return {"reachable": True, "port": selected_port, **payload}
        return {"reachable": False, "port": selected_port, "error": "unexpected service identity"}
    except Exception as exc:
        return {"reachable": False, "port": selected_port, "error": f"{type(exc).__name__}: {exc}"}


def choose_native_router_port() -> int:
    saved = configured_native_router_port()
    managed = _managed_router_service_port()
    if (
        native_router_health(saved, timeout=1).get("reachable")
        or _port_is_bindable(saved)
        or managed == saved
    ):
        return saved
    for port in range(NATIVE_ROUTER_DEFAULT_PORT, NATIVE_ROUTER_DEFAULT_PORT + 100):
        if _port_is_bindable(port):
            return port
    raise RuntimeError("No free loopback port is available for the PCL Relay native router")


def detect_official_proxy() -> str:
    registry = load_registry()
    candidates = [
        os.environ.get("PCL_RELAY_OFFICIAL_PROXY", ""),
        os.environ.get("HTTPS_PROXY", ""),
        os.environ.get("https_proxy", ""),
        str(registry.get("official_proxy") or ""),
    ]
    for port in (17731, 17890, 7890):
        candidates.append(f"http://127.0.0.1:{port}")
    for value in candidates:
        value = value.strip()
        if not value:
            continue
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            continue
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=0.4):
                return value
        except OSError:
            continue
    return ""


def install_native_router_service(port: Optional[int] = None) -> Dict[str, Any]:
    selected_port = int(port or choose_native_router_port())
    proxy = detect_official_proxy()
    registry = load_registry()
    registry["official_proxy"] = proxy
    save_registry(registry)
    NATIVE_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = NATIVE_STATE_ROOT / "native-router.log"
    command = [str(BIN_PATH), "native-router", "--port", str(selected_port)]
    manager = ""
    warning = ""

    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/cn.haichen.pcl-relay-router"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        # A managed router can take a moment to release its listener. Reuse the
        # configured port instead of treating that normal shutdown window as a
        # reason to migrate Codex to another port.
        for _ in range(20):
            if _port_is_bindable(selected_port):
                break
            time.sleep(0.1)
        if not _port_is_bindable(selected_port):
            for candidate in range(NATIVE_ROUTER_DEFAULT_PORT, NATIVE_ROUTER_DEFAULT_PORT + 100):
                if _port_is_bindable(candidate):
                    warning = f"Preferred port {selected_port} is owned by another process; using {candidate}"
                    selected_port = candidate
                    break
            else:
                raise RuntimeError("No free loopback port is available for the PCL Relay native router")
        NATIVE_LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "Label": "cn.haichen.pcl-relay-router",
            "ProgramArguments": command,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
            "EnvironmentVariables": {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PCL_RELAY_NATIVE_PORT": str(selected_port),
                "PCL_RELAY_OFFICIAL_PROXY": proxy,
            },
        }
        with NATIVE_LAUNCH_AGENT.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)
        started = subprocess.run(
            ["launchctl", "bootstrap", domain, str(NATIVE_LAUNCH_AGENT)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if started.returncode != 0:
            raise RuntimeError(started.stderr.strip() or "Could not start the PCL Relay LaunchAgent")
        manager = "launchd"
    elif sys.platform.startswith("linux"):
        NATIVE_SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
        systemd_bin = str(BIN_PATH).replace(" ", chr(92) + "x20")
        unit = "\n".join(
            [
                "[Unit]",
                "Description=PCL Relay native Codex router",
                "After=network-online.target tailscaled.service",
                "Wants=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                f"ExecStart={systemd_bin} native-router --port {selected_port}",
                'Environment="PYTHONDONTWRITEBYTECODE=1"',
                f'Environment="PCL_RELAY_NATIVE_PORT={selected_port}"',
                f'Environment="PCL_RELAY_OFFICIAL_PROXY={proxy}"',
                "Restart=on-failure",
                "RestartSec=3",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
        NATIVE_SYSTEMD_UNIT.write_text(unit, encoding="utf-8")
        daemon = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        enabled = subprocess.run(
            ["systemctl", "--user", "enable", "--now", NATIVE_SYSTEMD_UNIT.name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ) if daemon.returncode == 0 else daemon
        if enabled.returncode == 0:
            subprocess.run(
                ["systemctl", "--user", "restart", NATIVE_SYSTEMD_UNIT.name],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            manager = "systemd-user"
        else:
            log_handle = log_path.open("ab")
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
                close_fds=True,
            )
            log_handle.close()
            manager = "detached-user-process"
            warning = enabled.stderr.strip() or "systemd user service unavailable; using a detached user process"
    else:
        raise RuntimeError("The native Codex router supports macOS and Linux only")

    health: Dict[str, Any] = {}
    for _ in range(30):
        health = native_router_health(selected_port, timeout=1)
        if health.get("reachable"):
            break
        time.sleep(0.2)
    if not health.get("reachable"):
        raise RuntimeError("Native router service did not become healthy: " + str(health.get("error") or "unknown"))
    registry = load_registry()
    registry["native_router_port"] = selected_port
    registry["official_proxy"] = proxy
    save_registry(registry)
    return {
        "installed": True,
        "manager": manager,
        "port": selected_port,
        "url": f"http://127.0.0.1:{selected_port}/v1",
        "official_proxy": proxy,
        "gateway": selected_gateway_from_registry(),
        "health": health,
        "warning": warning,
        "log": str(log_path),
    }


def selected_gateway_from_registry() -> str:
    registry = load_registry()
    return str(registry.get("gateway") or DEFAULT_GATEWAY_URL)


def uninstall_native_router_service() -> Dict[str, Any]:
    stopped: List[str] = []
    if sys.platform == "darwin" and NATIVE_LAUNCH_AGENT.exists():
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(NATIVE_LAUNCH_AGENT)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        NATIVE_LAUNCH_AGENT.unlink()
        stopped.append(str(NATIVE_LAUNCH_AGENT))
    if sys.platform.startswith("linux") and NATIVE_SYSTEMD_UNIT.exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", NATIVE_SYSTEMD_UNIT.name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        NATIVE_SYSTEMD_UNIT.unlink()
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        stopped.append(str(NATIVE_SYSTEMD_UNIT))
    return {"stopped": stopped, "health": native_router_health(timeout=1)}


def request_json(url: str, body: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Any:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_tailscale() -> Optional[str]:
    """Locate the Tailscale CLI even when a macOS app has a minimal PATH."""
    candidates: List[Path] = []
    override = os.environ.get("PCL_TAILSCALE_BIN", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    found = shutil.which("tailscale")
    if found:
        candidates.append(Path(found))
    candidates.extend(TAILSCALE_CANDIDATES)

    seen = set()
    for candidate in candidates:
        path = str(candidate)
        if path in seen:
            continue
        seen.add(path)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return path
    return None


def _tailscale_status() -> Dict[str, Any]:
    executable = find_tailscale()
    if not executable:
        raise RuntimeError(
            "Tailscale CLI is not installed or could not be found; "
            "install Tailscale.app or set PCL_TAILSCALE_BIN"
        )
    result = subprocess.run(
        [executable, "status", "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Tailscale is not connected")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("Tailscale returned an invalid status document")
    return payload


def _tailnet_nodes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    raw_nodes: List[tuple[Dict[str, Any], bool]] = []
    self_node = payload.get("Self")
    if isinstance(self_node, dict):
        raw_nodes.append((self_node, True))
    peers = payload.get("Peer")
    if isinstance(peers, dict):
        raw_nodes.extend((value, False) for value in peers.values() if isinstance(value, dict))
    elif isinstance(peers, list):
        raw_nodes.extend((value, False) for value in peers if isinstance(value, dict))

    seen = set()
    for node, is_self in raw_nodes:
        addresses = node.get("TailscaleIPs") if isinstance(node.get("TailscaleIPs"), list) else []
        ipv4 = next((str(value) for value in addresses if ":" not in str(value)), "")
        if not ipv4 or ipv4 in seen:
            continue
        seen.add(ipv4)
        dns_name = str(node.get("DNSName") or "").rstrip(".")
        nodes.append(
            {
                "node_name": str(node.get("HostName") or dns_name or ipv4),
                "magic_dns": dns_name,
                "tailscale_ip": ipv4,
                "online": True if is_self else bool(node.get("Online")),
                "self": is_self,
            }
        )
    return nodes


def _probe_relay(node: Dict[str, Any], port: int, timeout: float, selected_url: str) -> Dict[str, Any]:
    record = dict(node)
    host = record.get("magic_dns") or record["tailscale_ip"]
    gateway_url = f"http://{host}:{port}/v1"
    record.update(
        {
            "gateway_url": gateway_url,
            "gateway": False,
            "pcl_auth": "not_checked",
            "model_count": 0,
            "latency_ms": None,
            "selected": urllib.parse.urlparse(selected_url).hostname in {
                record.get("magic_dns"),
                record.get("tailscale_ip"),
            },
            "error": "",
        }
    )
    if not record["online"]:
        record["error"] = "tailnet_offline"
        return record
    started = time.monotonic()
    try:
        health = request_json(gateway_url.rsplit("/v1", 1)[0] + "/healthz", timeout=timeout)
        if not isinstance(health, dict) or health.get("status") != "ok":
            raise RuntimeError("not a PCL relay")
        upstream = str(health.get("upstream") or "")
        service = str(health.get("service") or "")
        if service and service != "pcl-codex-gateway":
            raise RuntimeError("unexpected service identity")
        if upstream and "llmapi.pcl.ac.cn" not in upstream:
            raise RuntimeError("unexpected upstream")
        record["gateway"] = True
        record["service"] = service or "pcl-codex-gateway"
        record["version"] = str(health.get("version") or "legacy")
        models = request_json(gateway_url + "/models", timeout=max(timeout, 8))
        entries = models.get("data") if isinstance(models, dict) else None
        record["model_count"] = len(entries) if isinstance(entries, list) else 0
        record["pcl_auth"] = "valid"
    except urllib.error.HTTPError as exc:
        record["pcl_auth"] = "invalid" if exc.code in {401, 403} else "upstream_error"
        record["error"] = f"http_{exc.code}"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["latency_ms"] = int((time.monotonic() - started) * 1000)
    return record


def discover_relays(port: int = 15722, timeout: float = 2.0) -> Dict[str, Any]:
    payload = _tailscale_status()
    nodes = _tailnet_nodes(payload)
    registry = load_registry()
    selected_url = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    online = [node for node in nodes if node["online"]]
    results: Dict[str, Dict[str, Any]] = {}
    if online:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(online))) as pool:
            futures = {
                pool.submit(_probe_relay, node, port, timeout, selected_url): node["tailscale_ip"]
                for node in online
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()
    for node in nodes:
        if node["tailscale_ip"] not in results:
            results[node["tailscale_ip"]] = _probe_relay(node, port, timeout, selected_url)
    ordered = sorted(
        results.values(),
        key=lambda item: (
            not bool(item.get("selected")),
            not bool(item.get("gateway")),
            not bool(item.get("online")),
            str(item.get("node_name", "")).lower(),
        ),
    )
    report = {
        "tailnet_connected": True,
        "selected_gateway": selected_url,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ready_count": sum(
            1 for item in ordered if item.get("gateway") and item.get("pcl_auth") == "valid"
        ),
        "nodes": ordered,
    }
    registry["relay_discovery"] = report
    save_registry(registry)
    return report


def select_relay(gateway_url: str) -> Dict[str, Any]:
    normalized = gateway_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized += "/v1"
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise RuntimeError("Relay URL must look like http://<tailscale-node>:15722/v1")
    health = request_json(normalized.rsplit("/v1", 1)[0] + "/healthz", timeout=10)
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError("Selected node is not a healthy PCL relay")
    models = request_json(normalized + "/models", timeout=30)
    entries = models.get("data") if isinstance(models, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("Selected relay could not authenticate to the PCL model API")

    registry = load_registry()
    previous = str(registry.get("gateway") or DEFAULT_GATEWAY_URL)
    registry["gateway"] = normalized
    registry["relay_selected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    registry["available_models"] = available_model_records(entries)
    registry.setdefault("models", {})
    save_registry(registry)
    config = install_client_config(normalized)
    return {
        "selected_gateway": normalized,
        "previous_gateway": previous,
        "model_count": len(entries),
        "pcl_auth": "valid",
        "codex_reload_required": True,
        "config": config,
        "main_provider_preserved": True,
    }


def request_stream_probe(url: str, body: Dict[str, Any], timeout: int = 120) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    saw_chunk = False
    saw_done = False
    with opener.open(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                continue
            if not data:
                continue
            payload = json.loads(data)
            if isinstance(payload, dict) and payload.get("choices"):
                saw_chunk = True
    return saw_chunk and saw_done


def request_responses_tool_probe(url: str, body: Dict[str, Any], timeout: int = 120) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            payload = json.loads(data)
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}:
                return item.get("name") == "pcl_probe"
    return False


def model_ids(payload: Any) -> List[str]:
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    result = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            result.append(str(entry["id"]))
    return result


def discover_models(gateway_url: str = DEFAULT_GATEWAY_URL) -> Dict[str, Any]:
    payload = request_json(gateway_root(gateway_url) + "/models", timeout=30)
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    records = available_model_records(entries)
    registry = load_registry()
    registry["gateway"] = gateway_url
    registry["catalog_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    registry["available_models"] = records
    if not isinstance(registry.get("selected_agents"), list):
        registry["selected_agents"] = list(AGENTS)
    if not isinstance(registry.get("agent_definitions"), dict):
        registry["agent_definitions"] = dict(AGENTS)
    registry.setdefault("models", {})
    save_registry(registry)
    return registry


def detect_models(gateway_url: str = DEFAULT_GATEWAY_URL, timeout: int = 120) -> Dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    previous = load_registry()
    definitions = configured_agents(previous)
    selected = previous.get("selected_agents") if isinstance(previous, dict) else None
    if not isinstance(selected, list):
        selected = list(definitions)
    report: Dict[str, Any] = {
        "gateway": gateway_url,
        "checked_at": now,
        "catalog_checked_at": previous.get("catalog_checked_at"),
        "selected_agents": [name for name in selected if name in definitions],
        "agent_definitions": definitions,
        "available_models": previous.get("available_models") or {},
        "models": dict(previous.get("models") or {}),
    }
    try:
        payload = request_json(gateway_root(gateway_url) + "/models", timeout=30)
        advertised = model_ids(payload)
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        report["available_models"] = available_model_records(entries)
        report["catalog_checked_at"] = now
    except Exception as exc:
        report["error"] = f"model discovery failed: {exc}"
        save_registry(report)
        return report

    for agent, info in definitions.items():
        model = info["model"]
        status: Dict[str, Any] = {
            "agent": agent,
            "model": model,
            "advertised": model in advertised,
            "chat": False,
            "stream": False,
            "tool_call": False,
            "tool_compatible": False,
            "tool_call_mode": "unavailable",
            "execution_ready": False,
            "error": "",
        }
        chat_body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply briefly with PCL_OK"}],
            "stream": False,
            "max_tokens": 64,
        }
        try:
            chat = request_json(
                gateway_root(gateway_url) + "/chat/completions",
                chat_body,
                timeout=min(timeout, 90),
            )
            status["chat"] = bool(isinstance(chat, dict) and chat.get("choices"))
        except Exception as exc:
            # Availability probes are read-only, so one bounded retry is safe.
            try:
                chat = request_json(
                    gateway_root(gateway_url) + "/chat/completions",
                    chat_body,
                    timeout=min(timeout, 90),
                )
                status["chat"] = bool(isinstance(chat, dict) and chat.get("choices"))
            except Exception as retry_exc:
                status["error"] = f"chat: {retry_exc}"
        if status["chat"]:
            try:
                stream_body = dict(chat_body)
                stream_body["stream"] = True
                status["stream"] = request_stream_probe(
                    gateway_root(gateway_url) + "/chat/completions",
                    stream_body,
                    timeout=min(timeout, 120),
                )
                if not status["stream"]:
                    status["error"] = (status["error"] + "; " if status["error"] else "") + "stream: incomplete SSE"
            except Exception as exc:
                status["error"] = (status["error"] + "; " if status["error"] else "") + f"stream: {exc}"
            native_tool_error = ""
            try:
                tool = request_json(
                    gateway_root(gateway_url) + "/chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": "Call the probe tool with value PCL_TOOL_OK. Do not answer normally.",
                            }
                        ],
                        "tools": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "pcl_probe",
                                    "description": "Capability probe",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"value": {"type": "string"}},
                                        "required": ["value"],
                                    },
                                },
                            }
                        ],
                        "tool_choice": "auto",
                        "stream": False,
                        "max_tokens": 512,
                    },
                    timeout=min(timeout, 120),
                )
                message = (((tool.get("choices") or [{}])[0]).get("message") or {}) if isinstance(tool, dict) else {}
                status["tool_call"] = bool(message.get("tool_calls"))
            except Exception as exc:
                native_tool_error = str(exc)

            if status["tool_call"]:
                status["tool_compatible"] = True
                status["tool_call_mode"] = "native"
            else:
                responses_probe = {
                    "model": model,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Call pcl_probe with value PCL_TOOL_OK. Do not answer normally.",
                                }
                            ],
                        }
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "name": "pcl_probe",
                            "description": "Capability probe",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        }
                    ],
                    "tool_choice": "auto",
                    "max_output_tokens": 512,
                }
                fallback_error = ""
                for _ in range(2):
                    try:
                        status["tool_compatible"] = request_responses_tool_probe(
                            gateway_root(gateway_url) + "/responses",
                            responses_probe,
                            timeout=min(timeout, 120),
                        )
                        if status["tool_compatible"]:
                            break
                    except Exception as exc:
                        fallback_error = str(exc)
                if status["tool_compatible"]:
                    status["tool_call_mode"] = "json_fallback"
                else:
                    details = fallback_error or native_tool_error or "no tool call returned"
                    status["error"] = (status["error"] + "; " if status["error"] else "") + f"tool: {details}"
            status["execution_ready"] = bool(
                status["chat"] and status["stream"] and status["tool_compatible"]
            )
        report["models"][agent] = status
    selected_statuses = [report["models"][name] for name in report["selected_agents"] if name in report["models"]]
    report["all_chat_ready"] = bool(selected_statuses) and all(item["chat"] for item in selected_statuses)
    report["all_stream_ready"] = bool(selected_statuses) and all(item["stream"] for item in selected_statuses)
    report["all_tool_compatible"] = bool(selected_statuses) and all(item["tool_compatible"] for item in selected_statuses)
    report["all_native_tools"] = bool(selected_statuses) and all(item["tool_call"] for item in selected_statuses)
    save_registry(report)
    return report


def doctor(gateway_url: str = DEFAULT_GATEWAY_URL) -> Dict[str, Any]:
    home = codex_home()
    config = home / "config.toml"
    result: Dict[str, Any] = {
        "gateway": False,
        "tailscale": bool(find_tailscale()),
        "codex": bool(find_codex()),
        "config_managed": False,
        # Keep the two legacy keys during the GUI migration.  They now describe
        # the native route/catalog, not the removed codex-exec profile.
        "profile": False,
        "catalog": _native_catalog_path().exists(),
        "native_router": False,
        "native_catalog": _native_catalog_path().exists(),
        "native_v1": False,
        "native_v2": False,
        "native_roles": False,
        "management_mcp": False,
        "legacy_delegate_mcp": False,
        "delegation": "native_spawn_agent",
        "multi_agent_surface": "v2_custom_roles",
        "native_router_port": int(load_registry().get("native_router_port") or NATIVE_ROUTER_DEFAULT_PORT),
        "registry": (Path.home() / ".config" / "pcl-codex-bridge" / "models.json").exists(),
        "unsandboxed_fallback": UNSANDBOXED_MARKER.exists(),
    }
    if config.exists():
        text = config.read_text(encoding="utf-8", errors="replace")
        root_managed = ROOT_BEGIN in text and ROOT_END in text
        management_mcp = BEGIN in text and END in text and "[mcp_servers.pcl_relay]" in text
        catalog_entries = _catalog_models(_native_catalog_path())
        pcl_entries = [item for item in catalog_entries if str(item.get("slug", "")).startswith(PCL_MODEL_PREFIX)]
        v2_match = re.search(
            r"(?ms)^\s*\[features\.multi_agent_v2\]\s*(?:#.*)?$\n(.*?)(?=^\s*\[|\Z)",
            text,
        )
        v2_body = v2_match.group(1) if v2_match else ""
        v2_transport = all(
            re.search(pattern, v2_body)
            for pattern in (
                r"(?m)^\s*enabled\s*=\s*true\s*(?:#.*)?$",
                r"(?m)^\s*hide_spawn_agent_metadata\s*=\s*true\s*(?:#.*)?$",
                r'(?m)^\s*tool_namespace\s*=\s*"agents"\s*(?:#.*)?$',
            )
        )
        native_v2 = bool(pcl_entries) and all(item.get("multi_agent_version") == "v2" for item in pcl_entries) and v2_transport
        selected = configured_agents()
        managed_roles = []
        if _native_agents_dir().exists():
            managed_roles = [
                path
                for path in _native_agents_dir().glob("*.toml")
                if path.read_text(encoding="utf-8", errors="replace").startswith(AGENT_ROLE_MARKER + "\n")
            ]
        result["config_managed"] = root_managed and management_mcp
        result["management_mcp"] = management_mcp
        result["native_v1"] = False
        result["native_v2"] = native_v2
        result["native_roles"] = len(managed_roles) == len(selected) and bool(selected)
        result["legacy_delegate_mcp"] = "[mcp_servers.pcl_agents]" in text or "[model_providers.pcl_internal]" in text
    router = native_router_health(result["native_router_port"])
    result["native_router"] = bool(router.get("reachable"))
    result["native_router_health"] = router
    result["profile"] = result["native_router"]
    try:
        health = request_json(gateway_root(gateway_url).rsplit("/v1", 1)[0] + "/healthz", timeout=10)
        result["gateway"] = isinstance(health, dict) and health.get("status") == "ok"
        if result["gateway"]:
            # The relay only listens on its Tailscale address, so a successful
            # health check is stronger evidence than a CLI-path check on macOS.
            result["tailscale"] = True
    except Exception as exc:
        result["gateway_error"] = str(exc)
    return result


def find_codex() -> Optional[str]:
    def usable(path: Path) -> bool:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        try:
            result = subprocess.run(
                [str(path), "--version"], capture_output=True, text=True, timeout=8, check=False
            )
            return result.returncode == 0 and "codex" in result.stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
            return False

    override = os.environ.get("PCL_CODEX_BIN")
    if override and usable(Path(override)):
        return override
    candidates = [Path("/Applications/ChatGPT.app/Contents/Resources/codex")]
    candidates.extend(Path.home().glob(".vscode-server/extensions/openai.chatgpt-*/bin/*/codex"))
    candidates.extend(Path.home().glob(".vscode/extensions/openai.chatgpt-*/bin/*/codex"))
    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if usable(candidate):
            return str(candidate)
    return None
