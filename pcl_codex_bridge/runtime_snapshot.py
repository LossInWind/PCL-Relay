"""Small read-only status contract; no discovery, credentials or service changes."""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


def runtime_snapshot(home=None):
    home = Path(home) if home else Path.home()
    result = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "installed_version": None, "opencodex_version": None,
              "configured_endpoint": None, "transport_healthy": None,
              "model_call_verified": False}
    try:
        result["installed_version"] = (home / ".local/share/pcl-codex-bridge/VERSION").read_text().strip() or None
    except OSError:
        pass
    try:
        config = json.loads((home / ".config/pcl-codex-bridge/opencodex/config.json").read_text())
        raw = config.get("providers", {}).get("pcl", {}).get("baseUrl", "")
        url = urllib.parse.urlsplit(raw)
        if url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password and not url.query and not url.fragment:
            result["configured_endpoint"] = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, "", ""))
        port = int(config.get("port", 15725))
        if not 1 <= port <= 65535:
            return result
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            health = json.loads(response.read(65536))
        if health.get("service") == "opencodex" and health.get("status") == "ok":
            result["transport_healthy"] = True
            result["opencodex_version"] = health.get("version")
        else:
            result["transport_healthy"] = False
    except (OSError, ValueError, TypeError, AttributeError):
        # Missing metadata is unknown, never a substitute for model success.
        pass
    return result
