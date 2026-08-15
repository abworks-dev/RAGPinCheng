import copy
import json
import os
import subprocess
import sys

import pytest

from scripts.sanitize_source_decoupled_override import sanitize


def test_sanitize_removes_only_docs_mounts():
    config = {
        "services": {
            "backend": {
                "environment": {"KEEP": "value"},
                "volumes": [
                    {"type": "bind", "source": "/state", "target": "/app/data"},
                    {"type": "bind", "source": "/legacy", "target": "/app/docs"},
                    {"type": "bind", "source": "/media", "target": "/app/media"},
                ],
            },
            "qdrant": {"image": "qdrant:test"},
        }
    }
    original = copy.deepcopy(config)

    sanitized, removed = sanitize(config)

    assert removed == 1
    assert sanitized["services"]["backend"]["volumes"] == [
        original["services"]["backend"]["volumes"][0],
        original["services"]["backend"]["volumes"][2],
    ]
    assert sanitized["services"]["backend"]["environment"] == {"KEEP": "value"}
    assert sanitized["services"]["qdrant"] == {"image": "qdrant:test"}


def test_sanitize_is_idempotent_when_docs_mount_is_absent():
    config = {"services": {"backend": {"volumes": []}}}
    sanitized, removed = sanitize(config)
    assert removed == 0
    assert sanitized == config


def test_cli_replaces_relative_env_file_with_production_path():
    config = {
        "services": {
            "backend": {
                "env_file": ["../.env"],
                "volumes": [],
            }
        }
    }
    result = subprocess.run(
        [sys.executable, "scripts/sanitize_source_decoupled_override.py"],
        input=json.dumps(config),
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "COMPOSE_ENV_FILE": "/data/secrets/prod.env"},
    )
    assert json.loads(result.stdout)["services"]["backend"]["env_file"] == [
        "/data/secrets/prod.env"
    ]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"services": {}},
        {"services": {"backend": {"volumes": "invalid"}}},
        {"services": {"backend": {"volumes": ["/host:/app/docs"]}}},
    ],
)
def test_sanitize_rejects_non_normalized_contracts(config):
    with pytest.raises(ValueError):
        sanitize(config)
