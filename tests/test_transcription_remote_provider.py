from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest
import httpx

from src.transcription.asr_service_contract import (
    ASR_API_VERSION,
    ASR_JOB_SCHEMA_VERSION,
    ASR_RESULT_SCHEMA_VERSION,
    ServiceCapabilities,
    ServiceFailureCode,
    ServiceJob,
    ServiceJobState,
    ServiceResult,
)
from src.transcription.candidate import CandidateSegment
from src.transcription.pipeline import execute_transcription
from src.transcription.profile import ProfileSnapshot, TranscriptionExecutionConfig
from src.transcription.profile_catalog import build_phase3_profile_catalog
from src.transcription.provider_protocol import (
    ProviderCandidate,
    ProviderErrorCode,
    ProviderFailure,
)
from src.transcription.provider_registry import ProviderRuntimePorts
from src.transcription.remote_provider import (
    AsrServiceClientError,
    HttpxAsrServiceClient,
    RemoteAsrProvider,
    _client_failure,
    compute_client_request_id,
)
from src.transcription.runtime_ports import (
    InputPart,
    MemoryInputSource,
    NeverCancel,
    NoOpProgressSink,
)
from src.transcription.types import TimeUnit, TranscriptionInputRef

JOB_ID = "11111111-1111-4111-8111-111111111111"
PROVIDER_KEY = "funasr-sensevoice"
SERVICE_PROFILE_ID = "funasr-sensevoice-small-v1"


@dataclass
class FakeClient:
    mode: str = "success"
    calls: list[str] = field(default_factory=list)
    request_id: str = "1" * 64
    job: ServiceJob | None = None

    def capabilities(self) -> ServiceCapabilities:
        self.calls.append("capabilities")
        if self.mode == "capabilities_error":
            raise AsrServiceClientError(503, "http_error")
        profiles = () if self.mode == "profile_mismatch" else (SERVICE_PROFILE_ID,)
        return ServiceCapabilities(ASR_API_VERSION, profiles, 16 * 1024**2, 32 * 1024**2)

    def create_job(self, request):
        self.calls.append("create")
        if self.mode.startswith("http_"):
            status = int(self.mode.split("_", 1)[1])
            raise AsrServiceClientError(status, "http_error")
        self.request_id = request.client_request_id
        self.job = ServiceJob(
            ASR_JOB_SCHEMA_VERSION,
            JOB_ID,
            request.client_request_id,
            ServiceJobState.created,
            0,
            request.input_ref.duration_ms,
        )
        return self.job

    def upload_part(self, job_id, part):
        self.calls.append(f"upload:{part.part_number}")
        self.job = self.job.transition(ServiceJobState.uploading)
        return self.job

    def complete_input(self, job_id):
        self.calls.append("complete")
        self.job = self.job.transition(ServiceJobState.queued)
        return self.job

    def start_job(self, job_id):
        self.calls.append("start")
        if self.mode == "oom":
            self.job = self.job.transition(
                ServiceJobState.running
            ).transition(ServiceJobState.failed, failure_code=ServiceFailureCode.provider_oom)
        else:
            self.job = self.job.transition(ServiceJobState.running)
        return self.job

    def get_job(self, job_id):
        self.calls.append("poll")
        if self.job.state in {
            ServiceJobState.failed,
            ServiceJobState.cancelled,
            ServiceJobState.succeeded,
        }:
            return self.job
        if self.mode == "timeout":
            return self.job
        self.job = self.job.transition(
            ServiceJobState.succeeded,
            processed_ms=self.job.total_ms,
        )
        return self.job

    def cancel_job(self, job_id):
        self.calls.append("cancel")
        return self.job

    def get_result(self, job_id):
        self.calls.append("result")
        if self.mode == "invalid_result":
            from src.transcription.types import ContractValidationError

            raise ContractValidationError("unknown_field", "result")
        key = "other-provider" if self.mode == "provider_mismatch" else PROVIDER_KEY
        candidate = ProviderCandidate(
            key,
            "zh-CN",
            self.job.total_ms,
            (
                CandidateSegment(
                    0, "0", str(self.job.total_ms), TimeUnit.milliseconds, "测试"
                ),
            ),
        )
        job = "22222222-2222-4222-8222-222222222222" if self.mode == "job_mismatch" else job_id
        return ServiceResult(ASR_RESULT_SCHEMA_VERSION, job, candidate)


@dataclass(frozen=True)
class SequenceCancel:
    values: tuple[bool, ...]
    _index: list[int] = field(default_factory=lambda: [0], compare=False)

    def is_cancel_requested(self) -> bool:
        index = self._index[0]
        self._index[0] += 1
        return self.values[min(index, len(self.values) - 1)]


def bundle(data: bytes = b"x", *, timeout_ms: int = 1000):
    ref = TranscriptionInputRef(
        JOB_ID,
        "audio",
        hashlib.sha256(data).hexdigest(),
        len(data),
        1000,
    )
    profile = build_phase3_profile_catalog()[0].profile
    execution = TranscriptionExecutionConfig.create(
        profile, ref, language="zh-CN", timeout_ms=timeout_ms
    )
    return ref, execution, ProfileSnapshot.create(profile, execution)


def run(client: FakeClient, *, data: bytes = b"x", input_source=None, cancellation=None, monotonic=None):
    ref, execution, snapshot = bundle(data)
    provider = RemoteAsrProvider(
        client,
        ProviderRuntimePorts(
            JOB_ID,
            input_source or MemoryInputSource(data),
            NoOpProgressSink(),
            cancellation or NeverCancel(),
        ),
        monotonic=monotonic or (lambda: 0.0),
        sleep=lambda _seconds: None,
    )
    return execute_transcription(
        provider, ref, execution, profile_snapshot=snapshot
    )


def test_remote_provider_uses_unique_bounded_sequence_and_pipeline_normalizer():
    client = FakeClient()
    result = run(client)
    assert result.__class__.__name__ == "CanonicalTranscript"
    assert client.calls == [
        "capabilities", "create", "upload:0", "complete", "start", "poll", "result"
    ]


def test_same_application_job_network_retry_has_stable_service_request_id():
    ref, execution, _snapshot = bundle()
    first = compute_client_request_id(JOB_ID, ref, execution)
    second = compute_client_request_id(JOB_ID, ref, execution)
    assert first == second


def test_new_application_retry_job_for_same_media_has_new_service_request_id():
    ref, execution, _snapshot = bundle()
    first = compute_client_request_id(JOB_ID, ref, execution)
    second = compute_client_request_id(
        "22222222-2222-4222-8222-222222222222", ref, execution
    )
    assert first != second


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("http_401", ProviderErrorCode.permanent_provider_error),
        ("http_403", ProviderErrorCode.permanent_provider_error),
        ("http_409", ProviderErrorCode.service_contract_mismatch),
        ("http_413", ProviderErrorCode.input_too_large),
        ("http_503", ProviderErrorCode.provider_unavailable),
        ("capabilities_error", ProviderErrorCode.provider_unavailable),
        ("profile_mismatch", ProviderErrorCode.service_contract_mismatch),
        ("oom", ProviderErrorCode.provider_oom),
        ("invalid_result", ProviderErrorCode.invalid_provider_output),
        ("job_mismatch", ProviderErrorCode.service_contract_mismatch),
        ("provider_mismatch", ProviderErrorCode.service_contract_mismatch),
    ],
)
def test_remote_failure_mapping_is_closed(mode, expected):
    result = run(FakeClient(mode))
    assert type(result) is ProviderFailure
    assert result.error_code is expected


def test_timeout_best_effort_cancels_before_returning_failure():
    times = iter((0.0, 0.0, 0.0, 2.0))
    client = FakeClient("timeout")
    result = run(client, monotonic=lambda: next(times))
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_timeout
    assert result.timeout_ms == 1000
    assert client.calls[-1] == "cancel"


def test_cancellation_never_produces_candidate():
    client = FakeClient()
    result = run(client, cancellation=SequenceCancel((False, False, True)))
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.provider_cancelled
    assert client.calls[-1] == "cancel"


def test_input_source_full_hash_mismatch_is_input_unavailable():
    class WrongSource:
        def iter_parts(self, input_ref, part_size_bytes):
            content = b"y"
            yield InputPart(0, 0, content, hashlib.sha256(content).hexdigest())

    client = FakeClient()
    result = run(client, input_source=WrongSource())
    assert type(result) is ProviderFailure
    assert result.error_code is ProviderErrorCode.input_unavailable
    assert "complete" not in client.calls


def test_http_client_uses_bearer_and_strict_json_without_network():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(
            200,
            json=ServiceCapabilities(
                ASR_API_VERSION, (SERVICE_PROFILE_ID,), 1024, 2048
            ).to_json_dict(),
        )

    client = HttpxAsrServiceClient(
        "https://asr.invalid", "secret", transport=httpx.MockTransport(handler)
    )
    assert client.capabilities().service_profiles == (SERVICE_PROFILE_ID,)
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/v1/capabilities"
    assert captured[0].headers["authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [(503, b"{}", "http_error"), (200, b"not-json", "invalid_json")],
)
def test_http_client_errors_do_not_expose_response_body(status, body, reason):
    transport = httpx.MockTransport(lambda _request: httpx.Response(status, content=body))
    client = HttpxAsrServiceClient(
        "https://asr.invalid", "secret", transport=transport
    )
    with pytest.raises(AsrServiceClientError) as caught:
        client.capabilities()
    assert caught.value.reason == reason
    assert body.decode(errors="ignore") not in str(caught.value)


def test_http_client_safely_preserves_known_shape_detail_code():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            409, json={"detail": {"code": "identity_conflict", "secret": "hidden"}}
        )
    )
    client = HttpxAsrServiceClient(
        "https://asr.invalid", "secret", transport=transport
    )
    with pytest.raises(AsrServiceClientError) as caught:
        client.capabilities()
    assert caught.value.detail_code == "identity_conflict"
    assert "hidden" not in str(caught.value)


def test_identity_conflict_is_not_reported_as_contract_mismatch():
    result = _client_failure(
        PROVIDER_KEY,
        AsrServiceClientError(409, "http_error", "identity_conflict"),
        1000,
    )
    assert result.error_code is ProviderErrorCode.service_request_identity_conflict
