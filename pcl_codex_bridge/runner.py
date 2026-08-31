from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .client_config import UNSANDBOXED_MARKER, find_codex
from .models import configured_agents, load_registry


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args], capture_output=True, text=True, timeout=30, check=False
    )
    return result.stdout if result.returncode == 0 else ""


def _is_git_repository(workspace: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


FileState = Tuple[int, int, int]


def _snapshot_files(workspace: Path) -> Dict[str, FileState]:
    """Record enough metadata to report changes in a non-Git workspace."""
    state: Dict[str, FileState] = {}
    for path in workspace.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            state[str(path.relative_to(workspace))] = (stat.st_size, stat.st_mtime_ns, stat.st_mode)
        except OSError:
            continue
    return state


def _changed_files(before: Dict[str, FileState], after: Dict[str, FileState]) -> List[str]:
    changed: List[str] = []
    for name in sorted(before.keys() | after.keys()):
        if name not in before:
            changed.append(f"created: {name}")
        elif name not in after:
            changed.append(f"deleted: {name}")
        elif before[name] != after[name]:
            changed.append(f"modified: {name}")
    return changed


def _lock_path(workspace: Path) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()[:24]
    root = Path.home() / ".cache" / "pcl-codex-bridge" / "locks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{digest}.lock"


@contextmanager
def _write_lock(workspace: Path) -> Iterator[None]:
    lock_path = _lock_path(workspace)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _extract_final(events: List[Dict[str, Any]], stdout: str) -> str:
    messages: List[str] = []
    for event in events:
        item = event.get("item") if isinstance(event, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"agent_message", "message"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                messages.append(text)
    if messages:
        return messages[-1]
    return stdout[-12000:]


def delegate(
    agent: str,
    task: str,
    workspace: str,
    timeout: int = 1800,
    execution_mode: str = "workspace-write",
) -> Dict[str, Any]:
    agents = configured_agents()
    if agent not in agents:
        raise ValueError(f"Unknown PCL agent: {agent}")
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Workspace does not exist: {root}")
    if root in {Path("/"), Path.home().resolve(), Path("/tmp"), Path("/home")}:
        raise ValueError(f"Refusing an unsafe workspace root: {root}")
    if execution_mode not in {"read-only", "workspace-write"}:
        raise ValueError("execution_mode must be read-only or workspace-write")
    timeout = max(30, min(int(timeout), 3600))
    codex = find_codex()
    if not codex:
        raise RuntimeError("Codex executable was not found; set PCL_CODEX_BIN")

    registry = load_registry()
    model_status = ((registry.get("models") or {}).get(agent) or {}) if isinstance(registry, dict) else {}
    if model_status and not model_status.get("chat", False):
        raise RuntimeError(f"{agent} is not marked available; run pcl-codex models detect")

    model = agents[agent]["model"]
    is_git = _is_git_repository(root)
    pre_status = _git(root, "status", "--short") if is_git else ""
    pre_files = {} if is_git else _snapshot_files(root)
    started = time.time()
    prompt = (
        f"You are the named PCL execution agent {agent}, using model {model}. "
        "Complete the delegated task inside the provided workspace. Preserve unrelated user changes. "
        "Inspect before editing, run proportionate tests, and finish with a concise summary of files changed, "
        "tests run, and any remaining blocker.\n\nDelegated task:\n" + task
    )
    if execution_mode == "read-only":
        prompt = "This is a read-only analysis. Do not create, modify, or delete any file.\n\n" + prompt
    unsandboxed_fallback = sys.platform.startswith("linux") and UNSANDBOXED_MARKER.exists()
    effective_sandbox = "danger-full-access(opt-in)" if unsandboxed_fallback else execution_mode
    command = [
        codex,
        "exec",
        "--profile",
        "pcl-agent",
        "--model",
        model,
        "--cd",
        str(root),
        "--sandbox",
        "danger-full-access" if unsandboxed_fallback else execution_mode,
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "--disable",
        "multi_agent",
        "--disable",
        "apps",
        "--disable",
        "plugins",
        "--disable",
        "recommended_plugins",
        "--disable",
        "enable_mcp_apps",
        "--config",
        'approval_policy="never"',
        "--config",
        "sandbox_workspace_write.network_access=true",
        prompt,
    ]

    lock_context = _write_lock(root) if execution_mode == "workspace-write" else nullcontext()
    with lock_context:
        try:
            environment = os.environ.copy()
            bypass = "localhost,127.0.0.1,100.64.0.0/10,.tail132f30.ts.net,haichen-pcl-linux-3070ti.tail132f30.ts.net"
            environment["NO_PROXY"] = bypass
            environment["no_proxy"] = bypass
            process = subprocess.run(
                command,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            process = None
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")

    if process is not None:
        stdout, stderr = process.stdout, process.stderr
        returncode = process.returncode
    else:
        returncode = 124

    events: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            continue
    post_status = _git(root, "status", "--short") if is_git else ""
    post_files = {} if is_git else _snapshot_files(root)
    modified_files = [] if is_git else _changed_files(pre_files, post_files)
    diff = (
        _git(root, "diff", "--no-ext-diff", "--stat") + _git(root, "diff", "--no-ext-diff")
        if is_git
        else ""
    )
    if len(diff) > 50000:
        diff = diff[:50000] + "\n[diff truncated]\n"
    return {
        "agent": agent,
        "model": model,
        "workspace": str(root),
        "execution_mode": execution_mode,
        "effective_sandbox": effective_sandbox,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.time() - started, 2),
        "summary": _extract_final(events, stdout),
        "git_repository": is_git,
        "git_status_before": pre_status,
        "git_status_after": post_status,
        "git_diff": diff,
        "modified_files": modified_files,
        "stderr_tail": stderr[-8000:],
    }
