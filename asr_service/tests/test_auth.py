from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asr_service.app import create_app
from asr_service.config import AsrServiceSettings


def settings(tmp_path: Path) -> AsrServiceSettings:
    return AsrServiceSettings(
        True,
        "secret-token",
        "127.0.0.1",
        8200,
        tmp_path,
        1024,
        1024,
        8,
        1000,
        3,
        "",
        "",
    )


def test_health_is_minimal_and_v1_routes_require_bearer(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "api_version": "asr-service/1"}
    assert client.get("/v1/capabilities").status_code == 401
    assert client.get(
        "/v1/capabilities?token=secret-token"
    ).status_code == 401
    assert client.get(
        "/v1/capabilities", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert "secret-token" not in response.text
