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


def test_production_mount_contract_is_explicit(monkeypatch):
    from scripts.sanitize_source_decoupled_override import apply_production_volumes

    config = {"services": {"backend": {"volumes": [
        {"target": "/app/docs"},
        {"type": "bind", "source": "/data/external-sources", "target": "/app/external-sources"},
    ]}}}
    monkeypatch.setenv("DATA_PATH", "/data/app")
    monkeypatch.setenv("CONTENT_HOST_PATH", "/data/content")
    monkeypatch.setenv("MEDIA_HOST_PATH", "/data/media")
    apply_production_volumes(config)
    volumes = config["services"]["backend"]["volumes"]
    assert [item["target"] for item in volumes] == [
        "/app/data", "/app/content", "/app/docs", "/app/media", "/app/external-sources"
    ]
    assert volumes[2]["type"] == "tmpfs"
    assert volumes[2]["read_only"] is True
    assert volumes[4]["source"] == "/data/external-sources"
    assert volumes[4]["read_only"] is True
    assert config["name"] == "ragpincheng-prod"
    assert config["networks"]["default"]["name"] == "ragpincheng-prod_default"


def test_production_mount_contract_includes_explicit_external_sources(monkeypatch):
    from scripts.sanitize_source_decoupled_override import apply_production_volumes
    config = {"services": {"backend": {"volumes": []}}}
    for key, value in {"DATA_PATH": "/data/app", "CONTENT_HOST_PATH": "/data/content", "MEDIA_HOST_PATH": "/data/media", "EXTERNAL_SOURCES_HOST_PATH": "/data/external-sources"}.items():
        monkeypatch.setenv(key, value)
    apply_production_volumes(config)
    assert config["services"]["backend"]["volumes"][-1] == {"type": "bind", "source": "/data/external-sources", "target": "/app/external-sources", "read_only": True}


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
