from __future__ import annotations

import sqlite3
import wave
from dataclasses import dataclass
from pathlib import Path

from api.db import connect, init_db
from api.transcription_artifacts import LocalTranscriptionArtifactStore
from api.transcription_media import FfmpegMediaAudioPreparer
from api.transcription_runtime import build_phase4_profile_registry
from api.transcription_service import TranscriptionApplicationService
from src.transcription.candidate import CandidateSegment
from src.transcription.provider_protocol import ProviderCandidate
from src.transcription.provider_registry import ProviderRegistry
from src.transcription.runtime_ports import ProviderRuntimeState
from src.transcription.types import TimeUnit, TranscriptionJobStatus

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"


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


@dataclass(frozen=True)
class FakeFactory:
    provider_key: str = "funasr-sensevoice"

    def create(self, ports):
        return FakeRemoteProvider(ports)


def make_service(tmp_path):
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
        ),
        ProviderRegistry((FakeFactory(),)),
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
    result = service.run_job(job.id)
    assert result.status is TranscriptionJobStatus.succeeded
    assert result.processed_ms == result.total_ms
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
