"""Shared deterministic fixtures for Phase 1 transcription contract tests."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from api.db import connect, init_db
from api.transcription_artifacts import LocalTranscriptionArtifactStore
from api.transcription_store import SQLiteTranscriptionStore
from src.transcription.canonical import CanonicalTranscript
from src.transcription.persistence import (
    INDEX_RECEIPT_SCHEMA_VERSION,
    PublicationIndexReceipt,
)
from src.transcription.workflow import build_pending_job

from src.transcription.candidate import CandidateSegment
from src.transcription.profile import (
    FakeAlphaConfig,
    FakeBetaConfig,
    FakeGammaConfig,
    ProfileSnapshot,
    ReleasePolicy,
    TranscriptionExecutionConfig,
    TranscriptionProfileDefinition,
)
from src.transcription.provider_protocol import (
    PermanentProviderError,
    ProviderCandidate,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
    ProviderTimeoutError,
    TransientProviderError,
)
from src.transcription.types import (
    ArtifactKind,
    ArtifactReference,
    NormalizerConfig,
    ProfileAdmission,
    ProfileQualification,
    PublicationIndexStatus,
    TimeUnit,
    TranscriptionInputRef,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "transcription"
MEDIA_ID = "123e4567-e89b-12d3-a456-426614174000"
CANDIDATE_VERSION_ID = "123e4567-e89b-12d3-a456-426614174001"
INPUT_SHA256 = "a" * 64
CANONICAL_SHA256 = "b" * 64
MARKDOWN_SHA256 = "c" * 64
JOB_ID = "123e4567-e89b-12d3-a456-426614174010"
REQUEST_ID = "123e4567-e89b-12d3-a456-426614174011"
VERSION_ID = "123e4567-e89b-12d3-a456-426614174012"
INDEX_JOB_ID = "123e4567-e89b-12d3-a456-426614174013"


def load_json(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def load_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def make_input_ref(*, duration_ms: int = 70_000, media_id: str = MEDIA_ID) -> TranscriptionInputRef:
    return TranscriptionInputRef(media_id, "video", INPUT_SHA256, 4096, duration_ms)


def _config(provider_key: str):
    if provider_key == "fake-alpha":
        return FakeAlphaConfig()
    if provider_key == "fake-beta":
        return FakeBetaConfig()
    if provider_key == "fake-gamma":
        return FakeGammaConfig()
    raise AssertionError(provider_key)


def make_profile(
    provider_key: str = "fake-alpha",
    *,
    profile_id: str | None = None,
    qualification: ProfileQualification = ProfileQualification.qualification_approved,
    admission: ProfileAdmission = ProfileAdmission.enabled,
    release_policy: ReleasePolicy | None = None,
    normalizer_config: NormalizerConfig | None = None,
) -> TranscriptionProfileDefinition:
    if release_policy is None:
        release_policy = (
            ReleasePolicy(True, False, False)
            if qualification is ProfileQualification.experimental
            else ReleasePolicy(False, False, False)
        )
    return TranscriptionProfileDefinition.create(
        profile_id=profile_id or f"{provider_key}-standard",
        display_name=f"{provider_key} fixture",
        description="deterministic Phase 1 fixture",
        provider_key=provider_key,
        provider_config=_config(provider_key),
        normalizer_config=normalizer_config or NormalizerConfig(1, 200, 500),
        qualification=qualification,
        admission=admission,
        release_policy=release_policy,
    )


def make_execution_bundle(
    provider_key: str = "fake-alpha",
    *,
    duration_ms: int = 70_000,
    profile: TranscriptionProfileDefinition | None = None,
):
    input_ref = make_input_ref(duration_ms=duration_ms)
    profile = profile or make_profile(provider_key)
    execution = TranscriptionExecutionConfig.create(profile, input_ref, language="zh-CN", timeout_ms=5_000)
    snapshot = ProfileSnapshot.create(profile, execution)
    return input_ref, profile, execution, snapshot


def make_candidate(provider_key: str, *, duration_ms: int = 70_000) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key,
        "zh-CN",
        duration_ms,
        (
            CandidateSegment(0, "0", "1.500", TimeUnit.seconds, "你好，世界。", 0.9),
            CandidateSegment(1, "1.500", "3", TimeUnit.seconds, "Second line.", None),
        ),
    )


@dataclass(slots=True)
class _FakeProviderBase:
    behavior: str = "success"
    key: str = "fake-alpha"

    @property
    def provider_key(self) -> str:
        return self.key

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(self.key, ("en", "zh-CN"), ("video",), True, True, 360_000_000)

    def transcribe(self, input_ref: TranscriptionInputRef, execution: TranscriptionExecutionConfig):
        if self.behavior == "success":
            return make_candidate(self.key, duration_ms=input_ref.duration_ms)
        if self.behavior == "transient_failure":
            return ProviderFailure(
                self.key,
                ProviderErrorCode.transient_provider_error,
                ProviderFailureClassification.transient,
            )
        if self.behavior == "permanent_failure":
            return ProviderFailure(
                self.key,
                ProviderErrorCode.permanent_provider_error,
                ProviderFailureClassification.permanent,
            )
        if self.behavior == "timeout_failure":
            return ProviderFailure(
                self.key,
                ProviderErrorCode.provider_timeout,
                ProviderFailureClassification.transient,
                execution.timeout_ms,
            )
        if self.behavior == "raise_timeout":
            raise ProviderTimeoutError()
        if self.behavior == "raise_transient":
            raise TransientProviderError()
        if self.behavior == "raise_permanent":
            raise PermanentProviderError()
        if self.behavior == "raise_unknown":
            raise RuntimeError("private provider detail")
        if self.behavior == "invalid_member":
            return {"raw": object()}
        if self.behavior == "invalid_failure":
            failure = ProviderFailure(
                self.key,
                ProviderErrorCode.permanent_provider_error,
                ProviderFailureClassification.permanent,
            )
            object.__setattr__(failure, "classification", "permanent")
            return failure
        if self.behavior == "invalid_candidate":
            candidate = make_candidate(self.key, duration_ms=input_ref.duration_ms)
            object.__setattr__(candidate, "duration_ms", input_ref.duration_ms + 1)
            return candidate
        if self.behavior == "invalid_candidate_artifact":
            candidate = make_candidate(self.key, duration_ms=input_ref.duration_ms)
            artifact = ArtifactReference(
                "provider-diagnostic",
                ArtifactKind.provider_diagnostic,
                "d" * 64,
                1,
            )
            object.__setattr__(artifact, "kind", "private_debug")
            object.__setattr__(candidate, "artifact_refs", (artifact,))
            return candidate
        if self.behavior == "mutate_input":
            original = input_ref.duration_ms
            object.__setattr__(input_ref, "duration_ms", original + 1)
            return make_candidate(self.key, duration_ms=original)
        if self.behavior == "mutate_execution":
            object.__setattr__(execution, "language", "en")
            return make_candidate(self.key, duration_ms=input_ref.duration_ms)
        raise AssertionError(self.behavior)


class FakeAlphaProvider(_FakeProviderBase):
    def __init__(self, behavior: str = "success") -> None:
        super().__init__(behavior, "fake-alpha")


class FakeBetaProvider(_FakeProviderBase):
    def __init__(self, behavior: str = "success") -> None:
        super().__init__(behavior, "fake-beta")


class FakeGammaProvider(_FakeProviderBase):
    def __init__(self, behavior: str = "success") -> None:
        super().__init__(behavior, "fake-gamma")


FAKE_PROVIDER_TYPES = (FakeAlphaProvider, FakeBetaProvider, FakeGammaProvider)


def make_canonical() -> CanonicalTranscript:
    return CanonicalTranscript.from_json_dict(load_json("canonical.json"))


def make_phase2_store(tmp_path: Path) -> tuple[sqlite3.Connection, SQLiteTranscriptionStore, LocalTranscriptionArtifactStore]:
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    conn.execute(
        """INSERT INTO media_assets(
            media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
            transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            MEDIA_ID, "Fixture video", "fixture.mp4", "fixture/original.mp4", "video/mp4", 1,
            INPUT_SHA256, None, "generated", "uploaded", None, 1, 1, None,
        ),
    )
    conn.commit()
    return conn, SQLiteTranscriptionStore(conn), LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())


def make_pending_job(*, job_id: str = JOB_ID, request_id: str = REQUEST_ID, attempt: int = 1, created_at: int = 10):
    input_ref, _profile, execution, snapshot = make_execution_bundle()
    return build_pending_job(
        job_id=job_id,
        request_idempotency_key=request_id,
        attempt_number=attempt,
        input_ref=input_ref,
        execution=execution,
        snapshot=snapshot,
        created_at=created_at,
    )


@dataclass(slots=True)
class FakePublicationIndexPort:
    behavior: str = "done"
    calls: int = 0

    def index_candidate(self, request):
        self.calls += 1
        if self.behavior == "invalid":
            return {"raw": object()}
        if self.behavior == "failed":
            return PublicationIndexReceipt(
                INDEX_RECEIPT_SCHEMA_VERSION,
                request.index_job_id,
                request.transcript_version_id,
                request.candidate_version_id,
                request.canonical_sha256,
                request.markdown_sha256,
                request.target_index_id,
                PublicationIndexStatus.failed,
                "index_adapter_failed",
                "fake index adapter failed",
            )
        return PublicationIndexReceipt(
            INDEX_RECEIPT_SCHEMA_VERSION,
            request.index_job_id,
            request.transcript_version_id,
            request.candidate_version_id,
            request.canonical_sha256,
            request.markdown_sha256,
            request.target_index_id,
            PublicationIndexStatus.done,
        )
