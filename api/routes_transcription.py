"""Admin application API for automatic transcription jobs."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
from src.transcription.profile_catalog import (
    FASTER_WHISPER_PROVIDER_KEY,
    FUNASR_SENSEVOICE_PROVIDER_KEY,
    QWEN3_ASR_PROVIDER_KEY,
    WHISPERX_PROVIDER_KEY,
)
from src.transcription.profile import ProfileOperation
from src.transcription.persistence import ManagedMarkdownRef
from src.transcription.provider_registry import ProviderRegistry
from src.transcription.scheme import SchemeValidationError
from src.transcription.types import ContractValidationError, TranscriptionJobStatus

from .auth import CurrentUser, require_admin, require_csrf_admin
from .content_store import _category_path, audit_event
from .db import connect
from .external_media import resolve_shared_category_key
from .schemas import (
    CreateMediaMetadataRevisionRequest,
    CreateTranscriptRevisionRequest,
    RetryTranscriptionRequest,
    StartTranscriptionRequest,
    BulkStartTranscriptionRequest,
    BulkRetryTranscriptionRequest,
    BulkReviewTranscriptionRequest,
    BulkPublishTranscriptionRequest,
    BulkTranscriptionActionResponse,
    BulkTranscriptionItemDTO,
    BulkTranscriptionPreflightResponse,
    BulkTranscriptionResponse,
    BulkDeleteTranscriptVersionItemDTO,
    BulkDeleteTranscriptVersionsRequest,
    BulkDeleteTranscriptVersionsResponse,
    PublishTranscriptVersionRequest,
    PublishTranscriptVersionResponse,
    ReviewTranscriptVersionRequest,
    MediaTranscriptDTO,
    MediaTranscriptSegmentDTO,
    TranscriptMarkdownPreviewDTO,
    TranscriptPublicationJobDTO,
    TranscriptVersionDTO,
    TranscriptionJobDTO,
    TranscriptionFailureDTO,
    TranscriptionActionItemDTO,
    TranscriptionProfileDTO,
    TranscriptionSchemeOptionDTO,
)
from .transcription_schemes import available_schemes, resolve_scheme_runtime
from .transcript_prompt_echo_guard import PromptEchoPublicationBlocked
from .media_storage import MediaStorageError, require_mutable_media_source, resolve_media_path
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
from .transcription_markdown import parse_transcript_segments
from .transcription_worker import enqueue

logger = logging.getLogger("api.routes_transcription")

router = APIRouter(prefix="/admin/transcription", tags=["admin-transcription"])


@router.get("/schemes", response_model=list[TranscriptionSchemeOptionDTO])
def list_scheme_options(
    _admin: CurrentUser = Depends(require_admin),
) -> list[TranscriptionSchemeOptionDTO]:
    conn = connect()
    try:
        return [
            TranscriptionSchemeOptionDTO(
                scheme_id=item["id"], name=item["name"], description=item["description"],
                base_id=item["base_id"], config_hash=item["config_hash"], enabled=item["enabled"],
                archived=item["archived"], sort_order=item["sort_order"], version=item["version"],
                availability="available",
            )
            for item in available_schemes(conn)
        ]
    finally:
        conn.close()


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
    def resolve_source(media_id: str):
        conn = connect()
        try:
            return resolve_media_path(conn, media_id).path
        except MediaStorageError as exc:
            # Preserve the storage reason so external-source enqueue failures are
            # actionable (for example, a stale SMB identity versus an unmounted root).
            raise ContractValidationError(str(exc), "media_id") from exc
        finally:
            conn.close()

    return TranscriptionApplicationService(
        profiles=profiles,
        providers=ProviderRegistry(factories),
        preparer=FfmpegMediaAudioPreparer(
            MEDIA_DIR.resolve(), ASR_FFMPEG_PATH, ASR_MEDIA_PREP_TIMEOUT_SECONDS,
            source_resolver=resolve_source,
        ),
        artifacts=LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR),
        job_timeout_ms=ASR_JOB_TIMEOUT_SECONDS * 1000,
    )


def resolve_admitted_retry_scheme(
    conn: sqlite3.Connection,
    preferred_scheme_id: str | None,
    *,
    service: TranscriptionApplicationService | None = None,
) -> tuple[str, str] | None:
    resolver = service or build_transcription_service()
    candidate_ids: list[str] = []
    if preferred_scheme_id:
        candidate_ids.append(preferred_scheme_id)
    candidate_ids.extend(
        str(scheme["id"])
        for scheme in available_schemes(conn)
        if str(scheme["id"]) not in candidate_ids
    )
    for scheme_id in candidate_ids:
        try:
            scheme, profile_id = resolve_scheme_runtime(conn, scheme_id)
            resolver.resolve_profile(profile_id, ProfileOperation.new_attempt)
        except (ContractValidationError, SchemeValidationError):
            continue
        return str(scheme["id"]), profile_id
    return None


_TRANSCRIPTION_FAILURES: dict[str, tuple[str, bool]] = {
    "provider_unavailable": ("自动转录服务暂时不可用，请稍后重试。", True),
    "profile_unavailable": ("所选转录 Profile 暂不可用，请稍后重试。", True),
    "queue_full": ("转录队列已满，请稍后重试。", True),
    "service_unavailable": ("转录服务当前暂停接收任务，请稍后重试。", True),
    "storage_unavailable": ("转录服务存储暂不可用，请稍后重试。", True),
    "disk_low": ("转录服务存储空间不足，请联系管理员处理。", True),
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


def _scheme_display(conn: sqlite3.Connection, scheme_id: str | None) -> tuple[str | None, bool]:
    """Resolve a scheme's display name and whether it was later removed (archived or missing)."""
    if not scheme_id:
        return None, False
    row = conn.execute(
        "SELECT name, archived FROM transcription_schemes WHERE id=?", (scheme_id,)
    ).fetchone()
    if row is None:
        return None, True
    return str(row["name"]), bool(row["archived"])


def _scheme_display_lookup(
    conn: sqlite3.Connection, scheme_ids: list[str | None]
) -> dict[str, tuple[str | None, bool]]:
    unique_ids = sorted({str(scheme_id) for scheme_id in scheme_ids if scheme_id})
    lookup: dict[str, tuple[str | None, bool]] = {}
    if unique_ids:
        placeholders = ",".join("?" for _ in unique_ids)
        for row in conn.execute(
            f"SELECT id,name,archived FROM transcription_schemes WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall():
            lookup[str(row["id"])] = (str(row["name"]), bool(row["archived"]))
    for scheme_id in unique_ids:
        if scheme_id not in lookup:
            lookup[scheme_id] = (None, True)
    return lookup


def _version_job_lookup(
    conn: sqlite3.Connection, version_ids: list[str]
) -> dict[str, tuple[int | None, int | None]]:
    """Return {version_id: (completed_at, attempt_number)} for the version list.

    An automatic transcript version row is inserted in the same transaction that
    marks its job succeeded, so ``finished_at`` is the transcription completion
    time; manual versions have no job and fall back to their own ``created_at``.
    """
    unique_ids = sorted({str(version_id) for version_id in version_ids if version_id})
    lookup: dict[str, tuple[int | None, int | None]] = {}
    if not unique_ids:
        return lookup
    placeholders = ",".join("?" for _ in unique_ids)
    for row in conn.execute(
        f"""SELECT v.id AS version_id,v.created_at AS version_created_at,
                   j.finished_at AS job_finished_at,j.attempt_number AS job_attempt_number
              FROM transcript_versions v
              LEFT JOIN transcription_jobs j ON j.id=v.transcription_job_id
             WHERE v.id IN ({placeholders})""",
        unique_ids,
    ).fetchall():
        finished_at = row["job_finished_at"]
        lookup[str(row["version_id"])] = (
            int(finished_at) if finished_at is not None else int(row["version_created_at"]),
            None if row["job_attempt_number"] is None else int(row["job_attempt_number"]),
        )
    return lookup


def _job_dto(
    job,
    scheme_lookup: dict[str, tuple[str | None, bool]] | None = None,
) -> TranscriptionJobDTO:
    scheme_name: str | None = None
    scheme_deleted = False
    if job.scheme_id:
        if scheme_lookup is not None:
            scheme_name, scheme_deleted = scheme_lookup.get(
                job.scheme_id, (None, True)
            )
        else:
            conn = connect()
            try:
                scheme_name, scheme_deleted = _scheme_display(conn, job.scheme_id)
            finally:
                conn.close()
    return TranscriptionJobDTO(
        job_id=job.id,
        media_id=job.media_id,
        attempt_number=job.attempt_number,
        profile_id=job.profile_id,
        scheme_id=job.scheme_id,
        scheme_name=scheme_name,
        scheme_deleted=scheme_deleted,
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
        audio_started_at=job.audio_started_at,
        audio_finished_at=job.audio_finished_at,
        transcribing_at=job.transcribing_at,
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
        scheme_lookup = _scheme_display_lookup(conn, [item.scheme_id for item in jobs])
        return [_job_dto(item, scheme_lookup) for item in jobs]
    finally:
        conn.close()


def _bulk_media_rows(conn, body: BulkStartTranscriptionRequest):
    selectors = sum(bool(value) for value in (body.media_ids, body.upload_batch_id, body.category_id))
    if selectors != 1:
        raise HTTPException(status_code=400, detail="批量转录必须指定视频、上传批次或目录范围")
    if body.media_ids:
        ids: list[str] = []
        for media_id in body.media_ids:
            try:
                uuid.UUID(media_id)
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="视频标识不合法")
            if media_id not in ids:
                ids.append(media_id)
        placeholders = ",".join("?" for _ in ids)
        return conn.execute(
            f"""SELECT m.media_id,m.title,m.original_filename,m.status,
                       (SELECT j.status FROM transcription_jobs j WHERE j.media_id=m.media_id
                        ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_status,
                       (SELECT j.failure_classification FROM transcription_jobs j WHERE j.media_id=m.media_id
                        ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_failure_classification,
                       COALESCE(i.category_id,m.target_category_id) AS category_id
                FROM media_assets m LEFT JOIN content_items i
                  ON i.media_id=m.media_id AND i.content_kind='media_transcript' AND i.archived_at IS NULL
                WHERE m.media_id IN ({placeholders}) AND m.status<>'archived'
                ORDER BY m.created_at DESC""", ids,
        ).fetchall()
    if body.upload_batch_id:
        return conn.execute(
            """SELECT DISTINCT m.media_id,m.title,m.original_filename,m.status,
                      (SELECT j.status FROM transcription_jobs j WHERE j.media_id=m.media_id
                       ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_status,
                      (SELECT j.failure_classification FROM transcription_jobs j WHERE j.media_id=m.media_id
                       ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_failure_classification,
                      COALESCE(i.category_id,m.target_category_id) AS category_id
               FROM upload_batch_entries e JOIN media_assets m ON m.media_id=e.media_id
               LEFT JOIN content_items i
                 ON i.media_id=m.media_id AND i.content_kind='media_transcript' AND i.archived_at IS NULL
               WHERE e.batch_id=? AND e.entry_kind='video' AND m.status<>'archived'
               ORDER BY e.sequence""", (body.upload_batch_id,),
        ).fetchall()
    if body.recursive:
        category_clause = """i.category_id IN (
            WITH RECURSIVE descendants(id) AS (
                SELECT ? UNION ALL SELECT c.id FROM category_nodes c JOIN descendants d ON c.parent_id=d.id
            ) SELECT id FROM descendants
        )"""
    else:
        category_clause = "i.category_id=?"
    return conn.execute(
        f"""SELECT m.media_id,m.title,m.original_filename,m.status,
                   (SELECT j.status FROM transcription_jobs j WHERE j.media_id=m.media_id
                    ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_status,
                   (SELECT j.failure_classification FROM transcription_jobs j WHERE j.media_id=m.media_id
                    ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_failure_classification,
                   i.category_id AS category_id
            FROM content_items i JOIN media_assets m ON m.media_id=i.media_id
            WHERE i.content_kind='media_transcript' AND i.archived_at IS NULL
              AND m.status<>'archived' AND {category_clause}
            ORDER BY i.category_id,m.created_at DESC""", (body.category_id,),
    ).fetchall()


def _check_start_scheme(conn, scheme_id: str) -> str:
    if not ASR_ENABLED or not ASR_SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="自动转录当前不可用")
    try:
        _scheme, profile_id = resolve_scheme_runtime(conn, scheme_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="所选转录方案当前不可用") from exc
    return profile_id


def _preflight_bulk_items(conn, body: BulkStartTranscriptionRequest) -> list[BulkTranscriptionItemDTO]:
    try:
        uuid.UUID(body.request_idempotency_key)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="批量转录幂等键不合法")
    _check_start_scheme(conn, body.scheme_id)
    result: list[BulkTranscriptionItemDTO] = []
    for row in _bulk_media_rows(conn, body):
        job_status = str(row["job_status"] or "")
        if job_status in {"pending", "running"}:
            item_status, reason = "already_started", "已有正在运行的转录任务"
        elif job_status == "succeeded":
            item_status, reason = "unavailable", "该视频已完成转录"
        elif row["status"] == "failed" and not job_status:
            item_status, reason = "unavailable", "视频上传失败，请重新上传或删除失败记录"
        elif row["status"] == "failed" and job_status not in {"failed", "cancelled"}:
            item_status, reason = "unavailable", "视频当前不可启动转录"
        else:
            item_status, reason = "ready", None
        result.append(BulkTranscriptionItemDTO(
            media_id=row["media_id"], title=row["title"], original_filename=row["original_filename"],
            category_path=_category_path(conn, str(row["category_id"])) if row["category_id"] else None,
            status=item_status, reason=reason,
        ))
    return result


@router.post("/media/{media_id}/start", response_model=TranscriptionJobDTO, status_code=202)
def start_media_transcription(
    media_id: str,
    body: StartTranscriptionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> TranscriptionJobDTO:
    conn = connect()
    try:
        try:
            uuid.UUID(media_id); uuid.UUID(body.request_idempotency_key)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="视频或幂等键不合法")
        profile_id = _check_start_scheme(conn, body.scheme_id)
        row = conn.execute("SELECT status FROM media_assets WHERE media_id=?", (media_id,)).fetchone()
        if row is None or row["status"] == "archived":
            raise HTTPException(status_code=404, detail="视频不存在")
        latest = conn.execute(
            """SELECT status,failure_classification FROM transcription_jobs
               WHERE media_id=? ORDER BY attempt_number DESC,created_at DESC LIMIT 1""",
            (media_id,),
        ).fetchone()
        if row["status"] == "failed" and latest is None:
            raise HTTPException(status_code=409, detail="该视频上传失败，请重新上传或删除失败记录")
        # A permanently failed attempt must not block a new attempt: the operator
        # re-transcribes with an explicitly chosen scheme, which creates a fresh
        # job and never overwrites an existing version. Only retry_transcription
        # (resuming the terminal job itself) stays closed for permanent failures.
        existing = conn.execute(
            "SELECT id,media_id,scheme_id FROM transcription_jobs WHERE request_idempotency_key=?",
            (body.request_idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["media_id"] != media_id or existing["scheme_id"] != body.scheme_id:
                raise HTTPException(status_code=409, detail="本次提交与原请求不一致")
            return _job_dto(SQLiteTranscriptionStore(conn).load_job(existing["id"]), _scheme_display_lookup(conn, [existing["scheme_id"]]))
        active = conn.execute(
            "SELECT id FROM transcription_jobs WHERE media_id=? AND status IN ('pending','running')",
            (media_id,),
        ).fetchone()
        if active is not None:
            raise HTTPException(status_code=409, detail="该视频已有正在运行的转录任务")
    finally:
        conn.close()
    try:
        job = build_transcription_service().create_pending_job(
            media_id=media_id, profile_id=profile_id,
            request_idempotency_key=body.request_idempotency_key,
            created_by=admin.id, scheme_id=body.scheme_id,
        )
    except (ContractValidationError, StoreConflictError, OSError) as exc:
        raise HTTPException(status_code=409, detail="视频音频准备失败，请检查视频后重试") from exc
    conn = connect()
    try:
        conn.execute("UPDATE media_assets SET status='transcribing',error=NULL,updated_at=? WHERE media_id=?", (int(time.time()), media_id))
        conn.commit()
    finally:
        conn.close()
    enqueue(job.id)
    return _job_dto(job)


@router.post("/bulk-start/preflight", response_model=BulkTranscriptionPreflightResponse)
def preflight_bulk_start_transcription(
    body: BulkStartTranscriptionRequest,
    _admin: CurrentUser = Depends(require_admin),
) -> BulkTranscriptionPreflightResponse:
    conn = connect()
    try:
        items = _preflight_bulk_items(conn, body)
        return BulkTranscriptionPreflightResponse(
            scheme_id=body.scheme_id, items=items,
            ready_count=sum(item.status == "ready" for item in items),
            blocked_count=sum(item.status != "ready" for item in items),
        )
    finally:
        conn.close()


@router.post("/bulk-start", response_model=BulkTranscriptionResponse, status_code=202)
def bulk_start_transcription(
    body: BulkStartTranscriptionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> BulkTranscriptionResponse:
    conn = connect()
    try:
        items = _preflight_bulk_items(conn, body)
        profile_id = _check_start_scheme(conn, body.scheme_id)
    finally:
        conn.close()
    output: list[BulkTranscriptionItemDTO] = []
    started = 0
    for item in items:
        if item.status != "ready":
            output.append(item)
            continue
        key = str(uuid.uuid5(uuid.UUID(body.request_idempotency_key), item.media_id))
        try:
            job = build_transcription_service().create_pending_job(
                media_id=item.media_id, profile_id=profile_id,
                request_idempotency_key=key, created_by=admin.id, scheme_id=body.scheme_id,
            )
            conn = connect()
            try:
                conn.execute("UPDATE media_assets SET status='transcribing',error=NULL,updated_at=? WHERE media_id=?", (int(time.time()), item.media_id))
                conn.commit()
            finally:
                conn.close()
            enqueue(job.id)
            output.append(item.model_copy(update={"status": "started", "transcription_job_id": job.id}))
            started += 1
        except (ContractValidationError, StoreConflictError, OSError):
            output.append(item.model_copy(update={"status": "failed", "reason": "音频准备失败，请检查视频后重试"}))
    return BulkTranscriptionResponse(
        scheme_id=body.scheme_id, items=output, requested=len(items), started=started, failed=len(items)-started,
    )


@router.get("/jobs/{job_id}", response_model=TranscriptionJobDTO)
def get_job(
    job_id: str,
    _admin: CurrentUser = Depends(require_admin),
) -> TranscriptionJobDTO:
    conn = connect()
    try:
        try:
            job = SQLiteTranscriptionStore(conn).load_job(job_id)
            return _job_dto(job, _scheme_display_lookup(conn, [job.scheme_id]))
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
                """UPDATE media_assets SET status='uploaded',error=NULL,updated_at=?
                   WHERE media_id=?
                     AND NOT EXISTS(
                         SELECT 1 FROM transcription_jobs
                         WHERE media_id=? AND status IN ('pending','running')
                     )""",
                (int(time.time()), cancelled.media_id, cancelled.media_id),
            )
            conn.commit()
            return _job_dto(cancelled, _scheme_display_lookup(conn, [cancelled.scheme_id]))
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
    return _retry_media_job(media_id, body.request_idempotency_key, admin, body.profile_id)


def _retry_media_job(
    media_id: str,
    request_idempotency_key: str,
    admin: CurrentUser,
    profile_id: str | None = None,
) -> TranscriptionJobDTO:
    if not ASR_ENABLED or not ASR_SERVICE_TOKEN:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "transcription_service_unavailable",
                "message": "自动转录当前不可用。",
                "retryable": True,
            },
        )
    try:
        uuid.UUID(media_id)
        uuid.UUID(request_idempotency_key)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="媒体或重试幂等键不合法")
    service: TranscriptionApplicationService | None = None
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id,media_id,profile_id,scheme_id FROM transcription_jobs WHERE request_idempotency_key=?",
            (request_idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["media_id"] != media_id or (
                profile_id is not None
                and (existing["scheme_id"] or existing["profile_id"]) != profile_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "retry_idempotency_conflict",
                        "message": "本次重试与原请求不一致，请重新发起重试。",
                        "retryable": False,
                    },
                )
            return _job_dto(SQLiteTranscriptionStore(conn).load_job(existing["id"]), _scheme_display_lookup(conn, [existing["scheme_id"]]))
        active = conn.execute(
            """SELECT j.id FROM transcription_jobs j
               JOIN media_assets m ON m.media_id=j.media_id
               WHERE j.media_id=? AND j.status IN ('pending','running')
                 AND m.status <> 'archived'""",
            (media_id,),
        ).fetchone()
        media = conn.execute(
            """SELECT m.status,m.storage_kind,e.availability,s.default_scheme_id
               FROM media_assets m
               LEFT JOIN external_media_entries e ON e.media_id=m.media_id
               LEFT JOIN external_media_sources s ON s.id=e.source_id
               WHERE m.media_id=?""",
            (media_id,),
        ).fetchone()
        if media is None or media["status"] == "archived":
            raise HTTPException(status_code=404, detail="媒体不存在")
        if active is not None:
            raise HTTPException(status_code=409, detail="该媒体已有活动转录任务")
        previous = conn.execute(
            """SELECT id,status,profile_id,scheme_id,failure_classification
               FROM transcription_jobs WHERE media_id=?
               ORDER BY attempt_number DESC,created_at DESC,id DESC LIMIT 1""",
            (media_id,),
        ).fetchone()
        if previous is not None:
            if previous["status"] not in ("failed", "cancelled"):
                raise HTTPException(status_code=409, detail="该媒体没有可重试的失败或取消任务")
            if previous["failure_classification"] == "permanent":
                raise HTTPException(status_code=409, detail="该转录任务属于永久失败，不能自动重试")
            if profile_id is not None and (previous["scheme_id"] or previous["profile_id"]) != profile_id:
                raise HTTPException(status_code=409, detail="重试必须保留原转录方案")
            retry_kind = "existing_job"
            previous_job_id = str(previous["id"])
            retry_scheme_id = None
            retry_profile_id = None
        elif (
            media["storage_kind"] == "external"
            and media["status"] == "failed"
            and media["availability"] == "available"
            and media["default_scheme_id"]
        ):
            retry_kind = "external_reservation"
            preferred_scheme_id = str(media["default_scheme_id"])
            service = build_transcription_service()
            resolved_scheme = resolve_admitted_retry_scheme(
                conn, preferred_scheme_id, service=service
            )
            if resolved_scheme is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "transcription_scheme_unavailable",
                        "message": "当前没有可用的转录方案，请先调整共享目录的默认转录方案。",
                        "retryable": False,
                    },
                )
            retry_scheme_id, retry_profile_id = resolved_scheme
            if profile_id is not None and profile_id not in {
                preferred_scheme_id,
                retry_scheme_id,
                retry_profile_id,
            }:
                raise HTTPException(status_code=409, detail="重试必须使用共享来源的默认转录方案")
            previous_job_id = None
        else:
            raise HTTPException(status_code=409, detail="该媒体没有可重试的失败或取消任务")
    finally:
        conn.close()
    try:
        service = service or build_transcription_service()
        if retry_kind == "existing_job":
            job = service.create_retry_job(
                previous_job_id=previous_job_id,
                request_idempotency_key=request_idempotency_key,
                created_by=admin.id,
            )
        else:
            job = service.create_pending_job(
                media_id=media_id,
                profile_id=retry_profile_id,
                request_idempotency_key=request_idempotency_key,
                created_by=admin.id,
                scheme_id=retry_scheme_id,
            )
    except StoreConflictError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "transcription_state_conflict",
                "message": "转录任务状态已变化，请刷新列表后重试。",
                "retryable": True,
            },
        )
    except (ContractValidationError, OSError):
        raise HTTPException(status_code=409, detail="视频音频准备失败，请检查视频后重试")
    latest_job = job
    try:
        try:
            conn = connect()
            try:
                conn.execute(
                    """UPDATE media_assets
                       SET status='transcribing',error=NULL,updated_at=?
                       WHERE media_id=?
                         AND EXISTS(
                             SELECT 1 FROM transcription_jobs
                             WHERE id=? AND media_id=? AND status IN ('pending','running')
                         )""",
                    (int(time.time()), media_id, job.id, media_id),
                )
                conn.commit()
                latest_job = SQLiteTranscriptionStore(conn).load_job(job.id)
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            logger.exception(
                "retry job %s persisted but media summary update failed", job.id
            )
    finally:
        enqueue(job.id)
    return _job_dto(latest_job)


@router.post("/bulk-retry", response_model=BulkTranscriptionActionResponse, status_code=202)
def bulk_retry_jobs(
    body: BulkRetryTranscriptionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> BulkTranscriptionActionResponse:
    try:
        namespace = uuid.UUID(body.request_idempotency_key)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="批量重试幂等键不合法")
    items: list[TranscriptionActionItemDTO] = []
    seen: set[str] = set()
    for media_id in body.media_ids:
        if media_id in seen:
            continue
        seen.add(media_id)
        request_key = str(uuid.uuid5(namespace, media_id))
        try:
            job = _retry_media_job(media_id, request_key, admin)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="succeeded",
                transcription_job_id=job.job_id,
            ))
        except HTTPException as exc:
            message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="failed",
                message=message,
            ))
        except Exception:
            logger.exception("unexpected transcription retry error for %s", media_id)
            items.append(TranscriptionActionItemDTO(
                media_id=media_id,
                status="failed",
                message="操作失败，请稍后重试",
            ))
    succeeded = sum(item.status == "succeeded" for item in items)
    return BulkTranscriptionActionResponse(
        items=items,
        succeeded=succeeded,
        failed=len(items) - succeeded,
    )


@router.post("/media/bulk-review", response_model=BulkTranscriptionActionResponse, status_code=202)
def bulk_review_transcripts(
    body: BulkReviewTranscriptionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> BulkTranscriptionActionResponse:
    """Approve transcript versions in bulk, one row per media.

    Each media either names an explicit version or falls back to its latest
    version still awaiting review; every item is evaluated independently so a
    single conflicting version never fails the whole batch.
    """
    items: list[TranscriptionActionItemDTO] = []
    seen: set[str] = set()
    conn = connect()
    try:
        service = _build_publication_service(conn)
        for entry in body.items:
            if entry.media_id in seen:
                continue
            seen.add(entry.media_id)
            try:
                version_id = entry.version_id
                if not version_id:
                    row = conn.execute(
                        """SELECT id FROM transcript_versions
                           WHERE media_id=? AND review_status='awaiting_review'
                           ORDER BY created_at DESC,id DESC LIMIT 1""",
                        (entry.media_id,),
                    ).fetchone()
                    if row is None:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "no_version_awaiting_review",
                                "message": "该视频没有待审核的转录版本。",
                            },
                        )
                    version_id = str(row["id"])
                service.review(
                    version_id,
                    approved=True,
                    reviewed_by=admin.id,
                    review_note=body.review_note,
                )
                items.append(TranscriptionActionItemDTO(
                    media_id=entry.media_id,
                    status="succeeded",
                ))
            except HTTPException as exc:
                message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
                items.append(TranscriptionActionItemDTO(
                    media_id=entry.media_id,
                    status="failed",
                    message=message,
                ))
            except KeyError:
                items.append(TranscriptionActionItemDTO(
                    media_id=entry.media_id,
                    status="failed",
                    message="转录版本不存在",
                ))
            except (ContractValidationError, StoreConflictError):
                items.append(TranscriptionActionItemDTO(
                    media_id=entry.media_id,
                    status="failed",
                    message="当前版本状态不可审核，请刷新列表后重试",
                ))
            except Exception:
                logger.exception("unexpected transcription review error for %s", entry.media_id)
                items.append(TranscriptionActionItemDTO(
                    media_id=entry.media_id,
                    status="failed",
                    message="操作失败，请稍后重试",
                ))
    finally:
        conn.close()
    succeeded = sum(item.status == "succeeded" for item in items)
    return BulkTranscriptionActionResponse(
        items=items,
        succeeded=succeeded,
        failed=len(items) - succeeded,
    )


@router.post("/media/bulk-publish", response_model=BulkTranscriptionActionResponse, status_code=202)
def bulk_publish_transcripts(
    body: BulkPublishTranscriptionRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> BulkTranscriptionActionResponse:
    """Publish the latest approved, unpublished transcript version per media.

    Each media is handled independently: publication intent, candidate index
    job and queue enqueue reuse the exact single-item contract, so a busy or
    conflicting row only fails itself.
    """
    items: list[TranscriptionActionItemDTO] = []
    seen: set[str] = set()
    conn = connect()
    try:
        service = _build_publication_service(conn)
        for media_id in body.media_ids:
            if media_id in seen:
                continue
            seen.add(media_id)
            try:
                row = conn.execute(
                    """SELECT id FROM transcript_versions
                       WHERE media_id=? AND review_status='review_approved'
                         AND publication_status IN ('not_published','publication_failed')
                       ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (media_id,),
                ).fetchone()
                if row is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "no_version_to_publish",
                            "message": "该视频没有审核通过且未发布的转录版本。",
                        },
                    )
                result = service.publish(str(row["id"]))
                job = result["job"]
                if job is not None and not result["reused"]:
                    enqueue_publication(str(job["id"]))
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="succeeded",
                ))
            except HTTPException as exc:
                message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message=message,
                ))
            except KeyError:
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message="转录版本不存在",
                ))
            except StoreConflictError:
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message="发布命令发生并发冲突，请刷新列表后重试",
                ))
            except PromptEchoPublicationBlocked as exc:
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message=exc.message,
                ))
            except ContractValidationError:
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message="当前版本尚不满足发布条件",
                ))
            except Exception:
                logger.exception("unexpected transcription publish error for %s", media_id)
                items.append(TranscriptionActionItemDTO(
                    media_id=media_id,
                    status="failed",
                    message="操作失败，请稍后重试",
                ))
    finally:
        conn.close()
    succeeded = sum(item.status == "succeeded" for item in items)
    return BulkTranscriptionActionResponse(
        items=items,
        succeeded=succeeded,
        failed=len(items) - succeeded,
    )


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
        resolve_category_key=lambda media_id: resolve_shared_category_key(conn, media_id),
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


def _version_dto(
    version,
    current_version_id: str | None = None,
    scheme_lookup: dict[str, tuple[str | None, bool]] | None = None,
    job_lookup: dict[str, tuple[int | None, int | None]] | None = None,
) -> TranscriptVersionDTO:
    scheme_name: str | None = None
    scheme_deleted = False
    if version.scheme_id:
        if scheme_lookup is not None:
            scheme_name, scheme_deleted = scheme_lookup.get(
                version.scheme_id, (None, True)
            )
        else:
            conn = connect()
            try:
                scheme_name, scheme_deleted = _scheme_display(conn, version.scheme_id)
            finally:
                conn.close()
    completed_at = int(version.created_at)
    attempt_number: int | None = None
    if job_lookup is not None:
        resolved = job_lookup.get(version.id)
        if resolved is not None:
            completed_at, attempt_number = resolved
    return TranscriptVersionDTO(
        version_id=version.id,
        media_id=version.media_id,
        source=version.source.value,
        profile_id=version.profile_id,
        scheme_id=version.scheme_id,
        scheme_name=scheme_name,
        scheme_deleted=scheme_deleted,
        provider_key=version.provider_key,
        model_id=version.model_id,
        model_revision=version.model_revision,
        markdown_storage_kind=version.markdown_storage_kind.value,
        review_status=version.review_status.value,
        reviewed_by=version.reviewed_by,
        reviewed_at=version.reviewed_at,
        review_note=version.review_note,
        publication_status=version.publication_status.value,
        published_at=version.published_at,
        supersedes_version_id=version.supersedes_version_id,
        derived_from_version_id=version.derived_from_version_id,
        edited_by=version.edited_by,
        markdown_sha256=version.markdown_ref.content_sha256,
        created_at=version.created_at,
        updated_at=version.updated_at,
        is_current=version.id == current_version_id,
        completed_at=completed_at,
        attempt_number=attempt_number,
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


def _normalized_media_metadata(title: str, original_filename: str) -> tuple[str, str]:
    clean_title = unicodedata.normalize("NFKC", title).strip()
    clean_filename = unicodedata.normalize("NFKC", original_filename).strip()
    if not clean_title or len(clean_title) > 200 or any(char in clean_title for char in "\r\n\x00"):
        raise ContractValidationError("invalid_media_title", "title")
    if (
        not clean_filename
        or len(clean_filename) > 255
        or clean_filename != Path(clean_filename).name
        or re.search(r'[\x00-\x1f\x7f<>:"/\\|?*]', clean_filename)
        or clean_filename.rstrip(" .") != clean_filename
        or Path(clean_filename).suffix.lower() != ".mp4"
    ):
        raise ContractValidationError("invalid_media_filename", "original_filename")
    return clean_title, clean_filename


@router.post(
    "/media/{media_id}/metadata-revisions",
    response_model=TranscriptVersionDTO,
    status_code=201,
)
def create_media_metadata_revision(
    media_id: str,
    body: CreateMediaMetadataRevisionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
):
    conn = connect()
    try:
        try:
            require_mutable_media_source(conn, media_id)
        except MediaStorageError as exc:
            raise HTTPException(status_code=409, detail="共享源只读，不能修改媒体信息") from exc
        title, original_filename = _normalized_media_metadata(
            body.title, body.original_filename
        )
        service = _build_publication_service(conn)
        store = service.store
        base = store.load_version(body.expected_version_id)
        markdown_ref = service.artifacts.write_markdown(
            service.preview_markdown(base.id).encode("utf-8")
        )
        version = store.register_metadata_revision(
            revision_id=str(uuid.uuid4()),
            version_id=str(uuid.uuid4()),
            media_id=media_id,
            base_version_id=body.expected_version_id,
            markdown_ref=markdown_ref,
            proposed_title=title,
            proposed_original_filename=original_filename,
            requested_by=admin.id,
            request_idempotency_key=body.request_idempotency_key,
            now=int(time.time()),
        )
        return _version_dto(version, store.current_head(media_id), _scheme_display_lookup(conn, [version.scheme_id]))
    except KeyError:
        raise HTTPException(status_code=404, detail="视频或正式转录版本不存在")
    except StoreConflictError as exc:
        detail = {
            "metadata_base_version_conflict": "正式转录版本已变化，请刷新后重试。",
            "metadata_revision_active": "该视频已有待处理的媒体信息修订。",
            "media_replacement_active": "该视频正在替换，暂不能修改媒体信息。",
            "metadata_idempotency_conflict": "本次保存请求与已处理请求不一致，请重新提交。",
        }.get(str(exc), "媒体信息修订发生并发冲突，请刷新后重试。")
        raise HTTPException(status_code=409, detail=detail)
    except ContractValidationError as exc:
        detail = (
            "源文件名必须是安全的 .mp4 文件名，且不能包含路径或非法字符。"
            if exc.code == "invalid_media_filename"
            else "视频标题不能为空、不能换行且不能超过 200 字。"
        )
        raise HTTPException(status_code=400, detail=detail)
    finally:
        conn.close()


@router.get("/media/{media_id}/versions", response_model=list[TranscriptVersionDTO])
def list_transcript_versions(media_id: str, _admin: CurrentUser = Depends(require_admin)):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        versions = service.list_versions(media_id)
        current = service.store.current_head(media_id)
        job_lookup = _version_job_lookup(conn, [version.id for version in versions])
        return [
            _version_dto(
                version,
                current,
                _scheme_display_lookup(conn, [version.scheme_id for version in versions]),
                job_lookup,
            )
            for version in versions
        ]
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


@router.get("/versions/{version_id}/timeline", response_model=MediaTranscriptDTO)
def preview_transcript_version_timeline(
    version_id: str,
    _admin: CurrentUser = Depends(require_admin),
) -> MediaTranscriptDTO:
    conn = connect()
    try:
        service = _build_publication_service(conn)
        version = service.store.load_version(version_id)
        if version.canonical is not None:
            return MediaTranscriptDTO(
                media_id=version.media_id,
                version_id=version.id,
                language=version.canonical.language,
                duration_ms=version.canonical.duration_ms,
                segments=[
                    MediaTranscriptSegmentDTO(
                        id=segment.id,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                    )
                    for segment in version.canonical.segments
                ],
            )

        segments = parse_transcript_segments(service.preview_markdown(version.id))
        return MediaTranscriptDTO(
            media_id=version.media_id,
            version_id=version.id,
            segments=[
                MediaTranscriptSegmentDTO(
                    id=index,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                )
                for index, segment in enumerate(segments)
            ],
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except ContractValidationError:
        raise HTTPException(status_code=409, detail="转录稿时间轴不可用")
    finally:
        conn.close()


_TRANSCRIPT_VERSIONS_BULK_DELETE_EVENT = "content.transcript_versions_bulk_deleted"
_TRANSCRIPT_VERSION_DELETE_EVENT = "content.transcript_version_deleted"
_TRANSCRIPT_VERSIONS_DELETE_LIMIT = 50
_TRANSCRIPT_VERSION_DELETE_REASONS = {
    "not_found": "该转录版本不存在或已被删除。",
    "not_in_media": "该转录版本不属于当前视频。",
    "current_head": "当前正式版本不能删除，请先发布其它版本替换后再试。",
    "published": "已发布或正在发布的转录版本不能删除。",
    "referenced_derived_from": "该转录版本是人工校对稿的来源版本，不能删除。",
    "legacy_manual": "手工上传的历史转录稿不能在此删除。",
}
_TRANSCRIPT_VERSION_DELETE_FAILURE_REASON = "删除转录版本时发生并发冲突，请刷新列表后重试。"


class _TranscriptVersionUnavailable(Exception):
    """Internal per-item rejection carrying a product-facing reason key."""

    def __init__(self, reason_key: str) -> None:
        self.reason_key = reason_key
        super().__init__(reason_key)


@contextmanager
def _transcript_version_delete_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Serialize on the write lock so every rule is checked against a frozen state."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _transcript_version_unavailable_reason(
    conn: sqlite3.Connection, media_id: str, version_id: str
) -> str:
    """Return the safety rule that blocks deleting this version, or '' when allowed.

    A version that is only *superseded* stays deletable: every version in a
    normal chain supersedes the previous one, so treating that reference as a
    blocker made the whole batch-delete feature unusable. The caller clears
    those references in the same transaction before deleting the row.
    A version *awaiting review* is deletable too: an unreviewed attempt is
    disposable, and the product decision is that deleting it withdraws the
    attempt rather than forcing the reviewer to finish the review first.
    A `derived_from_version_id` reference still refuses the delete, because a
    manual revision keeps deriving from its source version.
    """
    row = conn.execute(
        """SELECT media_id,publication_status FROM transcript_versions WHERE id=?""",
        (version_id,),
    ).fetchone()
    if row is None:
        return "not_found"
    if str(row["media_id"]) != media_id:
        return "not_in_media"
    if str(row["publication_status"]) != "not_published":
        return "published"
    if conn.execute(
        "SELECT 1 FROM media_transcript_heads WHERE media_id=? AND current_version_id=?",
        (media_id, version_id),
    ).fetchone() is not None:
        return "current_head"
    if conn.execute(
        """SELECT 1 FROM transcript_versions
           WHERE id<>? AND derived_from_version_id=?
           LIMIT 1""",
        (version_id, version_id),
    ).fetchone() is not None:
        return "referenced_derived_from"
    return ""


def _transcript_version_is_legacy_manual(conn: sqlite3.Connection, version_id: str) -> bool:
    """A legacy manual row points at a hand-written file this endpoint does not own."""
    row = conn.execute(
        "SELECT markdown_storage_kind FROM transcript_versions WHERE id=?", (version_id,)
    ).fetchone()
    return row is not None and str(row["markdown_storage_kind"]) != "managed_artifact"


def _delete_transcript_version_row(
    conn: sqlite3.Connection,
    media_id: str,
    version_id: str,
    *,
    actor_user_id: int,
) -> dict[str, object]:
    """Delete one transcript version row inside its own transaction.

    Returns the artifact location that the caller may unlink after the commit.
    Raises `_TranscriptVersionUnavailable` when any safety rule blocks the
    delete, so a rejected item never aborts the rest of the batch.
    """
    with _transcript_version_delete_transaction(conn):
        row = conn.execute(
            "SELECT markdown_rel_path FROM transcript_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            raise _TranscriptVersionUnavailable("not_found")
        reason = _transcript_version_unavailable_reason(conn, media_id, version_id)
        if reason:
            raise _TranscriptVersionUnavailable(reason)
        if _transcript_version_is_legacy_manual(conn, version_id):
            raise _TranscriptVersionUnavailable("legacy_manual")
        markdown_rel_path = str(row["markdown_rel_path"])
        # A content-addressed artifact can be shared with another version, so it
        # may only be unlinked once no remaining row points at the same blob.
        shared = conn.execute(
            """SELECT 1 FROM transcript_versions
               WHERE id<>? AND markdown_storage_kind='managed_artifact' AND markdown_rel_path=?
               LIMIT 1""",
            (version_id, markdown_rel_path),
        ).fetchone() is not None
        artifact_path: str | None = markdown_rel_path if not shared else None
        # Superseding is history, not ownership: a newer version may point at a
        # version we are allowed to delete, so its `supersedes_version_id` is
        # cleared in the same transaction before the row goes away. Only
        # `derived_from_version_id` still refuses the delete (checked above).
        conn.execute(
            "UPDATE transcript_versions SET supersedes_version_id=NULL WHERE supersedes_version_id=?",
            (version_id,),
        )
        conn.execute("DELETE FROM transcript_version_artifacts WHERE version_id=?", (version_id,))
        # A metadata revision is owned by the version it proposes: the row is
        # inserted for that version's id in the same transaction that creates it
        # (always in `awaiting_review`), and `activate_metadata_revision` refuses
        # to activate it once this version is gone. Its FK is RESTRICT, so the
        # owner has to remove it here or the version row cannot be deleted at all.
        conn.execute(
            "DELETE FROM media_metadata_revisions WHERE transcript_version_id=?",
            (version_id,),
        )
        deleted = conn.execute(
            "DELETE FROM transcript_versions WHERE id=? AND media_id=?",
            (version_id, media_id),
        ).rowcount
        if deleted != 1:
            raise _TranscriptVersionUnavailable("not_found")
        # `version_id` is NULL on purpose: content_audit_events.version_id
        # references content_versions, while this is a transcript version.
        audit_event(
            conn,
            _TRANSCRIPT_VERSION_DELETE_EVENT,
            actor_user_id=actor_user_id,
            item_id=_transcript_catalog_item_id(conn, media_id),
            metadata={
                "media_id": media_id,
                "transcript_version_id": version_id,
                "artifact_rel_path": artifact_path,
            },
        )
    return {"artifact_rel_path": artifact_path}


def _transcript_catalog_item_id(conn: sqlite3.Connection, media_id: str) -> str | None:
    """Resolve the catalog item of this video so the audit event stays queryable."""
    row = conn.execute(
        """SELECT id FROM content_items
           WHERE media_id=? AND content_kind='media_transcript'
           ORDER BY created_at DESC,id DESC LIMIT 1""",
        (media_id,),
    ).fetchone()
    return None if row is None else str(row["id"])


def _delete_transcript_version_markdown(
    artifacts: LocalTranscriptionArtifactStore,
    result: dict[str, object],
    version_id: str,
) -> None:
    """Unlink the version's managed artifact after its row is already gone."""
    relative_path = result["artifact_rel_path"]
    if not relative_path:
        return
    reference = ManagedMarkdownRef(relative_path, "0" * 64, 0)
    try:
        artifacts.delete_markdown(reference)
    except (OSError, ValueError, ContractValidationError):
        logger.exception("transcript artifact cleanup failed for %s", version_id)


def _bulk_delete_transcript_versions_result(
    items: list[BulkDeleteTranscriptVersionItemDTO],
) -> BulkDeleteTranscriptVersionsResponse:
    deleted = sum(item.status == "deleted" for item in items)
    return BulkDeleteTranscriptVersionsResponse(
        items=items,
        deleted_count=deleted,
        skipped_count=len(items) - deleted,
    )


def _replay_bulk_version_delete(
    conn: sqlite3.Connection, media_id: str, fingerprint: str, idempotency_key: str
) -> BulkDeleteTranscriptVersionsResponse | None:
    """Return the first result of this request, or None when it is a new request.

    The completion audit event is the idempotency ledger: an existing event for
    the same key replays its stored per-item result instead of deleting again.
    Candidate rows are matched in Python so the lookup does not depend on the
    SQLite JSON1 extension being available.
    """
    rows = conn.execute(
        """SELECT metadata_json FROM content_audit_events
           WHERE event_type=? AND actor_user_id IS NOT NULL
           ORDER BY created_at DESC,rowid DESC LIMIT 1000""",
        (_TRANSCRIPT_VERSIONS_BULK_DELETE_EVENT,),
    ).fetchall()
    inconsistent = HTTPException(
        status_code=409, detail="本次批量删除请求与已处理的请求不一致，请重新提交。"
    )
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError):
            continue
        if metadata.get("request_idempotency_key") != idempotency_key:
            continue
        if metadata.get("media_id") != media_id or metadata.get("request_fingerprint") != fingerprint:
            raise inconsistent
        try:
            recorded_items = metadata["items"]
            return BulkDeleteTranscriptVersionsResponse(
                items=[
                    BulkDeleteTranscriptVersionItemDTO(
                        version_id=str(item["version_id"]),
                        status=str(item["status"]),
                        reason=None if item["reason"] is None else str(item["reason"]),
                    )
                    for item in recorded_items
                ],
                deleted_count=int(metadata["deleted_count"]),
                skipped_count=int(metadata["skipped_count"]),
            )
        except (TypeError, ValueError, KeyError):
            raise inconsistent
    return None


@router.post(
    "/media/{media_id}/versions/bulk-delete",
    response_model=BulkDeleteTranscriptVersionsResponse,
    status_code=202,
)
def bulk_delete_transcript_versions(
    media_id: str,
    body: BulkDeleteTranscriptVersionsRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
) -> BulkDeleteTranscriptVersionsResponse:
    """Permanently delete never-published old transcript versions of one video.

    Every item is validated and committed on its own, so a version that is the
    current head, published, or still referenced through
    `derived_from_version_id` only refuses itself. A version that is merely
    superseded by newer versions is deletable: those `supersedes_version_id`
    references are cleared in the same transaction. So is a version that is
    still awaiting review: deleting it withdraws the unreviewed attempt. The same
    `request_idempotency_key` replays the first result instead of deleting twice.
    """
    if not body.version_ids or len(body.version_ids) > _TRANSCRIPT_VERSIONS_DELETE_LIMIT:
        raise HTTPException(status_code=400, detail="每次最多删除 50 个转录版本，且列表不能为空。")
    try:
        uuid.UUID(media_id)
        uuid.UUID(body.request_idempotency_key)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="视频或幂等键不合法。")
    version_ids = list(dict.fromkeys(body.version_ids))
    if len(version_ids) > _TRANSCRIPT_VERSIONS_DELETE_LIMIT:
        raise HTTPException(status_code=400, detail="每次最多删除 50 个转录版本，且列表不能为空。")
    for version_id in version_ids:
        try:
            uuid.UUID(version_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="转录版本标识不合法。")
    fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            {"media_id": media_id, "version_ids": sorted(version_ids)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    artifacts = LocalTranscriptionArtifactStore(TRANSCRIPTION_ARTIFACT_DIR)
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM media_assets WHERE media_id=?", (media_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="视频不存在。")
        replayed = _replay_bulk_version_delete(conn, media_id, fingerprint, body.request_idempotency_key)
        if replayed is not None:
            return replayed
    finally:
        conn.close()

    items: list[BulkDeleteTranscriptVersionItemDTO] = []
    for version_id in version_ids:
        item_conn = connect()
        try:
            result = _delete_transcript_version_row(
                item_conn,
                media_id,
                version_id,
                actor_user_id=admin.id,
            )
            items.append(BulkDeleteTranscriptVersionItemDTO(version_id=version_id, status="deleted"))
        except _TranscriptVersionUnavailable as exc:
            items.append(
                BulkDeleteTranscriptVersionItemDTO(
                    version_id=version_id,
                    status="unavailable",
                    reason=_TRANSCRIPT_VERSION_DELETE_REASONS.get(
                        exc.reason_key, _TRANSCRIPT_VERSION_DELETE_REASONS["not_found"]
                    ),
                )
            )
            continue
        except Exception:
            logger.exception("bulk transcript version delete failed for %s", version_id)
            items.append(
                BulkDeleteTranscriptVersionItemDTO(
                    version_id=version_id,
                    status="conflict",
                    reason=_TRANSCRIPT_VERSION_DELETE_FAILURE_REASON,
                )
            )
            continue
        finally:
            item_conn.close()
        _delete_transcript_version_markdown(artifacts, result, version_id)

    response = _bulk_delete_transcript_versions_result(items)
    ledger = connect()
    try:
        _record_bulk_version_delete(
            ledger, media_id, fingerprint, body.request_idempotency_key, response, admin.id
        )
    finally:
        ledger.close()
    return response


def _record_bulk_version_delete(
    conn: sqlite3.Connection,
    media_id: str,
    fingerprint: str,
    idempotency_key: str,
    response: BulkDeleteTranscriptVersionsResponse,
    actor_user_id: int,
) -> None:
    """Write the idempotency ledger event; a failure never invalidates the deletes."""
    try:
        audit_event(
            conn,
            _TRANSCRIPT_VERSIONS_BULK_DELETE_EVENT,
            actor_user_id=actor_user_id,
            item_id=_transcript_catalog_item_id(conn, media_id),
            metadata={
                "media_id": media_id,
                "request_idempotency_key": idempotency_key,
                "request_fingerprint": fingerprint,
                "deleted_count": response.deleted_count,
                "skipped_count": response.skipped_count,
                "items": [
                    {
                        "version_id": item.version_id,
                        "status": item.status,
                        "reason": item.reason,
                    }
                    for item in response.items
                ],
            },
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("bulk transcript version delete ledger write failed for %s", media_id)


@router.post(
    "/versions/{base_version_id}/revisions",
    response_model=TranscriptVersionDTO,
    status_code=201,
)
def create_transcript_revision(
    base_version_id: str,
    body: CreateTranscriptRevisionRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        version = service.create_revision(
            base_version_id,
            markdown=body.markdown,
            base_markdown_sha256=body.base_markdown_sha256,
            edited_by=admin.id,
            request_idempotency_key=body.request_idempotency_key,
        )
        return _version_dto(version, service.store.current_head(version.media_id), _scheme_display_lookup(conn, [version.scheme_id]))
    except KeyError:
        raise HTTPException(status_code=404, detail="基础转录版本不存在")
    except StoreConflictError as exc:
        detail = {
            "stale_base_markdown": "基础版本已变化，请重新加载后再保存。",
            "unchanged_markdown": "内容没有变化，无需创建新草稿。",
            "edit_idempotency_conflict": "保存请求与已处理请求不一致，请重新发起保存。",
        }.get(str(exc), "保存修订时发生并发冲突，请重新加载后再试。")
        raise HTTPException(status_code=409, detail=detail)
    except ContractValidationError as exc:
        detail = {
            "empty_transcript_markdown": "转录稿不能为空。",
            "transcript_markdown_too_large": "转录稿超过 2 MiB 限制。",
            "transcript_turn_required": "转录稿至少需要一段带时间戳且有正文的说话人内容。",
            "invalid_transcript_timestamp": "转录稿包含无效时间戳。",
            "invalid_markdown_encoding": "转录稿必须是有效的 UTF-8 文本。",
        }.get(exc.code, "转录稿格式不符合要求。")
        raise HTTPException(status_code=400, detail=detail)
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
        return _version_dto(version, service.store.current_head(version.media_id), _scheme_display_lookup(conn, [version.scheme_id]))
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except (ContractValidationError, StoreConflictError):
        raise HTTPException(status_code=409, detail="当前版本状态不可审核")
    finally:
        conn.close()


@router.post("/versions/{version_id}/return-to-review", response_model=TranscriptVersionDTO)
def return_version_to_review(version_id: str, _admin: CurrentUser = Depends(require_csrf_admin)):
    conn = connect()
    try:
        service = _build_publication_service(conn)
        version = service.return_to_review(version_id)
        return _version_dto(version, service.store.current_head(version.media_id), _scheme_display_lookup(conn, [version.scheme_id]))
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except (ContractValidationError, StoreConflictError):
        raise HTTPException(status_code=409, detail="当前版本不能退回审核")
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
            version=_version_dto(version, service.store.current_head(version.media_id), _scheme_display_lookup(conn, [version.scheme_id])),
            job=_publication_job_dto(job),
            reused=bool(result["reused"]),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="转录版本不存在")
    except PromptEchoPublicationBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.message)
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
