"""Remove the retired /app/docs bind from normalized Compose JSON."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def sanitize(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    services = config.get("services")
    if not isinstance(services, dict):
        raise ValueError("compose_services_missing")
    backend = services.get("backend")
    if not isinstance(backend, dict):
        raise ValueError("compose_backend_missing")
    volumes = backend.get("volumes", [])
    if not isinstance(volumes, list):
        raise ValueError("compose_backend_volumes_invalid")

    retained: list[dict[str, Any]] = []
    removed = 0
    for volume in volumes:
        if not isinstance(volume, dict):
            raise ValueError("compose_backend_volume_not_normalized")
        if volume.get("target") == "/app/docs":
            removed += 1
            continue
        retained.append(volume)
    backend["volumes"] = retained
    return config, removed


def apply_production_volumes(config: dict[str, Any]) -> None:
    """Make the decoupled production mount contract explicit, without YAML merge tags."""
    required = ("DATA_PATH", "CONTENT_HOST_PATH", "MEDIA_HOST_PATH")
    if not all(os.environ.get(name) for name in required):
        return
    config["name"] = "ragpincheng-prod"
    networks = config.setdefault("networks", {})
    default_network = networks.setdefault("default", {})
    default_network["name"] = "ragpincheng-prod_default"
    backend = config["services"]["backend"]
    volumes = [
        {"type": "bind", "source": os.environ["DATA_PATH"], "target": "/app/data"},
        {"type": "bind", "source": os.environ["CONTENT_HOST_PATH"], "target": "/app/content"},
        {
            "type": "tmpfs",
            "target": "/app/docs",
            "read_only": True,
            "tmpfs": {"size": 1048576, "mode": 365},
        },
        {"type": "bind", "source": os.environ["MEDIA_HOST_PATH"], "target": "/app/media"},
    ]
    external_root = os.environ.get("EXTERNAL_SOURCES_HOST_PATH")
    if external_root:
        volumes.append({"type": "bind", "source": external_root, "target": "/app/external-sources", "read_only": True})
    else:
        existing = next((v for v in backend.get("volumes", []) if v.get("target") == "/app/external-sources"), None)
        if existing is not None:
            volumes.append({**existing, "read_only": True})
    backend["volumes"] = volumes


def main() -> int:
    config = json.load(sys.stdin)
    sanitized, removed = sanitize(config)
    env_file = os.environ.get("COMPOSE_ENV_FILE")
    if env_file:
        backend = sanitized["services"]["backend"]
        backend["env_file"] = [env_file]
    apply_production_volumes(sanitized)
    json.dump(sanitized, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write("\n")
    print(f"SOURCE_DECOUPLED_OVERRIDE status=sanitized removed_docs_mounts={removed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
