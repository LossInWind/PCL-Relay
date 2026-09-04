from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Optional


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
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
