from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.asr_service.app import create_app
from services.asr_service.config import AsrServiceSettings
from services.asr_service.engine_protocol import (
    FASTER_WHISPER_SERVICE_CONFIG,
    SENSEVOICE_SERVICE_CONFIG,
    ServiceEngineCapabilities,
)
from services.asr_service.engine_registry import EngineRegistration, EngineRegistry
from services.asr_service.engines.fake import FakeEngine
from services.asr_service.model_cache import ModelCacheStatus
from services.asr_service.scheduler import BgePriorityDecision, FixedBgePriorityProbe, Scheduler
from services.asr_service.storage import LocalJobRepository
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
        EngineRegistry(
            (
                EngineRegistration(
                    FakeEngine(
                        provider_key="faster-whisper",
                        service_profile_id="faster-whisper-large-v3-turbo-v1",
                    ),
                    FASTER_WHISPER_SERVICE_CONFIG,
                ),
                EngineRegistration(FakeEngine(), SENSEVOICE_SERVICE_CONFIG),
            )
        ),
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


def test_authenticated_diagnostics_exposes_only_operational_state(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        assert client.get("/v1/diagnostics").status_code == 401
        response = client.get("/v1/diagnostics", headers=headers())
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "queue_depth": 0,
        "queue_limit": 2,
        "oom_latched": False,
        "consecutive_failures": 0,
        "failure_limit": 3,
        "pause_reason": None,
        "profiles": [
            {
                "service_profile_id": "faster-whisper-large-v3-turbo-v1",
                "available": True,
                "unavailable_reason_code": None,
            },
            {
                "service_profile_id": "funasr-sensevoice-small-v1",
                "available": True,
                "unavailable_reason_code": None,
            },
        ],
    }


def test_capabilities_expose_each_available_registered_profile(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        payload = client.get("/v1/capabilities", headers=headers()).json()
    assert payload["service_profiles"] == [
        "faster-whisper-large-v3-turbo-v1",
        "funasr-sensevoice-small-v1",
    ]


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


def test_enabled_default_wiring_accepts_qwen_only_model_cache(tmp_path, monkeypatch):
    qwen_asr_path = (tmp_path / "models" / "qwen-asr").resolve()
    qwen_aligner_path = (tmp_path / "models" / "qwen-aligner").resolve()
    config = replace(
        settings(tmp_path),
        model_cache_root=None,
        model_manifest_path=None,
        qwen3_asr_model_cache_root=tmp_path / "models",
        qwen3_asr_model_manifest_path=tmp_path / "qwen-asr.json",
        qwen3_aligner_model_cache_root=tmp_path / "models",
        qwen3_aligner_model_manifest_path=tmp_path / "qwen-aligner.json",
        qwen3_language_policy="auto-zh-en",
        qwen3_timing_diagnostics=True,
    )
    monkeypatch.setattr(
        "services.asr_service.app.validate_qwen3_asr_cache",
        lambda *_args: ModelCacheStatus(True, "available", qwen_asr_path),
    )
    monkeypatch.setattr(
        "services.asr_service.app.validate_qwen3_aligner_cache",
        lambda *_args: ModelCacheStatus(True, "available", qwen_aligner_path),
    )
    monkeypatch.setattr(
        "services.asr_service.app.Qwen3AsrEngine.capabilities",
        lambda self: ServiceEngineCapabilities(
            self.provider_key, self.service_profile_id, True
        ),
    )

    app = create_app(config)
    assert app.state.asr_scheduler.engines.available_profile_ids() == (
        "qwen3-asr-06b-aligner-v1",
    )
    registration = app.state.asr_scheduler.engines.resolve(
        "qwen3-asr-06b-aligner-v1"
    )
    assert registration is not None
    assert registration.engine.language_policy == "auto-zh-en"
    assert registration.engine.timing_diagnostics is True


def test_enabled_default_wiring_does_not_require_optional_candidate_caches(
    tmp_path, monkeypatch
):
    model_path = (tmp_path / "models" / "sensevoice").resolve()
    monkeypatch.setattr(
        "services.asr_service.app.validate_sensevoice_cache",
        lambda *_args: ModelCacheStatus(True, "available", model_path),
    )
    monkeypatch.setattr(
        "services.asr_service.app.validate_faster_whisper_cache",
        lambda *_args: ModelCacheStatus(False, "model-cache-unconfigured"),
    )
    monkeypatch.setattr(
        "services.asr_service.app.FunAsrSenseVoiceEngine.capabilities",
        lambda self: ServiceEngineCapabilities(
            self.provider_key, self.service_profile_id, True
        ),
    )
    app = create_app(settings(tmp_path))
    registrations = app.state.asr_scheduler.engines.registrations
    assert tuple(item.config.service_profile_id for item in registrations) == (
        "faster-whisper-large-v3-turbo-v1",
        "funasr-sensevoice-small-v1",
        "qwen3-asr-06b-aligner-v1",
        "whisperx-large-v3-zh-align-v1",
    )
