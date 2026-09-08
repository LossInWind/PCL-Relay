from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse


OPENCODEX_VERSION = "2.46.0"
OPENCODEX_COMMIT = "bba63222d3eeb5c8e397edae35798225e4fa1a6f"
OPENCODEX_RUNTIME_BUN_VERSION = "1.3.14"
OPENCODEX_RELEASE_ID = (
    f"{OPENCODEX_VERSION}-{OPENCODEX_COMMIT[:12]}-bun{OPENCODEX_RUNTIME_BUN_VERSION}"
)
OPENCODEX_DEFAULT_PORT = 15725
OPENCODEX_CONFIG_HOME = (
    Path.home() / ".config" / "pcl-codex-bridge" / "opencodex"
)
OPENCODEX_INSTALL_HOME = (
    Path.home() / ".local" / "share" / "pcl-codex-bridge" / "opencodex"
)
OPENCODEX_PROXY_RESTART_MARKER = ".pcl-relay-proxy-restart.json"
PCL_PROVIDER = "pcl"
PCL_ADMISSION_PLACEHOLDER = "pcl-tailnet-sidecar"


@dataclass(frozen=True)
class OpenCodexRuntime:
    root: Path
    bun: Path
    cli: Path
    version: str
    commit: str


def _manifest_path(root: Path) -> Optional[Path]:
    candidates = (
        root / "UPSTREAM.json",
        root.parent / "opencodex.UPSTREAM.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _load_manifest(root: Path) -> Dict[str, Any]:
    manifest = _manifest_path(root)
    if manifest is None:
        raise RuntimeError(f"OpenCodex provenance manifest is missing beside {root}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"OpenCodex provenance manifest is invalid: {exc}") from exc
    if payload.get("commit") != OPENCODEX_COMMIT:
        raise RuntimeError("OpenCodex runtime commit does not match the pinned source")
    if payload.get("version") != OPENCODEX_VERSION:
        raise RuntimeError("OpenCodex runtime version does not match the pinned source")
    return payload


def runtime_at(root: Path) -> OpenCodexRuntime:
    root = root.resolve()
    _load_manifest(root)
    package = root / "package.json"
    cli = root / "src" / "cli" / "index.ts"
    bun_candidates = (
        root / "bin" / "bun",
        root / "node_modules" / "bun" / "bin" / "bun",
    )
    bun = next((path for path in bun_candidates if path.is_file()), None)
    if not package.is_file() or not cli.is_file() or bun is None:
        raise RuntimeError(
            "OpenCodex runtime is incomplete; package.json, src/cli/index.ts, and Bun are required"
        )
    try:
        package_payload = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"OpenCodex package.json is invalid: {exc}") from exc
    if package_payload.get("version") != OPENCODEX_VERSION:
        raise RuntimeError("OpenCodex package version does not match the pinned runtime")
    if not os.access(bun, os.X_OK):
        raise RuntimeError(f"Bundled Bun is not executable: {bun}")
    return OpenCodexRuntime(
        root=root,
        bun=bun.resolve(),
        cli=cli,
        version=OPENCODEX_VERSION,
        commit=OPENCODEX_COMMIT,
    )


def bundled_runtime_root(source_root: Path) -> Optional[Path]:
    """Find a built sidecar without treating the source-only vendor tree as runnable."""
    candidates = (
        source_root / "opencodex",
        source_root.parent / "opencodex",
        source_root / "vendor" / "opencodex-runtime",
    )
    return next((path for path in candidates if (path / "UPSTREAM.json").is_file()), None)


def stage_bundled_runtime(
    source_root: Path,
    install_home: Path = OPENCODEX_INSTALL_HOME,
) -> Optional[Dict[str, Any]]:
    """Stage an immutable runtime release; never stop or replace a running service."""
    source = bundled_runtime_root(source_root)
    if source is None:
        return None
    runtime_at(source)
    releases = install_home / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    destination = releases / OPENCODEX_RELEASE_ID
    installed = False
    if not destination.exists():
        temporary = releases / f".{OPENCODEX_RELEASE_ID}.staging-{os.getpid()}"
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            shutil.copytree(source, temporary, symlinks=True)
            runtime_at(temporary)
            os.replace(temporary, destination)
            installed = True
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    current = install_home / "current"
    activated = False
    if not current.exists() and not current.is_symlink():
        current.symlink_to(Path("releases") / OPENCODEX_RELEASE_ID)
        activated = True
    runtime = runtime_at(destination)
    return {
        "runtime": str(runtime.root),
        "version": runtime.version,
        "commit": runtime.commit,
        "installed": installed,
        "activated": activated,
        "current": str(current),
        "service_restarted": False,
    }


def installed_runtime(
    install_home: Path = OPENCODEX_INSTALL_HOME,
) -> OpenCodexRuntime:
    return runtime_at(install_home / "current")


def sidecar_environment(
    config_home: Path = OPENCODEX_CONFIG_HOME,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    environment = dict(os.environ)
    environment["OPENCODEX_HOME"] = str(config_home)
    if extra:
        environment.update(extra)
    if not environment.get("CODEX_CLI_PATH"):
        # OpenCodex owns runtime validation, persistence, catalog extraction,
        # and version compatibility. The macOS app only supplies the Codex
        # launcher it already discovers for its existing client diagnostics.
        from .client_config import find_codex

        codex = find_codex()
        if codex:
            environment["CODEX_CLI_PATH"] = codex
    return environment


def invoke_sidecar(
    runtime: OpenCodexRuntime,
    arguments: Sequence[str],
    config_home: Path = OPENCODEX_CONFIG_HOME,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(runtime.bun), str(runtime.cli), *arguments],
        cwd=runtime.root,
        env=sidecar_environment(config_home),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


SidecarRunner = Callable[
    [OpenCodexRuntime, Sequence[str], Path, int], subprocess.CompletedProcess
]


def _run_checked(
    runner: SidecarRunner,
    runtime: OpenCodexRuntime,
    arguments: Sequence[str],
    config_home: Path,
    timeout: int = 60,
) -> Dict[str, Any]:
    result = runner(runtime, arguments, config_home, timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "OpenCodex command failed").strip()
        raise RuntimeError(f"OpenCodex {' '.join(arguments[:3])} failed: {detail}")
    raw = result.stdout.strip()
    if not raw:
        return {"ok": True}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except ValueError:
        return {"ok": True, "output": raw}


def _run_json(
    runner: SidecarRunner,
    runtime: OpenCodexRuntime,
    arguments: Sequence[str],
    config_home: Path,
    timeout: int = 60,
) -> Dict[str, Any]:
    result = runner(runtime, arguments, config_home, timeout)
    raw = result.stdout.strip()
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    payload = value if isinstance(value, dict) else {}
    payload.setdefault("ok", result.returncode == 0)
    payload.setdefault("exit_code", result.returncode)
    return payload


def _run_idempotent_unset(
    runner: SidecarRunner,
    runtime: OpenCodexRuntime,
    arguments: Sequence[str],
    config_home: Path,
) -> Dict[str, Any]:
    """Delegate unset to OpenCodex while accepting its two no-op outcomes.

    OpenCodex reports a missing config file or dot-path as an error. For a
    transactional restore/clear those states already equal the requested
    result, so treating them as failures hides the original error behind a
    misleading "rollback failed" message.
    """
    result = runner(runtime, arguments, config_home, 60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "OpenCodex command failed").strip()
        normalized = detail.lower()
        if "config is missing" in normalized:
            return {
                "ok": True,
                "changed": False,
                "already_absent": True,
                "config_missing": True,
            }
        if "config path not found" in normalized:
            return {"ok": True, "changed": False, "already_absent": True}
        raise RuntimeError(f"OpenCodex {' '.join(arguments[:3])} failed: {detail}")
    raw = result.stdout.strip()
    if not raw:
        return {"ok": True}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except ValueError:
        return {"ok": True, "output": raw}


def _run_proxy_config_command(
    runner: SidecarRunner,
    runtime: OpenCodexRuntime,
    arguments: Sequence[str],
    config_home: Path,
) -> Dict[str, Any]:
    if list(arguments[:2]) == ["config", "unset"]:
        return _run_idempotent_unset(runner, runtime, arguments, config_home)
    return _run_checked(runner, runtime, arguments, config_home)


def _normalized_gateway(gateway_url: str) -> str:
    value = gateway_url.rstrip("/")
    if not value.endswith("/v1"):
        value += "/v1"
    parsed = urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise RuntimeError("PCL gateway must be an explicit http://host:port/v1 URL")
    return value


def _selected_models(models: Sequence[str]) -> List[str]:
    selected: List[str] = []
    for value in models:
        model = str(value).strip()
        if model and model not in selected:
            selected.append(model)
    if not selected:
        raise RuntimeError("At least one PCL text-agent model is required")
    return selected


def _validated_port(port: int) -> int:
    value = int(port)
    if value < 1 or value > 65535:
        raise RuntimeError("OpenCodex port must be between 1 and 65535")
    return value


def configure_sidecar(
    runtime: OpenCodexRuntime,
    gateway_url: str,
    models: Sequence[str],
    enable_codex: bool = False,
    port: int = OPENCODEX_DEFAULT_PORT,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Configure only provider/control-plane state through OpenCodex's own CLI."""
    gateway = _normalized_gateway(gateway_url)
    selected = _selected_models(models)
    subagents = [f"{PCL_PROVIDER}/{model}" for model in selected[:5]]
    listen_port = _validated_port(port)
    commands: List[List[str]] = [
        [
            "provider", "add", PCL_PROVIDER,
            "--adapter", "openai-chat",
            "--base-url", gateway,
            "--api-key", PCL_ADMISSION_PLACEHOLDER,
            "--default-model", selected[0],
            "--allow-private-network",
            "--force",
            "--json",
        ],
        ["config", "set", "port", str(listen_port), "--json"],
        ["config", "set", "websockets", "true", "--json"],
        ["config", "set", "providers.pcl.selectedModels", json.dumps(selected), "--json"],
        ["config", "set", "providers.pcl.retainModels", json.dumps(selected), "--json"],
        ["config", "set", "providers.openai.codexAccountMode", json.dumps("direct"), "--json"],
        ["config", "set", "defaultModelAliases", "false", "--json"],
        ["config", "set", "fastRows", "false", "--json"],
        ["config", "set", "emptyCompletionRetry", "false", "--json"],
        ["config", "set", "subagentModels", json.dumps(subagents), "--json"],
        ["config", "set", "multiAgentMode", json.dumps("v2"), "--json"],
        ["config", "set", "keepNativeChatGptOnV1", "true", "--json"],
        ["config", "set", "multiAgentGuidanceEnabled", "true", "--json"],
    ]
    commands.append(["config", "validate", "--json"])
    commands.append(["config", "set", "clientIntegrations", json.dumps({"codex": enable_codex}), "--json"])

    config_home.mkdir(parents=True, exist_ok=True)
    os.chmod(config_home, 0o700)
    results = [
        _run_checked(runner, runtime, command, config_home)
        for command in commands
    ]
    return {
        "configured": True,
        "runtime_version": runtime.version,
        "runtime_commit": runtime.commit,
        "config_home": str(config_home),
        "gateway": gateway,
        "provider": PCL_PROVIDER,
        "port": listen_port,
        "selected_models": selected,
        "subagent_models": subagents,
        "official_provider": "openai",
        "official_account_mode": "direct",
        "codex_integration_enabled": enable_codex,
        "transport_implementation": "upstream-opencodex",
        "commands": len(results),
    }


def switch_pcl_gateway(
    runtime: OpenCodexRuntime,
    gateway_url: str,
    models: Sequence[str],
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Atomically reconfigure only the ``pcl`` provider in OpenCodex.

    Service lifecycle, official-provider settings and Codex integration are
    intentionally untouched.  The caller owns rollback to its previous URL
    if any upstream command or validation step fails.
    """
    gateway = _normalized_gateway(gateway_url)
    selected = _selected_models(models)
    commands: List[List[str]] = [
        [
            "provider", "add", PCL_PROVIDER,
            "--adapter", "openai-chat",
            "--base-url", gateway,
            "--api-key", PCL_ADMISSION_PLACEHOLDER,
            "--default-model", selected[0],
            "--allow-private-network",
            "--force",
            "--json",
        ],
        ["config", "set", "providers.pcl.selectedModels", json.dumps(selected), "--json"],
        ["config", "set", "providers.pcl.retainModels", json.dumps(selected), "--json"],
        ["config", "validate", "--json"],
    ]
    results = [_run_checked(runner, runtime, command, config_home) for command in commands]
    return {
        "configured": True,
        "provider": PCL_PROVIDER,
        "gateway": gateway,
        "selected_models": selected,
        "service_restarted": False,
        "official_route_changed": False,
        "commands": len(results),
    }


def opencodex_proxy_policy(
    runtime: OpenCodexRuntime,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Read OpenCodex's own explicit outbound policy without probing a network."""
    config = _run_checked(runner, runtime, ["config", "show", "--json"], config_home)
    raw_no_proxy = config.get("noProxy")
    if isinstance(raw_no_proxy, str):
        no_proxy = [raw_no_proxy]
    elif isinstance(raw_no_proxy, list):
        no_proxy = [str(value) for value in raw_no_proxy]
    else:
        no_proxy = []
    proxy = config.get("proxy")
    pending = _proxy_restart_pending(runtime, config_home, runner)
    return {
        "proxy": str(proxy) if isinstance(proxy, str) else "",
        "no_proxy": no_proxy,
        "configured": isinstance(proxy, str) or bool(no_proxy),
        "implementation": "upstream-opencodex",
        "automatic_discovery": False,
        "network_managed": False,
        "restart_required": pending,
        "service_restarted": False,
    }


def _proxy_restart_marker(config_home: Path) -> Path:
    return config_home / OPENCODEX_PROXY_RESTART_MARKER


def _write_proxy_restart_marker(config_home: Path, pid: Optional[int]) -> None:
    config_home.mkdir(parents=True, exist_ok=True)
    marker = _proxy_restart_marker(config_home)
    temporary = marker.with_name(f".{marker.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps({"pid": pid if isinstance(pid, int) else None}) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, marker)


def _clear_proxy_restart_marker(config_home: Path) -> None:
    try:
        _proxy_restart_marker(config_home).unlink()
    except FileNotFoundError:
        pass


def _proxy_restart_pending(
    runtime: OpenCodexRuntime,
    config_home: Path,
    runner: SidecarRunner,
) -> bool:
    marker = _proxy_restart_marker(config_home)
    if not marker.is_file():
        return False
    health = sidecar_health(runtime, config_home, runner)
    if health.get("ok") is not True:
        # A stopped service will load the persisted policy on its next start.
        _clear_proxy_restart_marker(config_home)
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return True
    previous_pid = payload.get("pid") if isinstance(payload, dict) else None
    current_pid = health.get("pid")
    if isinstance(previous_pid, int) and isinstance(current_pid, int) and previous_pid != current_pid:
        # A different healthy process proves the persisted startup policy was loaded.
        _clear_proxy_restart_marker(config_home)
        return False
    return True


def apply_pending_opencodex_proxy_policy(
    runtime: OpenCodexRuntime,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Apply a saved startup-only policy through OpenCodex's own lifecycle.

    Active turns are never interrupted: the operation remains pending and can
    be retried after they finish. OpenCodex itself owns restart attestation,
    drain, replacement and readiness verification.
    """
    health = sidecar_health(runtime, config_home, runner)
    if health.get("ok") is not True:
        _clear_proxy_restart_marker(config_home)
        return {
            "restart_required": False,
            "service_restarted": False,
            "active_turn_count": 0,
            "lifecycle_reason": "sidecar_stopped_policy_applies_on_next_start",
        }

    activity = _run_json(runner, runtime, ["memory", "--json"], config_home, 20)
    active = activity.get("activeTurnCount")
    draining = activity.get("isDraining")
    if activity.get("ok") is not True or not isinstance(active, int) or not isinstance(draining, bool):
        _write_proxy_restart_marker(config_home, health.get("pid"))
        return {
            "restart_required": True,
            "service_restarted": False,
            "active_turn_count": None,
            "lifecycle_reason": "activity_state_unavailable",
        }
    if active > 0 or draining:
        _write_proxy_restart_marker(config_home, health.get("pid"))
        return {
            "restart_required": True,
            "service_restarted": False,
            "active_turn_count": active,
            "lifecycle_reason": "active_requests" if active > 0 else "already_draining",
        }

    restart = runner(runtime, ["restart"], config_home, 180)
    if restart.returncode != 0:
        _write_proxy_restart_marker(config_home, health.get("pid"))
        detail = (restart.stderr or restart.stdout or "OpenCodex restart failed").strip()
        return {
            "restart_required": True,
            "service_restarted": False,
            "active_turn_count": 0,
            "lifecycle_reason": "upstream_restart_failed",
            "restart_error": detail,
        }
    ready = require_sidecar_ready(runtime, config_home=config_home, runner=runner)
    old_pid = health.get("pid")
    new_pid = ready.get("pid")
    if isinstance(old_pid, int) and isinstance(new_pid, int) and old_pid == new_pid:
        _write_proxy_restart_marker(config_home, old_pid)
        return {
            "restart_required": True,
            "service_restarted": False,
            "active_turn_count": 0,
            "lifecycle_reason": "replacement_identity_unchanged",
        }
    _clear_proxy_restart_marker(config_home)
    return {
        "restart_required": False,
        "service_restarted": True,
        "active_turn_count": 0,
        "lifecycle_reason": "upstream_drain_restart_complete",
        "previous_pid": old_pid,
        "pid": new_pid,
    }


def configure_opencodex_proxy_policy(
    runtime: OpenCodexRuntime,
    proxy: Optional[str] = None,
    no_proxy: Optional[Sequence[str]] = None,
    clear: bool = False,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Apply an explicit user request through the complete OpenCodex CLI.

    The previous upstream configuration is restored if any command or
    validation fails. No port scanning, proxy detection or network recovery is
    performed here.
    """
    if not clear and proxy is None and no_proxy is None:
        raise RuntimeError("Set --proxy and/or --no-proxy, or use routes proxy clear")
    before = opencodex_proxy_policy(runtime, config_home, runner)
    commands: List[List[str]] = []
    if clear:
        commands.extend([
            ["config", "unset", "proxy", "--json"],
            ["config", "unset", "noProxy", "--json"],
        ])
    else:
        if proxy is not None:
            value = proxy.strip()
            commands.append(
                ["config", "set", "proxy", json.dumps(value), "--json"]
                if value
                else ["config", "unset", "proxy", "--json"]
            )
        if no_proxy is not None:
            values = [str(value).strip() for value in no_proxy if str(value).strip()]
            commands.append(
                ["config", "set", "noProxy", json.dumps(values), "--json"]
                if values
                else ["config", "unset", "noProxy", "--json"]
            )
    commands.append(["config", "validate", "--json"])

    try:
        config_missing_noop = False
        for command in commands:
            if command[:3] == ["config", "validate", "--json"] and config_missing_noop:
                continue
            result = _run_proxy_config_command(runner, runtime, command, config_home)
            config_missing_noop = config_missing_noop or result.get("config_missing") is True
    except Exception as apply_error:
        restore_commands = [
            ["config", "set", "proxy", json.dumps(before["proxy"]), "--json"]
            if before["proxy"]
            else ["config", "unset", "proxy", "--json"],
            ["config", "set", "noProxy", json.dumps(before["no_proxy"]), "--json"]
            if before["no_proxy"]
            else ["config", "unset", "noProxy", "--json"],
            ["config", "validate", "--json"],
        ]
        try:
            restore_config_missing_noop = False
            for command in restore_commands:
                if command[:3] == ["config", "validate", "--json"] and restore_config_missing_noop:
                    continue
                result = _run_proxy_config_command(runner, runtime, command, config_home)
                restore_config_missing_noop = (
                    restore_config_missing_noop or result.get("config_missing") is True
                )
        except Exception as restore_error:
            raise RuntimeError(
                f"OpenCodex proxy policy failed and rollback failed: {restore_error}"
            ) from apply_error
        raise RuntimeError(f"OpenCodex proxy policy was rejected and rolled back: {apply_error}") from apply_error
    health = sidecar_health(runtime, config_home, runner)
    _write_proxy_restart_marker(config_home, health.get("pid"))
    lifecycle = apply_pending_opencodex_proxy_policy(runtime, config_home, runner)
    return {
        **opencodex_proxy_policy(runtime, config_home, runner),
        "changed": True,
        **lifecycle,
    }


def sidecar_health(
    runtime: OpenCodexRuntime,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Read OpenCodex's own liveness contract without adding another probe."""
    return _run_json(runner, runtime, ["health", "--json"], config_home, 15)


def sidecar_integration_status(
    runtime: OpenCodexRuntime,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Read durable intent and applied routing from OpenCodex's own status surfaces.

    OpenCodex 2.46.0's live ``integration native list`` route reports the
    server's startup config snapshot. After its own Codex toggle commits a new
    desired state, that list can therefore remain stale until the service
    restarts. ``config show`` reads the durable config, while ``status``
    inspects the applied Codex artifacts; together they are OpenCodex's
    authoritative persisted and observed states.
    """
    runtime_status = _run_json(
        runner,
        runtime,
        ["status", "--json"],
        config_home,
        30,
    )
    persisted_config = _run_json(
        runner,
        runtime,
        ["config", "show", "--json"],
        config_home,
        30,
    )
    integrations = persisted_config.get("clientIntegrations")
    integrations = integrations if isinstance(integrations, dict) else {}
    desired_enabled = integrations.get("codex") is not False

    startup = runtime_status.get("startup")
    startup = startup if isinstance(startup, dict) else {}
    routing_injected = startup.get("routingInjected") is True
    codex_home = runtime_status.get("codexHome")
    codex_home = codex_home if isinstance(codex_home, dict) else {}
    effective_home = codex_home.get("effectiveCodexHome")
    codex = {
        "clientId": "codex",
        "state": "current" if routing_injected else "absent",
        "installed": True,
        "configPath": str(Path(effective_home) / "config.toml")
        if isinstance(effective_home, str) and effective_home
        else "",
        "desiredEnabled": desired_enabled,
        "disableBlocked": None,
    }
    ok = runtime_status.get("ok") is True and persisted_config.get("ok") is True
    return {
        "ok": ok,
        "clients": [codex],
        "codex": codex,
        "status": runtime_status,
        "transport_implementation": "upstream-opencodex",
    }


def require_sidecar_ready(
    runtime: OpenCodexRuntime,
    port: int = OPENCODEX_DEFAULT_PORT,
    wait_seconds: int = 60,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Wait on OpenCodex's native readiness gate and require the pinned port."""
    expected_port = _validated_port(port)
    result = _run_checked(
        runner,
        runtime,
        [
            "ready",
            "--json",
            "--wait",
            "--timeout",
            str(max(1, int(wait_seconds))),
        ],
        config_home,
        max(15, int(wait_seconds) + 10),
    )
    if result.get("ready") is not True:
        raise RuntimeError("OpenCodex reported that the sidecar is not ready")
    if int(result.get("port") or 0) != expected_port:
        raise RuntimeError(
            f"OpenCodex is ready on port {result.get('port')}, expected {expected_port}"
        )
    return result


def prepare_sidecar(
    runtime: OpenCodexRuntime,
    gateway_url: str,
    models: Sequence[str],
    port: int = OPENCODEX_DEFAULT_PORT,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Configure and start upstream OpenCodex with Codex injection still OFF.

    A healthy service is never restarted here. A healthy service on another
    port is refused so an App refresh cannot silently move the data plane.
    """
    expected_port = _validated_port(port)
    before = sidecar_health(runtime, config_home, runner)
    if before.get("ok") and before.get("port") not in (None, expected_port):
        raise RuntimeError(
            f"OpenCodex is already healthy on port {before.get('port')}; "
            "refusing to restart an active data plane"
        )

    configured = configure_sidecar(
        runtime,
        gateway_url,
        models,
        enable_codex=False,
        port=expected_port,
        config_home=config_home,
        runner=runner,
    )
    service_started = not bool(before.get("ok"))
    if service_started:
        _run_checked(runner, runtime, ["service", "install"], config_home, 90)
    ready = require_sidecar_ready(
        runtime,
        expected_port,
        config_home=config_home,
        runner=runner,
    )
    return {
        **configured,
        "prepared": True,
        "ready": True,
        "pid": ready.get("pid"),
        "service_started": service_started,
        "codex_integration_enabled": False,
    }


def activate_sidecar(
    runtime: OpenCodexRuntime,
    port: int = OPENCODEX_DEFAULT_PORT,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Delegate Codex injection, catalog sync, journaling and history to OpenCodex."""
    ready = require_sidecar_ready(
        runtime,
        port,
        config_home=config_home,
        runner=runner,
    )
    activated = _run_checked(
        runner,
        runtime,
        ["integration", "native", "codex", "on", "--json"],
        config_home,
        180,
    )
    if not (
        activated.get("ok") is True
        and activated.get("desiredEnabled") is True
        and activated.get("state") == "current"
    ):
        raise RuntimeError("OpenCodex did not activate the Codex integration")
    return {
        "active": True,
        "port": ready.get("port"),
        "pid": ready.get("pid"),
        "activation": activated,
        "transport_implementation": "upstream-opencodex",
    }


def deactivate_sidecar(
    runtime: OpenCodexRuntime,
    config_home: Path = OPENCODEX_CONFIG_HOME,
    runner: SidecarRunner = invoke_sidecar,
) -> Dict[str, Any]:
    """Restore native Codex through OpenCodex while leaving the sidecar alive."""
    restored = _run_checked(
        runner,
        runtime,
        ["integration", "native", "codex", "off", "--json"],
        config_home,
        180,
    )
    if not (restored.get("ok") is True and restored.get("desiredEnabled") is False):
        raise RuntimeError("OpenCodex did not restore native Codex")
    return {
        "active": False,
        "sidecar_stopped": False,
        "restore": restored,
        "transport_implementation": "upstream-opencodex",
    }
