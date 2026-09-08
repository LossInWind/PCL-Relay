from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def safe_http_error(exc: urllib.error.HTTPError) -> urllib.error.HTTPError:
    """Keep bounded structured diagnostics; never print raw HTML/headers/keys."""
    reason = str(exc.reason)
    try:
        payload = json.loads(exc.read(8192))
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            try:
                payload = json.loads(payload["detail"])
            except (ValueError, TypeError):
                pass
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str):
                reason += ": " + message
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
                reason += " [" + code + "]"
    except (ValueError, OSError):
        pass
    reason = re.sub(r"https?://\S+", "[URL]", reason)
    reason = re.sub(r"(?i)bearer\s+\S+|sk-[A-Za-z0-9_-]+", "[REDACTED]", reason)
    reason = re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+", r"\1=[REDACTED]", reason)
    reason = " ".join(reason.split())[:600]
    return urllib.error.HTTPError(exc.url, exc.code, reason, None, None)


def gateway_root(url: str) -> str:
    return url.rstrip("/")


def request_json(url: str, body: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Any:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise safe_http_error(exc) from None
