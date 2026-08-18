from __future__ import annotations

from dataclasses import replace

import pytest

from src.transcription.asr_service_contract import (
    ASR_API_VERSION,
    ASR_JOB_SCHEMA_VERSION,
    ASR_PROFILE_IDENTITIES_SCHEMA_VERSION,
    ASR_RESULT_SCHEMA_VERSION,
    ASR_UPLOAD_SCHEMA_VERSION,
    CreateJobRequest,
    ServiceCapabilities,
    ServiceJob,
    ServiceJobState,
    ServicePauseReason,
    ServiceProfileIdentities,
    ServiceProfileIdentity,
    ServiceResult,
    UploadManifest,
    UploadPartRecord,
    validate_service_transition,
)
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderCandidate
from src.transcription.types import (
    ContractValidationError,
    TimeUnit,
    TranscriptionInputRef,
)

JOB_ID = "11111111-1111-4111-8111-111111111111"
REF = TranscriptionInputRef(JOB_ID, "audio", "0" * 64, 1, 1000)


def request() -> CreateJobRequest:
    return CreateJobRequest(
        ASR_API_VERSION,
        "1" * 64,
        "funasr-sensevoice",
        "funasr-sensevoice-small-v1",
        "2" * 64,
        REF,
    )


def candidate() -> ProviderCandidate:
    return ProviderCandidate(
        "funasr-sensevoice",
        "zh-CN",
        1000,
        (CandidateSegment(0, "0", "1000", TimeUnit.milliseconds, "测试"),),
    )


@pytest.mark.parametrize(
    "value",
    [
        lambda: request(),
        lambda: ServiceCapabilities(
            ASR_API_VERSION, ("funasr-sensevoice-small-v1",), 1024, 2048
        ),
        lambda: ServiceProfileIdentities(
            ASR_PROFILE_IDENTITIES_SCHEMA_VERSION,
            (
                ServiceProfileIdentity(
                    "funasr-sensevoice-small-v1",
                    "funasr-sensevoice",
                    "0" * 64,
                    None,
                    "not-required",
                ),
            ),
        ),
        lambda: UploadManifest(
            ASR_UPLOAD_SCHEMA_VERSION,
            JOB_ID,
            "0" * 64,
            1,
            True,
            (UploadPartRecord(0, 0, 1, "0" * 64),),
        ),
        lambda: ServiceJob(
            ASR_JOB_SCHEMA_VERSION,
            JOB_ID,
            "1" * 64,
            ServiceJobState.created,
            0,
            1000,
        ),
        lambda: ServiceResult(ASR_RESULT_SCHEMA_VERSION, JOB_ID, candidate()),
    ],
)
def test_service_dto_roundtrip_and_unknown_field_rejection(value):
    item = value()
    rebuilt = type(item).from_json_dict(item.to_json_dict())
    assert rebuilt == item
    payload = item.to_json_dict()
    payload["warnings"] = []
    with pytest.raises(ContractValidationError):
        type(item).from_json_dict(payload)


def test_candidate_and_service_result_reject_warnings_at_nested_boundary():
    payload = ServiceResult(ASR_RESULT_SCHEMA_VERSION, JOB_ID, candidate()).to_json_dict()
    payload["result"]["warnings"] = []
    with pytest.raises(ContractValidationError):
        ServiceResult.from_json_dict(payload)


def test_every_service_state_transition_matches_exact_table():
    allowed = {
        ServiceJobState.created: {ServiceJobState.uploading},
        ServiceJobState.uploading: {ServiceJobState.uploading, ServiceJobState.queued},
        ServiceJobState.queued: {
            ServiceJobState.running,
            ServiceJobState.paused,
            ServiceJobState.cancelled,
        },
        ServiceJobState.running: {
            ServiceJobState.running,
            ServiceJobState.paused,
            ServiceJobState.succeeded,
            ServiceJobState.failed,
            ServiceJobState.cancelled,
        },
        ServiceJobState.paused: {
            ServiceJobState.queued,
            ServiceJobState.cancelled,
            ServiceJobState.failed,
        },
        ServiceJobState.succeeded: set(),
        ServiceJobState.failed: set(),
        ServiceJobState.cancelled: set(),
    }
    for current in ServiceJobState:
        for target in ServiceJobState:
            if target in allowed[current]:
                validate_service_transition(current, target)
            else:
                with pytest.raises(ContractValidationError):
                    validate_service_transition(current, target)


def test_pause_reason_is_state_qualified_and_bool_is_not_integer():
    with pytest.raises(ContractValidationError):
        ServiceJob(
            ASR_JOB_SCHEMA_VERSION,
            JOB_ID,
            "1" * 64,
            ServiceJobState.running,
            0,
            1,
            ServicePauseReason.bge_busy,
        )
    with pytest.raises(ContractValidationError):
        replace(
            ServiceJob(
                ASR_JOB_SCHEMA_VERSION,
                JOB_ID,
                "1" * 64,
                ServiceJobState.created,
                0,
                1,
            ),
            processed_ms=True,
        )


@pytest.mark.parametrize("version", ["asr-service/2", "asr-service/1.1", "1"])
def test_unknown_api_versions_are_rejected(version):
    with pytest.raises(ContractValidationError):
        replace(request(), api_version=version)
