from __future__ import annotations

import os
import urllib.parse
from typing import Tuple


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


def resolve_official_proxy(saved: str = "") -> Tuple[str, str]:
    """Resolve only standard process/user configuration.

    PCL Relay never reads another application's state.  This helper remains
    for diagnostics and explicit legacy callers; the OpenCodex integration
    deliberately does not persist its result, so official Codex traffic keeps
    the environment and networking semantics of the upstream implementation.
    """
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
