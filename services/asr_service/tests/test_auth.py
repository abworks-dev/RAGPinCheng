from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from services.asr_service.app import create_app
from services.asr_service.config import AsrServiceSettings
from services.asr_service.engine_protocol import SENSEVOICE_SERVICE_CONFIG
from services.asr_service.engine_registry import EngineRegistration, EngineRegistry
from services.asr_service.engines.fake import FakeEngine
from services.asr_service.scheduler import BgePriorityDecision, FixedBgePriorityProbe, Scheduler
from services.asr_service.storage import LocalJobRepository


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
        "http://127.0.0.1:8100/v1/activity",
        "gpu-probe-token",
        tmp_path / "models",
        tmp_path / "model-manifest.json",
    )


def test_health_is_minimal_and_v1_routes_require_bearer(tmp_path):
    config = settings(tmp_path)
    repo = LocalJobRepository(tmp_path, 1024, 1024)
    scheduler = Scheduler(
        repo,
        EngineRegistry((EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG),)),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        enabled=True,
    )
    client = TestClient(create_app(config, scheduler))
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
