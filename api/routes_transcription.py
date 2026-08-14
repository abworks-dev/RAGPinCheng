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
    DOCS_DIR,
    TRANSCRIPTION_ADMITTED_PROFILE_IDS,
    TRANSCRIPTION_ARTIFACT_DIR,
)
from src.transcription.profile import ProfileOperation
from src.transcription.profile_catalog import (
    FASTER_WHISPER_PROVIDER_KEY,
    FUNASR_SENSEVOICE_PROVIDER_KEY,
    QWEN3_ASR_PROVIDER_KEY,
    WHISPERX_PROVIDER_KEY,
)
from src.transcription.provider_registry import ProviderRegistry
from src.transcription.types import ContractValidationError, TranscriptionJobStatus

from .auth import CurrentUser, require_admin, require_csrf_admin
from .db import connect
from .schemas import (
    RetryTranscriptionRequest,
    PublishTranscriptVersionRequest,
    PublishTranscriptVersionResponse,
    ReviewTranscriptVersionRequest,
    TranscriptMarkdownPreviewDTO,
    TranscriptPublicationJobDTO,
    TranscriptVersionDTO,
    TranscriptionJobDTO,
    TranscriptionFailureDTO,
    TranscriptionProfileDTO,
)
from .transcription_artifacts import LocalTranscriptionArtifactStore
from .transcription_publication import TranscriptionPublicationApplicationService
from .indexing import enqueue_publication
from .transcription_media import FfmpegMediaAudioPreparer
from .transcription_runtime import (
    RemoteAsrProviderFactory,
    build_phase4_profile_catalog,
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
        admitted_profile_ids=TRANSCRIPTION_ADMITTED_PROFILE_IDS,
    )
    factories = tuple(
        RemoteAsrProviderFactory(
            ASR_SERVICE_URL,
            ASR_SERVICE_TOKEN,
            ASR_CONNECT_TIMEOUT_SECONDS,
            ASR_REQUEST_TIMEOUT_SECONDS,
            provider_key,
        )
        for provider_key in (
            FASTER_WHISPER_PROVIDER_KEY,
            FUNASR_SENSEVOICE_PROVIDER_KEY,
            QWEN3_ASR_PROVIDER_KEY,
            WHISPERX_PROVIDER_KEY,
        )
    )
    return TranscriptionApplicationService(
        profiles=profiles,
        providers=ProviderRegistry(factories),
        preparer=FfmpegMediaAudioPreparer(
            MEDIA_DIR.resolve(), ASR_FFMPEG_PATH, ASR_MEDIA_PREP_TIMEOUT_SECONDS
        ),
        artifacts=LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR),
        job_timeout_ms=ASR_JOB_TIMEOUT_SECONDS * 1000,
)


_TRANSCRIPTION_FAILURES: dict[str, tuple[str, bool]] = {
    "provider_unavailable": ("自动转录服务暂时不可用，请稍后重试。", True),
    "provider_timeout": ("自动转录任务超时，可以重新转录。", True),
    "provider_oom": ("转录资源暂时不足，请稍后重试。", True),
    "transient_provider_error": ("转录服务暂时失败，可以重新转录。", True),
    "permanent_provider_error": ("转录服务执行失败，请联系管理员检查服务配置。", False),
    "input_too_large": ("视频音频超过转录服务限制。", False),
    "input_unavailable": ("无法读取有效音频，请检查视频文件。", False),
    "service_contract_mismatch": ("应用与转录服务契约不兼容，请联系管理员。", False),
    "service_request_identity_conflict": ("转录服务请求身份冲突，请联系管理员。", False),
    "invalid_provider_output": ("转录结果格式异常，请联系管理员。", False),
    "provider_cancelled": ("转录任务已取消，可以重新转录。", True),
    "worker_restarted": ("转录服务重启导致任务中断，可以重新转录。", True),
}


def _failure_dto(code: str | None) -> TranscriptionFailureDTO | None:
    if code is None:
        return None
    message, retryable = _TRANSCRIPTION_FAILURES.get(
        code, ("转录任务失败，请联系管理员查看技术详情。", False)
    )
    return TranscriptionFailureDTO(code=code, message=message, retryable=retryable)


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
        failure=_failure_dto(job.failure_error_code),
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
                FUNASR_SENSEVOICE_PROVIDER_KEY,
            ).capabilities()
            healthy = True
        except Exception:
            pass
    entries = build_phase4_profile_catalog(
        service_enabled=ASR_ENABLED,
        service_healthy=healthy,
        service_capabilities=capabilities,
        admitted_profile_ids=TRANSCRIPTION_ADMITTED_PROFILE_IDS,
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
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "retry_idempotency_conflict",
                        "message": "本次重试与原请求不一致，请重新发起重试。",
                        "retryable": False,
                    },
                )
            return _job_dto(SQLiteTranscriptionStore(conn).load_job(existing["id"]))
        active = conn.execute(
            """SELECT j.id FROM transcription_jobs j
               JOIN media_assets m ON m.media_id=j.media_id
               WHERE j.media_id=? AND j.status IN ('pending','running')
                 AND m.status <> 'archived'""",
            (media_id,),
        ).fetchone()
        media = conn.execute(
            "SELECT status FROM media_assets WHERE media_id=?", (media_id,)
        ).fetchone()
        if media is None or media["status"] == "archived":
            raise HTTPException(status_code=404, detail="媒体不存在")
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



def _build_publication_service(conn) -> TranscriptionPublicationApplicationService:
    profiles = build_phase4_profile_registry(
        upload_part_bytes=ASR_UPLOAD_PART_BYTES,
        poll_interval_ms=ASR_POLL_INTERVAL_MS,
        expected_api_version=ASR_EXPECTED_API_VERSION,
        admitted_profile_ids=TRANSCRIPTION_ADMITTED_PROFILE_IDS,
    )

    def media_title(media_id: str) -> str:
        row = conn.execute("SELECT title FROM media_assets WHERE media_id=?", (media_id,)).fetchone()
        if row is None:
            raise KeyError(media_id)
        return str(row["title"])

    return TranscriptionPublicationApplicationService(
        store=SQLiteTranscriptionStore(conn),
        artifacts=LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR),
        profiles=profiles,
        docs_root=DOCS_DIR,
        media_title=media_title,
    )


def run_publication_index_job(index_job_id: str) -> None:
    conn = connect()
    try:
        _build_publication_service(conn).run_publication_job(index_job_id)
    finally:
        conn.close()



def recover_publications_on_boot() -> tuple[str, ...]:
    conn = connect()
    queued: list[str] = []
    try:
        service = _build_publication_service(conn)
        rows = conn.execute(
            """SELECT v.id AS version_id,j.id AS index_job_id,j.status
               FROM transcript_versions v
               JOIN transcript_publication_index_jobs j ON j.transcript_version_id=v.id
               WHERE v.publication_status='publishing'
                 AND j.attempt_number=(SELECT MAX(j2.attempt_number)
                                       FROM transcript_publication_index_jobs j2
                                       WHERE j2.transcript_version_id=v.id)
               ORDER BY j.created_at,j.id"""
        ).fetchall()
        for row in rows:
            if row["status"] == "done":
                service.promote_ready(str(row["version_id"]))
            elif row["status"] in ("pending", "parsing", "chunking", "embedding"):
                index_job_id = str(row["index_job_id"])
                enqueue_publication(index_job_id)
                queued.append(index_job_id)
        return tuple(queued)
    finally:
        conn.close()


def _version_dto(version, current_version_id: str | None = None) -> TranscriptVersionDTO:
    return TranscriptVersionDTO(
        version_id=version.id,
        media_id=version.media_id,
        source=version.source.value,
        profile_id=version.profile_id,
        provider_key=version.provider_key,
        model_id=version.model_id,
        model_revision=version.model_revision,
        review_status=version.review_status.value,
        reviewed_by=version.reviewed_by,
        reviewed_at=version.reviewed_at,
        review_note=version.review_note,
        publication_status=version.publication_status.value,
        published_at=version.published_at,
        supersedes_version_id=version.supersedes_version_id,
        markdown_sha256=version.markdown_ref.content_sha256,
        created_at=version.created_at,
        updated_at=version.updated_at,
        is_current=version.id == current_version_id,
    )


def _publication_job_dto(job: dict[str, object] | None) -> TranscriptPublicationJobDTO | None:
    if job is None:
        return None
    return TranscriptPublicationJobDTO(
        index_job_id=str(job["id"]),
        transcript_version_id=str(job["transcript_version_id"]),
        attempt_number=int(job["attempt_number"]),
        target_index_id=str(job["target_index_id"]),
        status=str(job["status"]),
        error_code=None if job["error_code"] is None else str(job["error_code"]),
        error_summary=None if job["error_summary"] is None else str(job["error_summary"]),
        created_at=int(job["created_at"]),
        started_at=None if job["started_at"] is None else int(job["started_at"]),
        finished_at=None if job["finished_at"] is None else int(job["finished_at"]),
        updated_at=int(job["updated_at"]),
    )


@router.get("/media/{media_id}/versions", response_model=list[TranscriptVersionDTO])
def list_transcript_versions(media_id: str, _admin: CurrentUser = Depends(require_admin)):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        versions = service.list_versions(media_id)
        current = service.store.current_head(media_id)
        return [_version_dto(version, current) for version in versions]
    except ContractValidationError:
        raise HTTPException(status_code=400, detail="媒体标识不合法")
    finally:
        conn.close()


@router.get("/versions/{version_id}/markdown", response_model=TranscriptMarkdownPreviewDTO)
def preview_transcript_version(version_id: str, _admin: CurrentUser = Depends(require_admin)):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        version = service.store.load_version(version_id)
        return TranscriptMarkdownPreviewDTO(
            version_id=version.id,
            markdown=service.preview_markdown(version.id),
            markdown_sha256=version.markdown_ref.content_sha256,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except ContractValidationError:
        raise HTTPException(status_code=409, detail="转录稿完整性校验失败")
    finally:
        conn.close()


@router.post("/versions/{version_id}/review", response_model=TranscriptVersionDTO)
def review_transcript_version(
    version_id: str,
    body: ReviewTranscriptVersionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        version = service.review(
            version_id,
            approved=body.approved,
            reviewed_by=admin.id,
            review_note=body.review_note,
        )
        return _version_dto(version, service.store.current_head(version.media_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except (ContractValidationError, StoreConflictError):
        raise HTTPException(status_code=409, detail="当前版本状态不可审核")
    finally:
        conn.close()


@router.post("/versions/{version_id}/publish", response_model=PublishTranscriptVersionResponse, status_code=202)
def publish_transcript_version(
    version_id: str,
    _body: PublishTranscriptVersionRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        result = service.publish(version_id)
        version = result["version"]
        job = result["job"]
        if not result["reused"] and job is not None:
            enqueue_publication(str(job["id"]))
        return PublishTranscriptVersionResponse(
            version=_version_dto(version, service.store.current_head(version.media_id)),
            job=_publication_job_dto(job),
            reused=bool(result["reused"]),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except StoreConflictError:
        raise HTTPException(status_code=409, detail="发布命令发生并发冲突")
    except ContractValidationError:
        raise HTTPException(status_code=409, detail="当前版本尚不满足发布条件")
    finally:
        conn.close()


@router.get("/publication-jobs/{index_job_id}", response_model=TranscriptPublicationJobDTO)
def get_publication_job(index_job_id: str, _admin: CurrentUser = Depends(require_admin)):
    conn = connect()
    try:
        return _publication_job_dto(SQLiteTranscriptionStore(conn).load_publication_job(index_job_id))
    except (KeyError, ContractValidationError):
        raise HTTPException(status_code=404, detail="发布索引任务不存在")
    finally:
        conn.close()
