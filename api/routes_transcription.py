"""Admin application API for automatic transcription jobs."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import (
    ASR_CONNECT_TIMEOUT_SECONDS,
    ASR_ENABLED,
    ASR_EXPECTED_API_VERSION,
    ASR_FFMPEG_PATH,
    ASR_JOB_TIMEOUT_SECONDS,
    ASR_MEDIA_PREP_TIMEOUT_SECONDS,
    ASR_POLL_INTERVAL_MS,
    ASR_REQUEST_TIMEOUT_SECONDS,
    ASR_SERVICE_TOKEN,
    ASR_SERVICE_URL,
    ASR_UPLOAD_PART_BYTES,
    MEDIA_DIR,
    TRANSCRIPTION_ARTIFACT_DIR,
)
from src.transcription.profile import ProfileOperation
from src.transcription.profile_catalog import build_phase3_profile_catalog
from src.transcription.provider_registry import ProviderRegistry
from src.transcription.types import ContractValidationError, TranscriptionJobStatus

from .auth import CurrentUser, require_admin, require_csrf_admin
from .db import connect
from .schemas import (
    RetryTranscriptionRequest,
    TranscriptionJobDTO,
    TranscriptionProfileDTO,
)
from .transcription_artifacts import LocalTranscriptionArtifactStore
from .transcription_media import FfmpegMediaAudioPreparer
from .transcription_runtime import (
    RemoteAsrProviderFactory,
    build_phase4_profile_registry,
)
from .transcription_service import TranscriptionApplicationService
from .transcription_store import SQLiteTranscriptionStore, StoreConflictError
from .transcription_worker import enqueue

router = APIRouter(prefix="/admin/transcription", tags=["admin-transcription"])


def build_transcription_service() -> TranscriptionApplicationService:
    profiles = build_phase4_profile_registry(
        upload_part_bytes=ASR_UPLOAD_PART_BYTES,
        poll_interval_ms=ASR_POLL_INTERVAL_MS,
        expected_api_version=ASR_EXPECTED_API_VERSION,
    )
    factory = RemoteAsrProviderFactory(
        ASR_SERVICE_URL,
        ASR_SERVICE_TOKEN,
        ASR_CONNECT_TIMEOUT_SECONDS,
        ASR_REQUEST_TIMEOUT_SECONDS,
    )
    return TranscriptionApplicationService(
        profiles=profiles,
        providers=ProviderRegistry((factory,)),
        preparer=FfmpegMediaAudioPreparer(
            MEDIA_DIR.resolve(), ASR_FFMPEG_PATH, ASR_MEDIA_PREP_TIMEOUT_SECONDS
        ),
        artifacts=LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR),
        job_timeout_ms=ASR_JOB_TIMEOUT_SECONDS * 1000,
    )


def _job_dto(job) -> TranscriptionJobDTO:
    return TranscriptionJobDTO(
        job_id=job.id,
        media_id=job.media_id,
        attempt_number=job.attempt_number,
        profile_id=job.profile_id,
        status=job.status.value,
        stage=None if job.stage is None else job.stage.value,
        processed_ms=job.processed_ms,
        total_ms=job.total_ms,
        failure_error_code=job.failure_error_code,
        error_summary=job.error_summary,
        result_version_id=job.result_version_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


@router.get("/profiles", response_model=list[TranscriptionProfileDTO])
def list_profiles(
    _admin: CurrentUser = Depends(require_admin),
) -> list[TranscriptionProfileDTO]:
    capabilities = None
    healthy = False
    if ASR_ENABLED and ASR_SERVICE_TOKEN:
        try:
            capabilities = RemoteAsrProviderFactory(
                ASR_SERVICE_URL,
                ASR_SERVICE_TOKEN,
                ASR_CONNECT_TIMEOUT_SECONDS,
                ASR_REQUEST_TIMEOUT_SECONDS,
            ).capabilities()
            healthy = True
        except Exception:
            pass
    entries = build_phase3_profile_catalog(
        service_enabled=ASR_ENABLED,
        service_healthy=healthy,
        service_capabilities=capabilities,
    )
    return [
        TranscriptionProfileDTO(
            profile_id=item.profile.profile_id,
            display_name=item.profile.display_name,
            description=item.profile.description,
            qualification=item.profile.qualification.value,
            admission=item.profile.admission.value,
            availability=item.availability.value,
            unavailable_reason_code=item.unavailable_reason_code,
            requires_review=item.profile.release_policy.requires_review,
            auto_publish=item.profile.release_policy.auto_publish,
            auto_index=item.profile.release_policy.auto_index,
        )
        for item in entries
    ]


@router.get("/jobs", response_model=list[TranscriptionJobDTO])
def list_jobs(
    media_id: str | None = None,
    latest_per_media: bool = True,
    limit: int = Query(100, ge=1, le=500),
    _admin: CurrentUser = Depends(require_admin),
) -> list[TranscriptionJobDTO]:
    conn = connect()
    try:
        try:
            jobs = SQLiteTranscriptionStore(conn).list_jobs(
                media_id=media_id,
                latest_per_media=latest_per_media,
                limit=limit,
            )
        except ContractValidationError:
            raise HTTPException(status_code=400, detail="转录任务查询参数不合法")
        return [_job_dto(job) for job in jobs]
    finally:
        conn.close()


@router.get("/jobs/{job_id}", response_model=TranscriptionJobDTO)
def get_job(
    job_id: str,
    _admin: CurrentUser = Depends(require_admin),
) -> TranscriptionJobDTO:
    conn = connect()
    try:
        try:
            return _job_dto(SQLiteTranscriptionStore(conn).load_job(job_id))
        except (KeyError, ContractValidationError):
            raise HTTPException(status_code=404, detail="转录任务不存在")
    finally:
        conn.close()


@router.post("/jobs/{job_id}/cancel", response_model=TranscriptionJobDTO)
def cancel_job(
    job_id: str,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> TranscriptionJobDTO:
    conn = connect()
    try:
        store = SQLiteTranscriptionStore(conn)
        try:
            job = store.load_job(job_id)
            cancelled = store.cancel_job(
                job_id, now=max(int(time.time()), job.updated_at + 1)
            )
            conn.execute(
                "UPDATE media_assets SET status='uploaded',error=NULL,updated_at=? WHERE media_id=?",
                (int(time.time()), cancelled.media_id),
            )
            conn.commit()
            return _job_dto(cancelled)
        except KeyError:
            raise HTTPException(status_code=404, detail="转录任务不存在")
        except (ContractValidationError, StoreConflictError):
            raise HTTPException(status_code=409, detail="当前任务状态不可取消")
    finally:
        conn.close()


@router.post(
    "/media/{media_id}/retry", response_model=TranscriptionJobDTO, status_code=202
)
def retry_job(
    media_id: str,
    body: RetryTranscriptionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> TranscriptionJobDTO:
    if not ASR_ENABLED:
        raise HTTPException(status_code=503, detail="自动转录当前未启用")
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id,media_id,profile_id FROM transcription_jobs WHERE request_idempotency_key=?",
            (body.request_idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["media_id"] != media_id or existing["profile_id"] != body.profile_id:
                raise HTTPException(status_code=409, detail="幂等键与转录请求不匹配")
            return _job_dto(SQLiteTranscriptionStore(conn).load_job(existing["id"]))
        active = conn.execute(
            "SELECT id FROM transcription_jobs WHERE media_id=? AND status IN ('pending','running')",
            (media_id,),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="该媒体已有活动转录任务")
        previous = conn.execute(
            "SELECT status FROM transcription_jobs WHERE media_id=? ORDER BY attempt_number DESC LIMIT 1",
            (media_id,),
        ).fetchone()
        if previous is None or previous["status"] not in ("failed", "cancelled", "succeeded"):
            raise HTTPException(status_code=409, detail="该媒体没有可重试的终态任务")
    finally:
        conn.close()
    try:
        job = build_transcription_service().create_pending_job(
            media_id=media_id,
            profile_id=body.profile_id,
            request_idempotency_key=body.request_idempotency_key,
            created_by=admin.id,
            operation=ProfileOperation.retry,
        )
    except StoreConflictError:
        raise HTTPException(status_code=409, detail="转录任务状态发生冲突")
    except ContractValidationError:
        raise HTTPException(status_code=400, detail="转录请求不符合服务端契约")
    enqueue(job.id)
    return _job_dto(job)
