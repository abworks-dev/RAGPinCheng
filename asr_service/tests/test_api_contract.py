from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asr_service.app import create_app
from asr_service.config import AsrServiceSettings
from asr_service.engine_protocol import SENSEVOICE_SERVICE_CONFIG
from asr_service.engine_registry import EngineRegistration, EngineRegistry
from asr_service.engines.fake import FakeEngine
from asr_service.scheduler import BgePriorityDecision, FixedBgePriorityProbe, Scheduler
from asr_service.storage import LocalJobRepository
from src.transcription.asr_service_contract import ASR_API_VERSION, CreateJobRequest
from src.transcription.types import TranscriptionInputRef

TOKEN = "service-secret"


def settings(tmp_path: Path, *, enabled=True) -> AsrServiceSettings:
    return AsrServiceSettings(
        enabled,
        TOKEN,
        "127.0.0.1",
        8200,
        tmp_path,
        1024,
        2,
        2,
        1000,
        3,
        "http://127.0.0.1:8100/v1/activity",
        "gpu-probe-token",
        tmp_path / "models",
        tmp_path / "model-manifest.json",
    )


def headers(**extra):
    return {"Authorization": f"Bearer {TOKEN}", **extra}


def app_for(tmp_path: Path):
    config = settings(tmp_path)
    repo = LocalJobRepository(
        config.spool_root, config.max_input_bytes, config.max_upload_part_bytes
    )
    scheduler = Scheduler(
        repo,
        EngineRegistry((EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG),)),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        queue_limit=config.max_queue_length,
        failure_limit=config.consecutive_failure_limit,
        enabled=True,
    )
    return create_app(config, scheduler)


def request(data=b"abc"):
    ref = TranscriptionInputRef(
        "11111111-1111-4111-8111-111111111111",
        "audio",
        hashlib.sha256(data).hexdigest(),
        len(data),
        1000,
    )
    return CreateJobRequest(
        ASR_API_VERSION,
        "1" * 64,
        "funasr-sensevoice",
        "funasr-sensevoice-small-v1",
        "2" * 64,
        ref,
    )


def test_api_create_upload_complete_start_poll_result_sequence(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/v1/jobs", headers=headers(), json=request().to_json_dict()
        )
        assert created.status_code == 200
        job_id = created.json()["job_id"]
        for number, (offset, content) in enumerate(((0, b"ab"), (2, b"c"))):
            uploaded = client.put(
                f"/v1/jobs/{job_id}/input/{number}",
                headers=headers(
                    **{
                        "X-Offset-Bytes": str(offset),
                        "X-Content-Sha256": hashlib.sha256(content).hexdigest(),
                        "Content-Type": "application/octet-stream",
                    }
                ),
                content=content,
            )
            assert uploaded.status_code == 200
        assert client.post(
            f"/v1/jobs/{job_id}/input/complete", headers=headers()
        ).json()["state"] == "queued"
        assert client.post(
            f"/v1/jobs/{job_id}/start", headers=headers()
        ).status_code == 200

        deadline = time.monotonic() + 1
        state = "queued"
        while state != "succeeded" and time.monotonic() < deadline:
            state = client.get(
                f"/v1/jobs/{job_id}", headers=headers()
            ).json()["state"]
            time.sleep(0.01)
        assert state == "succeeded"
        result = client.get(f"/v1/jobs/{job_id}/result", headers=headers())
        assert result.status_code == 200
        assert result.json()["result_kind"] == "candidate"


def test_api_errors_are_finite_and_do_not_leak_paths_or_body(tmp_path):
    client = TestClient(app_for(tmp_path))
    missing = "22222222-2222-4222-8222-222222222222"
    response = client.get(f"/v1/jobs/{missing}", headers=headers())
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "storage_not_found"}}
    assert str(tmp_path) not in response.text

    response = client.post("/v1/jobs", headers=headers(), content=b"private-body")
    assert response.status_code == 422
    assert "private-body" not in response.text

    created = client.post(
        "/v1/jobs", headers=headers(), json=request().to_json_dict()
    )
    job_id = created.json()["job_id"]
    response = client.put(
        f"/v1/jobs/{job_id}/input/0",
        headers=headers(
            **{
                "X-Offset-Bytes": "0",
                "X-Content-Sha256": hashlib.sha256(b"abc").hexdigest(),
                "Content-Type": "text/plain",
            }
        ),
        content=b"abc",
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_request"}}


def test_disabled_service_exposes_no_profiles_and_requires_no_token(tmp_path):
    disabled = settings(tmp_path, enabled=False)
    disabled = AsrServiceSettings(
        disabled.enabled,
        "",
        disabled.host,
        disabled.port,
        disabled.spool_root,
        disabled.max_input_bytes,
        disabled.max_upload_part_bytes,
        disabled.max_queue_length,
        disabled.chunk_duration_ms,
        disabled.consecutive_failure_limit,
        disabled.bge_priority_probe_url,
        disabled.bge_priority_probe_token,
    )
    client = TestClient(create_app(disabled))
    assert client.get("/health").json()["status"] == "disabled"


def test_enabled_default_wiring_rejects_unverified_model_cache(tmp_path):
    with pytest.raises(RuntimeError, match="ASR model cache unavailable"):
        create_app(settings(tmp_path))
