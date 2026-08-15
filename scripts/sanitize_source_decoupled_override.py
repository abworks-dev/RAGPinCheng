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


def main() -> int:
    config = json.load(sys.stdin)
    sanitized, removed = sanitize(config)
    env_file = os.environ.get("COMPOSE_ENV_FILE")
    if env_file:
        backend = sanitized["services"]["backend"]
        backend["env_file"] = [env_file]
    json.dump(sanitized, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write("\n")
    print(f"SOURCE_DECOUPLED_OVERRIDE status=sanitized removed_docs_mounts={removed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
