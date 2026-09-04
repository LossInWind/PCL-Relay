from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_GATEWAY_URL = os.environ.get(
    "PCL_CODEX_GATEWAY_URL",
    "http://haichen-pcl-linux-3070ti.tail132f30.ts.net:15722/v1",
).rstrip("/")

AGENTS: Dict[str, Dict[str, str]] = {
    "pcl_deepseek_pro": {
        "model": "DeepSeek-V4-Pro",
        "description": "PCL DeepSeek Pro execution agent for difficult coding, debugging, and reasoning tasks.",
    },
    "pcl_deepseek_flash": {
        "model": "DeepSeek-V4-Flash-0731",
        "description": "Fast PCL DeepSeek execution agent for focused edits, tests, and repository exploration.",
    },
    "pcl_glm": {
        "model": "GLM-5.2",
        "description": "PCL GLM execution agent, especially useful for Chinese technical work and independent review.",
    },
    "pcl_kimi": {
        "model": "Kimi-K3",
        "description": "PCL Kimi execution agent for long-context reading, synthesis, and cross-checking.",
    },
}


def model_alias(model_id: str) -> str:
    for alias, info in AGENTS.items():
        if info["model"] == model_id:
            return alias
    slug = re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
    if not slug:
        slug = "model_" + hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:8]
    return "pcl_" + slug[:48]


def model_details(model_id: str, owned_by: str = "") -> Dict[str, Any]:
    lower = model_id.lower()
    category = "chat"
    family = "Other"
    description = "PCL 内网提供的文本生成模型；启用前建议先运行能力检测。"
    eligible = True
    modalities = ["text"]

    if "deepseek" in lower:
        family = "DeepSeek"
        description = "适合代码生成、复杂推理、调试和执行型 Agent 任务。"
    elif lower.startswith("glm") or "chatglm" in lower:
        family = "GLM"
        description = "适合中文技术任务、代码工作和独立审查。"
    elif "kimi" in lower:
        family = "Kimi"
        description = "适合长上下文阅读、检索、总结和跨文件分析。"
    elif "qwen" in lower and "image" not in lower:
        family = "Qwen"
        description = "通义千问文本模型；适合中文、代码和通用 Agent 任务。"
    elif "pcnl" in lower or "本地大模型" in model_id:
        family = "PCL"
        description = "PCL 本地部署的通用大模型；具体能力以在线检测结果为准。"
    elif "bge" in lower and "rerank" in lower:
        family, category, eligible = "BGE", "reranker", False
        description = "文本重排序模型，用于检索结果排序，不能作为 Codex 执行 Agent。"
    elif "bge" in lower:
        family, category, eligible = "BGE", "embedding", False
        description = "文本向量模型，用于语义检索，不能作为 Codex 执行 Agent。"
    elif "whisper" in lower:
        family, category, eligible = "Whisper", "speech", False
        modalities = ["audio"]
        description = "语音识别模型，不支持 Codex 文本执行 Agent 协议。"
    elif "ocr" in lower:
        family, category, eligible = "PaddleOCR", "vision-ocr", False
        modalities = ["text", "image"]
        description = "视觉文字识别模型；当前子 Agent 仅支持文本输入，因此不可启用。"
    elif "image" in lower:
        family, category, eligible = "Qwen Image", "image", False
        modalities = ["text", "image"]
        description = "图像生成或编辑模型，不支持 Codex 文本执行 Agent 协议。"

    alias = model_alias(model_id)
    return {
        "id": model_id,
        "alias": alias,
        "family": family,
        "category": category,
        "description": description,
        "agent_eligible": eligible,
        "recommended": alias in AGENTS,
        "owned_by": owned_by or "pcl",
        "input_modalities": modalities,
    }


def available_model_records(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            model_id = str(entry["id"])
            records[model_id] = model_details(model_id, str(entry.get("owned_by") or ""))
    return records


def configured_agents(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    data = registry if isinstance(registry, dict) else load_registry()
    stored = data.get("agent_definitions") if isinstance(data, dict) else None
    selected = data.get("selected_agents") if isinstance(data, dict) else None
    definitions: Dict[str, Dict[str, str]] = {}
    if isinstance(stored, dict):
        for alias, info in stored.items():
            if isinstance(info, dict) and info.get("model"):
                definitions[str(alias)] = {
                    "model": str(info["model"]),
                    "description": str(info.get("description") or model_details(str(info["model"]))["description"]),
                }
    if not definitions:
        definitions = dict(AGENTS)
    if isinstance(selected, list):
        filtered = {name: definitions[name] for name in selected if name in definitions}
        if filtered:
            return filtered
    return definitions


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def registry_path() -> Path:
    return Path(
        os.environ.get(
            "PCL_CODEX_REGISTRY",
            Path.home() / ".config" / "pcl-codex-bridge" / "models.json",
        )
    ).expanduser()


def load_registry() -> Dict[str, Any]:
    path = registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_registry(data: Dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def model_catalog(definitions: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    levels = [
        {"effort": "low", "description": "Brief prompt-steered reasoning"},
        {"effort": "medium", "description": "Balanced prompt-steered reasoning"},
        {"effort": "high", "description": "Thorough prompt-steered reasoning"},
        {"effort": "xhigh", "description": "Very thorough prompt-steered reasoning"},
    ]
    models = []
    for priority, (agent, info) in enumerate((definitions or configured_agents()).items(), 1):
        models.append(
            {
                "slug": info["model"],
                "display_name": info["model"],
                "description": info["description"],
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": levels,
                "shell_type": "unified_exec",
                "visibility": "list",
                "supported_in_api": True,
                "priority": priority,
                "availability_nux": None,
                "upgrade": None,
                "model_messages": {"instructions_template": None},
                "include_skills_usage_instructions": False,
                "include_plugin_usage_instructions": False,
                "include_apps_usage_instructions": False,
                "default_reasoning_summary": "detailed",
                "supports_parallel_tool_calls": True,
                "support_verbosity": False,
                "default_verbosity": "low",
                "apply_patch_tool_type": "freeform",
                "web_search_tool_type": "text",
                "supports_websockets": False,
                "supports_image_detail_original": False,
                "context_window": 128000,
                "max_context_window": 128000,
                "comp_hash": "pcl-v1",
                "effective_context_window_percent": 90,
                "auto_compact_token_limit": 100000,
                "input_modalities": ["text"],
                "truncation_policy": {"mode": "tokens", "limit": 10000},
                "experimental_supported_tools": [],
                "supports_search_tool": False,
                "use_responses_lite": False,
                "node_repl_auto_review_required": False,
                "node_repl_disabled": True,
                "base_instructions": (
                    "You are Codex operating as a delegated PCL execution agent. "
                    "Inspect the workspace, preserve unrelated changes, implement the delegated task, "
                    "run proportionate tests, and report files changed and remaining blockers."
                ),
            }
        )
    return {"models": models}
