from __future__ import annotations

import json
import os
import stat
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple


HAICHEN_SERVICES_PROXY_STATE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Haichen Services"
    / "official-proxy.json"
)


def normalize_proxy_url(value: str) -> str:
    """Return a supported explicit proxy URL, or an empty string."""
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urllib.parse.urlparse(candidate)
    try:
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or port is None:
        return ""
    return candidate


def haichen_services_proxy() -> Optional[str]:
    """Read Haichen Services' proxy decision without managing the network.

    ``None`` means that Haichen Services has not published a contract.  An
    empty string is different: the publisher is present and explicitly says
    that no proxy is available.  The file is accepted only when it belongs to
    the current user and cannot be edited by group/other users.
    """
    path = HAICHEN_SERVICES_PROXY_STATE
    try:
        metadata = path.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("provider") != "haichen-services":
        return None
    if payload.get("available") is False:
        return ""
    value = normalize_proxy_url(str(payload.get("official_proxy_url") or ""))
    return value or None


def resolve_official_proxy(saved: str = "") -> Tuple[str, str]:
    """Resolve one configured route; never scan ports or switch networks."""
    managed = haichen_services_proxy()
    if managed is not None:
        return managed, "haichen-services"
    candidates = (
        ("PCL_RELAY_OFFICIAL_PROXY", os.environ.get("PCL_RELAY_OFFICIAL_PROXY", "")),
        ("HTTPS_PROXY", os.environ.get("HTTPS_PROXY", "")),
        ("https_proxy", os.environ.get("https_proxy", "")),
        ("registry", saved),
    )
    for source, value in candidates:
        normalized = normalize_proxy_url(value)
        if normalized:
            return normalized, source
    return "", "system"
