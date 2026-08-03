from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from asr_service.storage import LocalJobRepository
from src.transcription.asr_service_contract import (
    ASR_API_VERSION,
    ASR_JOB_SCHEMA_VERSION,
    CreateJobRequest,
    ServiceJob,
    ServiceJobState,
)
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderCandidate
from src.transcription.runtime_ports import InputPart
from src.transcription.types import (
    ContractValidationError,
    TimeUnit,
    TranscriptionInputRef,
    canonical_json_bytes,
)

MEDIA_ID = "11111111-1111-4111-8111-111111111111"


def create_repo(tmp_path, data=b"abc", *, request_id="1" * 64):
    ref = TranscriptionInputRef(
        MEDIA_ID, "audio", hashlib.sha256(data).hexdigest(), len(data), 1000
    )
    request = CreateJobRequest(
        ASR_API_VERSION,
        request_id,
        "funasr-sensevoice",
        "funasr-sensevoice-small-v1",
        "2" * 64,
        ref,
    )
    repo = LocalJobRepository(tmp_path, 1024, max_part_bytes=2)
    return repo, request, repo.create(request)


def upload_all(repo, job_id, data=b"abc"):
    for number, offset in enumerate(range(0, len(data), 2)):
        content = data[offset : offset + 2]
        repo.upload(
            job_id,
            InputPart(number, offset, content, hashlib.sha256(content).hexdigest()),
        )


def test_job_identity_and_upload_are_idempotent(tmp_path):
    repo, request, job = create_repo(tmp_path)
    assert repo.create(request).job_id == job.job_id
    upload_all(repo, job.job_id)
    first = InputPart(0, 0, b"ab", hashlib.sha256(b"ab").hexdigest())
    repo.upload(job.job_id, first)
    queued = repo.complete_upload(job.job_id)
    assert queued.state is ServiceJobState.queued
    assert repo.complete_upload(job.job_id) == queued
    assert repo.content(job.job_id) == b"abc"


def test_same_identity_different_metadata_conflicts(tmp_path):
    repo, request, _job = create_repo(tmp_path)
    with pytest.raises(ContractValidationError, match="identity_conflict"):
        repo.create(replace(request, execution_fingerprint="3" * 64))


@pytest.mark.parametrize("mutation", ["gap", "offset", "part_conflict", "too_large"])
def test_upload_rejects_gaps_conflicts_and_limits(tmp_path, mutation):
    repo, _request, job = create_repo(tmp_path)
    good = InputPart(0, 0, b"ab", hashlib.sha256(b"ab").hexdigest())
    if mutation == "gap":
        part = InputPart(1, 0, b"c", hashlib.sha256(b"c").hexdigest())
    elif mutation == "offset":
        part = InputPart(0, 1, b"ab", hashlib.sha256(b"ab").hexdigest())
    elif mutation == "too_large":
        content = b"abc"
        part = InputPart(0, 0, content, hashlib.sha256(content).hexdigest())
    else:
        repo.upload(job.job_id, good)
        content = b"ac"
        part = InputPart(0, 0, content, hashlib.sha256(content).hexdigest())
    with pytest.raises(ContractValidationError):
        repo.upload(job.job_id, part)


def test_complete_checks_full_hash_and_completed_upload_cannot_be_overwritten(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    upload_all(repo, job.job_id)
    part_path = tmp_path / job.job_id / "parts" / "00000001.part"
    part_path.write_bytes(b"x")
    with pytest.raises(ContractValidationError, match="input_hash_mismatch"):
        repo.complete_upload(job.job_id)
    part_path.write_bytes(b"c")
    repo.complete_upload(job.job_id)
    with pytest.raises(ContractValidationError, match="upload_already_complete"):
        repo.upload(job.job_id, InputPart(0, 0, b"ab", hashlib.sha256(b"ab").hexdigest()))


def test_crash_windows_resume_orphan_part_and_completed_manifest(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    parts = tmp_path / job.job_id / "parts"
    parts.mkdir()
    (parts / "00000000.part").write_bytes(b"ab")
    first = InputPart(0, 0, b"ab", hashlib.sha256(b"ab").hexdigest())
    assert repo.upload(job.job_id, first).state is ServiceJobState.uploading
    repo.upload(
        job.job_id,
        InputPart(1, 2, b"c", hashlib.sha256(b"c").hexdigest()),
    )
    queued = repo.complete_upload(job.job_id)
    uploading = ServiceJob(
        ASR_JOB_SCHEMA_VERSION,
        queued.job_id,
        queued.client_request_id,
        ServiceJobState.uploading,
        0,
        queued.total_ms,
    )
    (tmp_path / job.job_id / "job.json").write_bytes(
        canonical_json_bytes(uploading.to_json_dict())
    )
    assert repo.complete_upload(job.job_id).state is ServiceJobState.queued


def test_repository_rejects_state_rollback(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    upload_all(repo, job.job_id)
    queued = repo.complete_upload(job.job_id)
    with pytest.raises(ContractValidationError, match="invalid_service_transition"):
        repo.save(
            type(queued)(
                ASR_JOB_SCHEMA_VERSION,
                queued.job_id,
                queued.client_request_id,
                ServiceJobState.uploading,
                0,
                queued.total_ms,
            )
        )


def test_checkpoint_regression_and_result_overwrite_fail_closed(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    upload_all(repo, job.job_id)
    repo.complete_upload(job.job_id)
    segment = CandidateSegment(0, "0", "1000", TimeUnit.milliseconds, "测试")
    checkpoint = repo.new_checkpoint(
        job.job_id, next_chunk_index=1, processed_ms=1000, partial_segments=(segment,)
    )
    repo.save_checkpoint(checkpoint)
    with pytest.raises(ContractValidationError, match="checkpoint_regression"):
        repo.save_checkpoint(replace(checkpoint, processed_ms=999))
    candidate = ProviderCandidate("funasr-sensevoice", "zh-CN", 1000, (segment,))
    repo.save_result(job.job_id, candidate)
    repo.save_result(job.job_id, candidate)
    other = replace(candidate, language="und")
    with pytest.raises(ContractValidationError, match="result_conflict"):
        repo.save_result(job.job_id, other)


def test_recovery_pauses_running_and_does_not_revive_terminal(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    upload_all(repo, job.job_id)
    queued = repo.complete_upload(job.job_id)
    running = queued.transition(ServiceJobState.running)
    repo.save(running)
    recovered = repo.recover()
    assert recovered[0].state is ServiceJobState.paused

    cancelled = recovered[0].transition(ServiceJobState.cancelled)
    repo.save(cancelled)
    assert repo.recover()[0].state is ServiceJobState.cancelled


def test_symlink_and_corrupt_json_are_rejected(tmp_path):
    repo, _request, job = create_repo(tmp_path)
    (tmp_path / job.job_id / "job.json").write_text("{", encoding="utf-8")
    with pytest.raises(ContractValidationError, match="storage_corrupt"):
        repo.get(job.job_id)
