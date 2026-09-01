from __future__ import annotations

import sqlite3
import wave
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from api.db import connect, init_db
from api.transcription_artifacts import LocalTranscriptionArtifactStore
from api.transcription_media import FfmpegMediaAudioPreparer
from api.transcription_runtime import build_phase4_profile_registry
from api.transcription_service import TranscriptionApplicationService
from api.transcription_schemes import create_scheme, update_scheme
from api.transcription_store import SQLiteTranscriptionStore
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderCandidate
from src.transcription.provider_registry import ProviderRegistry
from src.transcription.runtime_ports import ProviderRuntimeState
from src.transcription.profile_catalog import (
    FUNASR_SENSEVOICE_PROFILE_ID,
    WHISPERX_BALANCED_PROFILE_ID,
    WHISPERX_FINE_PROFILE_ID,
    WHISPERX_NATURAL_PROFILE_ID,
    WHISPERX_PROFILE_ID,
)
from src.transcription.types import TimeUnit, TranscriptionJobStage, TranscriptionJobStatus

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"
RETRY_REQUEST_ID = "44444444-4444-4444-8444-444444444444"


@dataclass(frozen=True)
class AudioRunner:
    def run(self, args, *, timeout_seconds):
        with wave.open(str(Path(args[-1])), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\0\0" * 16_000)
        return 0


@dataclass(frozen=True)
class FakeRemoteProvider:
    ports: object
    provider_key: str = "funasr-sensevoice"

    def transcribe(self, input_ref, execution):
        content = b"".join(
            part.content for part in self.ports.input_source.iter_parts(input_ref, 1024)
        )
        assert len(content) == input_ref.size_bytes
        self.ports.progress_sink.record(
            "33333333-3333-4333-8333-333333333333",
            input_ref.duration_ms // 2,
            input_ref.duration_ms,
            ProviderRuntimeState.running,
            None,
        )
        return ProviderCandidate(
            self.provider_key,
            "zh-CN",
            input_ref.duration_ms,
            (
                CandidateSegment(
                    0,
                    "0",
                    str(input_ref.duration_ms),
                    TimeUnit.milliseconds,
                    "自动转录测试",
                ),
            ),
        )


@dataclass
class FakeFactory:
    provider_key: str = "funasr-sensevoice"
    created_ports: list[object] = field(default_factory=list)

    def create(self, ports):
        self.created_ports.append(ports)
        return FakeRemoteProvider(ports, provider_key=self.provider_key)


def make_service(tmp_path, admitted_profile_ids=None, provider_keys=("funasr-sensevoice",)):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at) VALUES (1,'admin','Admin','x','admin',1,1)"
    )
    conn.execute(
        """INSERT INTO media_assets(media_id,title,original_filename,storage_rel_path,mime_type,file_size,
           sha256,transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (MEDIA_ID, "Fixture", "fixture.mp4", f"{MEDIA_ID}/original.mp4", "video/mp4", 3, None, None, "generated", "uploaded", 1, 1, 1, None),
    )
    conn.commit()
    conn.close()
    media_root = (tmp_path / "media").resolve()
    media_dir = media_root / MEDIA_ID
    media_dir.mkdir(parents=True)
    (media_dir / "original.mp4").write_bytes(b"mp4")
    factory = lambda: connect(db_path)
    service = TranscriptionApplicationService(
        build_phase4_profile_registry(
            upload_part_bytes=1024 * 1024,
            poll_interval_ms=100,
            expected_api_version="asr-service/1",
            admitted_profile_ids=admitted_profile_ids,
        ),
        ProviderRegistry(tuple(sorted(FakeFactory(provider_key=key) for key in provider_keys))),
        FfmpegMediaAudioPreparer(media_root, "fake-ffmpeg", 10, AudioRunner()),
        LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve()),
        60_000,
        connect_factory=factory,
        clock=lambda: 100,
    )
    return service, factory


def test_application_persists_candidate_version_without_publication_or_index(tmp_path):
    service, factory = make_service(tmp_path)
    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id="funasr-sensevoice-zh-experimental-v1",
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )
    assert job.audio_started_at is None
    result = service.run_job(job.id)
    assert result.status is TranscriptionJobStatus.succeeded
    provider_factory = service.providers.factories[0]
    assert provider_factory.created_ports[0].application_job_id == job.id
    assert result.processed_ms == result.total_ms
    assert result.audio_started_at is not None
    assert result.audio_finished_at is not None
    assert result.transcribing_at is not None
    assert result.audio_finished_at >= result.audio_started_at
    assert result.transcribing_at >= result.audio_finished_at
    retry = service.create_retry_job(
        previous_job_id=result.id,
        request_idempotency_key="55555555-5555-4555-8555-555555555555",
        created_by=1,
    )
    assert retry.audio_started_at == result.audio_started_at
    assert retry.audio_finished_at == result.audio_finished_at
    assert retry.transcribing_at is None
    conn = factory()
    try:
        version = conn.execute(
            "SELECT review_status,publication_status FROM transcript_versions WHERE id=?",
            (result.result_version_id,),
        ).fetchone()
        assert tuple(version) == ("awaiting_review", "not_published")
        assert conn.execute("SELECT COUNT(*) FROM index_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transcript_publication_index_jobs").fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (MEDIA_ID,)
        ).fetchone()[0] == "transcript_ready"
    finally:
        conn.close()


def test_application_creates_pending_job_for_admitted_whisperx(tmp_path):
    service, _factory = make_service(
        tmp_path,
        (FUNASR_SENSEVOICE_PROFILE_ID, WHISPERX_PROFILE_ID),
    )

    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id=WHISPERX_PROFILE_ID,
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )

    assert job.profile_id == WHISPERX_PROFILE_ID
    assert job.status is TranscriptionJobStatus.pending


def test_scheme_snapshot_is_persisted_and_retry_ignores_later_scheme_changes(tmp_path):
    service, factory = make_service(tmp_path)
    conn = factory()
    try:
        custom = create_scheme(
            conn,
            name="自定义中文方案",
            description="受控快照测试",
            base_id="sensevoice-v1",
            parameters={
                "segmentation_preset": "custom",
                "max_duration_ms": 20_000,
                "max_chars": 180,
                "merge_gap_ms": 600,
            },
            actor_id=1,
        )
        conn.commit()
    finally:
        conn.close()

    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id=FUNASR_SENSEVOICE_PROFILE_ID,
        scheme_id=custom["id"],
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )
    assert job.profile_id == FUNASR_SENSEVOICE_PROFILE_ID
    assert job.scheme_id == custom["id"]
    assert job.scheme_snapshot.version == 1
    assert job.stage is TranscriptionJobStage.preparing_audio
    assert job.scheme_snapshot.parameters["max_chars"] == 180
    assert job.scheme_snapshot.parameters["max_duration_ms"] == 20_000
    assert job.scheme_snapshot.parameters["merge_gap_ms"] == 600

    conn = factory()
    try:
        stored = conn.execute(
            "SELECT scheme_id,scheme_snapshot_json FROM transcription_jobs WHERE id=?", (job.id,)
        ).fetchone()
        assert stored["scheme_id"] == custom["id"]
        assert '"version":1' in stored["scheme_snapshot_json"]
        cancelled = SQLiteTranscriptionStore(conn).cancel_job(job.id, now=101)
        update_scheme(
            conn, custom["id"], name=None, description=None,
            parameters={"segmentation_preset": "fine", "max_chars": 120},
            enabled=False, archived=True, expected_version=1, actor_id=1,
        )
        conn.commit()
    finally:
        conn.close()

    retry = service.create_retry_job(
        previous_job_id=cancelled.id,
        request_idempotency_key=RETRY_REQUEST_ID,
        created_by=1,
    )
    assert retry.scheme_snapshot == job.scheme_snapshot
    assert retry.stage is TranscriptionJobStage.preparing_audio

    result = service.run_job(retry.id)
    assert result.status is TranscriptionJobStatus.succeeded
    assert result.execution_config is not None
    assert result.execution_config.segmentation_config.max_segment_chars == 180
    conn = factory()
    try:
        version = conn.execute(
            "SELECT scheme_id,scheme_snapshot_json FROM transcript_versions WHERE id=?",
            (result.result_version_id,),
        ).fetchone()
        assert version["scheme_id"] == custom["id"]
        assert version["scheme_snapshot_json"] == stored["scheme_snapshot_json"]
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("scheme_id", "preset", "max_duration_ms"),
    (
        (WHISPERX_NATURAL_PROFILE_ID, "natural", None),
        (WHISPERX_BALANCED_PROFILE_ID, "balanced", 30_000),
        (WHISPERX_FINE_PROFILE_ID, "fine", 15_000),
    ),
)
def test_whisperx_system_schemes_use_one_admitted_runtime_profile(
    tmp_path, scheme_id, preset, max_duration_ms
):
    service, _factory = make_service(
        tmp_path, (WHISPERX_BALANCED_PROFILE_ID,), provider_keys=("whisperx",)
    )

    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id=WHISPERX_BALANCED_PROFILE_ID,
        scheme_id=scheme_id,
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )

    assert job.profile_id == WHISPERX_BALANCED_PROFILE_ID
    assert job.scheme_id == scheme_id
    assert job.scheme_snapshot.scheme_id == scheme_id
    assert job.stage is TranscriptionJobStage.preparing_audio
    assert job.scheme_snapshot.parameters["segmentation_preset"] == preset
    assert job.scheme_snapshot.parameters["max_duration_ms"] == max_duration_ms
    result = service.run_job(job.id)
    assert result.status is TranscriptionJobStatus.succeeded
    assert result.execution_config is not None
    assert result.execution_config.segmentation_config.preset == preset
    assert result.execution_config.segmentation_config.max_segment_duration_ms == max_duration_ms


def test_custom_whisperx_scheme_uses_fixed_runtime_and_frozen_parameters(tmp_path):
    service, factory = make_service(
        tmp_path, (WHISPERX_BALANCED_PROFILE_ID,), provider_keys=("whisperx",)
    )
    conn = factory()
    try:
        custom = create_scheme(
            conn,
            name="自定义 WhisperX",
            description="固定运行身份",
            base_id="whisperx-v2",
            parameters={
                "segmentation_preset": "custom",
                "max_duration_ms": 22_000,
                "max_chars": 260,
                "merge_gap_ms": 650,
            },
            actor_id=1,
        )
        conn.commit()
    finally:
        conn.close()

    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id=WHISPERX_BALANCED_PROFILE_ID,
        scheme_id=custom["id"],
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )

    assert job.profile_id == WHISPERX_BALANCED_PROFILE_ID
    assert job.scheme_id == custom["id"]
    assert job.scheme_snapshot.parameters["max_duration_ms"] == 22_000
    assert job.stage is TranscriptionJobStage.preparing_audio
    result = service.run_job(job.id)
    assert result.status is TranscriptionJobStatus.succeeded
    assert result.execution_config is not None
    assert result.execution_config.segmentation_config.max_segment_chars == 260

def test_cancelled_job_is_terminal_and_worker_does_not_revive_it(tmp_path):
    service, factory = make_service(tmp_path)
    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id="funasr-sensevoice-zh-experimental-v1",
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )
    conn = factory()
    try:
        from api.transcription_store import SQLiteTranscriptionStore

        cancelled = SQLiteTranscriptionStore(conn).cancel_job(job.id, now=101)
    finally:
        conn.close()
    assert service.run_job(job.id) == cancelled


def test_process_shutdown_never_persists_a_success_version(tmp_path):
    from threading import Event

    from api.transcription_runtime import EventCancellationProbe

    service, factory = make_service(tmp_path)
    job = service.create_pending_job(
        media_id=MEDIA_ID,
        profile_id="funasr-sensevoice-zh-experimental-v1",
        request_idempotency_key=REQUEST_ID,
        created_by=1,
    )
    shutdown = Event()
    shutdown.set()
    result = service.run_job(job.id, EventCancellationProbe(shutdown))
    assert result.status is TranscriptionJobStatus.running
    assert result.result_version_id is None
    conn = factory()
    try:
        assert conn.execute("SELECT COUNT(*) FROM transcript_versions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM index_jobs").fetchone()[0] == 0
    finally:
        conn.close()
