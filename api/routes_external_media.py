"""Administrative APIs for read-only external media sources."""
from __future__ import annotations

import sqlite3
import time
import uuid
from fastapi import BackgroundTasks
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import ASR_ENABLED, ASR_SERVICE_TOKEN, EXTERNAL_MEDIA_ROOTS, EXTERNAL_MEDIA_UNC_ROOTS, resolve_external_unc_path
from src.transcription.profile import ProfileOperation
from src.transcription.types import ContractValidationError

from .auth import CurrentUser, require_admin, require_csrf_admin
from .db import connect, get_db
from .external_media import ExternalMediaError, due_source_ids, external_request_key, reconcile_source
from .media_storage import normalize_external_relative_path
from .media_upload_conflicts import require_active_category
from .routes_transcription import build_transcription_service
from .schemas import (
    ExternalMediaEnqueueRequest,
    ExternalMediaEnqueuePreview,
    ExternalMediaEnqueuePreviewItem,
    ExternalMediaEnqueueResult,
    ExternalMediaEntryDTO,
    ExternalMediaEntryListDTO,
    ExternalMediaRootDTO,
    ExternalMediaScanDTO,
    ExternalMediaSourceCreate,
    ExternalMediaSourceDTO,
    ExternalMediaSourceUpdate,
)
from .transcription_schemes import available_schemes, resolve_scheme_runtime
from .transcription_store import StoreConflictError
from .transcription_worker import enqueue as enqueue_transcription

router = APIRouter(prefix="/admin/external-media", tags=["external-media"])


def _source_dto(row: sqlite3.Row) -> ExternalMediaSourceDTO:
    return ExternalMediaSourceDTO(
        id=str(row["id"]),
        name=str(row["name"]),
        root_alias=str(row["root_alias"]),
        relative_path=str(row["relative_path"]),
        target_category_id=str(row["target_category_id"]),
        default_scheme_id=str(row["default_scheme_id"]),
        auto_enqueue=bool(row["auto_enqueue"]),
        scan_interval_seconds=int(row["scan_interval_seconds"]),
        enabled=bool(row["enabled"]),
        status=row["status"],
        total_files=int(row["total_files"]),
        available_files=int(row["available_files"]),
        missing_files=int(row["missing_files"]),
        last_scan_at=row["last_scan_at"],
        last_successful_scan_at=row["last_successful_scan_at"],
        last_error_code=row["last_error_code"],
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        version=int(row["version"]),
    )


def _validate_source_targets(conn: sqlite3.Connection, category_id: str, scheme_id: str) -> None:
    try:
        require_active_category(conn, category_id, allow_shared=True)
        resolve_scheme_runtime(conn, scheme_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="目标目录或转录方案当前不可用") from exc


@router.get("/roots", response_model=list[ExternalMediaRootDTO])
def list_external_roots(_admin: CurrentUser = Depends(require_admin)) -> list[ExternalMediaRootDTO]:
    return [ExternalMediaRootDTO(alias=alias) for alias in sorted(EXTERNAL_MEDIA_ROOTS)]


@router.get("/sources", response_model=list[ExternalMediaSourceDTO])
def list_sources(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ExternalMediaSourceDTO]:
    rows = conn.execute("SELECT * FROM external_media_sources ORDER BY created_at,name").fetchall()
    return [_source_dto(row) for row in rows]


@router.post("/sources", response_model=ExternalMediaSourceDTO, status_code=201)
def create_source(
    body: ExternalMediaSourceCreate,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaSourceDTO:
    try:
        if body.unc_path:
            root_alias, unc_relative = resolve_external_unc_path(body.unc_path, EXTERNAL_MEDIA_UNC_ROOTS)
            relative_path = normalize_external_relative_path(unc_relative, allow_empty=True)
        else:
            root_alias = body.root_alias
            if root_alias not in EXTERNAL_MEDIA_ROOTS:
                raise ValueError("external_root_unconfigured")
            relative_path = normalize_external_relative_path(body.relative_path, allow_empty=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="共享目录路径不合法或不在服务端白名单内") from exc
    _validate_source_targets(conn, body.target_category_id, body.default_scheme_id)
    now = int(time.time())
    source_id = str(uuid.uuid4())
    try:
        conn.execute(
            """INSERT INTO external_media_sources(
                   id,name,root_alias,relative_path,target_category_id,default_scheme_id,
                   auto_enqueue,scan_interval_seconds,created_by,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_id,
                body.name.strip(),
                root_alias,
                relative_path,
                body.target_category_id,
                body.default_scheme_id,
                int(body.auto_enqueue),
                body.scan_interval_seconds,
                admin.id,
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail="该共享目录已经登记") from exc
    return _source_dto(conn.execute("SELECT * FROM external_media_sources WHERE id=?", (source_id,)).fetchone())


@router.patch("/sources/{source_id}", response_model=ExternalMediaSourceDTO)
def update_source(
    source_id: str,
    body: ExternalMediaSourceUpdate,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaSourceDTO:
    linked = conn.execute("SELECT id FROM category_nodes WHERE external_source_id=?", (source_id,)).fetchone()
    if linked is not None and body.target_category_id != linked["id"]:
        raise HTTPException(status_code=409, detail="共享文件夹的目标分类不能单独修改")
    _validate_source_targets(conn, body.target_category_id, body.default_scheme_id)
    now = int(time.time())
    changed = conn.execute(
        """UPDATE external_media_sources SET name=?,target_category_id=?,default_scheme_id=?,
                  auto_enqueue=?,scan_interval_seconds=?,enabled=?,updated_at=?,version=version+1
           WHERE id=? AND version=?""",
        (
            body.name.strip(),
            body.target_category_id,
            body.default_scheme_id,
            int(body.auto_enqueue),
            body.scan_interval_seconds,
            int(body.enabled),
            now,
            source_id,
            body.expected_version,
        ),
    ).rowcount
    conn.commit()
    if changed != 1:
        if conn.execute("SELECT 1 FROM external_media_sources WHERE id=?", (source_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="外部媒体源不存在")
        raise HTTPException(status_code=409, detail="外部媒体源已被其他操作更新，请刷新后重试")
    return _source_dto(conn.execute("SELECT * FROM external_media_sources WHERE id=?", (source_id,)).fetchone())


@router.post("/sources/{source_id}/scan", response_model=ExternalMediaScanDTO)
def scan_source(
    source_id: str,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaScanDTO:
    try:
        result = reconcile_source(conn, source_id, trigger_type="manual")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="外部媒体源不存在") from exc
    except ExternalMediaError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc), "message": "共享目录当前不可访问，已保留上次扫描结果。"}) from exc
    return ExternalMediaScanDTO(
        run_id=result.run_id,
        source_id=result.source_id,
        discovered_count=result.discovered_count,
        added_count=result.added_count,
        changed_count=result.changed_count,
        missing_count=result.missing_count,
    )


def _entry_rows(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.*,m.status AS media_status,
                  (SELECT id FROM transcription_jobs j WHERE j.media_id=e.media_id ORDER BY attempt_number DESC LIMIT 1) AS job_id,
                  (SELECT status FROM transcription_jobs j WHERE j.media_id=e.media_id ORDER BY attempt_number DESC LIMIT 1) AS job_status,
                  (SELECT review_status FROM transcript_versions v WHERE v.media_id=e.media_id ORDER BY created_at DESC LIMIT 1) AS review_status,
                  (SELECT publication_status FROM transcript_versions v WHERE v.media_id=e.media_id ORDER BY created_at DESC LIMIT 1) AS publication_status,
                  (SELECT status FROM transcript_publication_index_jobs p
                     JOIN transcript_versions v ON v.id=p.transcript_version_id
                    WHERE v.media_id=e.media_id ORDER BY p.created_at DESC LIMIT 1) AS index_status
           FROM external_media_entries e JOIN media_assets m ON m.media_id=e.media_id
           WHERE e.source_id=? AND e.availability<>'superseded'
           ORDER BY e.relative_path COLLATE NOCASE""",
        (source_id,),
    ).fetchall()


@router.get("/sources/{source_id}/entries", response_model=ExternalMediaEntryListDTO)
def list_entries(
    source_id: str,
    parent: str = Query(default="", max_length=1000),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaEntryListDTO:
    if conn.execute("SELECT 1 FROM external_media_sources WHERE id=?", (source_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="外部媒体源不存在")
    try:
        parent_path = normalize_external_relative_path(parent, allow_empty=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="目录路径不合法") from exc
    prefix = f"{parent_path}/" if parent_path else ""
    folders: dict[str, ExternalMediaEntryDTO] = {}
    videos: list[ExternalMediaEntryDTO] = []
    for row in _entry_rows(conn, source_id):
        relative = str(row["relative_path"])
        if not relative.startswith(prefix):
            continue
        remainder = relative[len(prefix):]
        parts = PurePosixPath(remainder).parts
        if len(parts) > 1:
            folder_path = f"{prefix}{parts[0]}" if prefix else parts[0]
            folders.setdefault(
                parts[0],
                ExternalMediaEntryDTO(id=f"folder:{folder_path}", kind="folder", name=parts[0], relative_path=folder_path),
            )
            continue
        videos.append(
            ExternalMediaEntryDTO(
                id=str(row["id"]),
                kind="video",
                name=str(row["filename"]),
                relative_path=relative,
                file_size=int(row["file_size"]),
                modified_ns=int(row["modified_ns"]),
                availability=row["availability"],
                media_id=str(row["media_id"]),
                media_status=row["media_status"],
                transcription_job_id=row["job_id"],
                transcription_job_status=row["job_status"],
                review_status=row["review_status"],
                publication_status=row["publication_status"],
                index_status=row["index_status"],
            )
        )
    entries = sorted(folders.values(), key=lambda item: item.name.casefold()) + videos
    return ExternalMediaEntryListDTO(source_id=source_id, parent_relative_path=parent_path, entries=entries)


def enqueue_source_entries(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    entry_ids: tuple[str, ...] | None,
    created_by: int,
    background_tasks: BackgroundTasks | None = None,
) -> ExternalMediaEnqueueResult:
    if not ASR_ENABLED or not ASR_SERVICE_TOKEN:
        raise ExternalMediaError("asr_unavailable")
    source = conn.execute("SELECT default_scheme_id FROM external_media_sources WHERE id=?", (source_id,)).fetchone()
    if source is None:
        raise KeyError(source_id)
    scheme_id = str(source["default_scheme_id"])
    try:
        _scheme, profile_id = resolve_scheme_runtime(conn, scheme_id)
        build_transcription_service().resolve_profile(profile_id, ProfileOperation.new_attempt)
    except (ValueError, ContractValidationError):
        profile_id = None
        for candidate in available_schemes(conn):
            try:
                candidate_scheme, candidate_profile = resolve_scheme_runtime(conn, str(candidate["id"]))
                build_transcription_service().resolve_profile(candidate_profile, ProfileOperation.new_attempt)
            except (ValueError, ContractValidationError):
                continue
            scheme_id, profile_id = str(candidate_scheme["id"]), candidate_profile
            conn.execute("UPDATE external_media_sources SET default_scheme_id=?,updated_at=? WHERE id=?", (scheme_id, int(time.time()), source_id))
            conn.commit()
            break
        if profile_id is None:
            raise ExternalMediaError("scheme_unavailable")
    params: list[object] = [source_id]
    identity_filter = ""
    if entry_ids is not None:
        if not entry_ids:
            return ExternalMediaEnqueueResult(requested=0, enqueued=0, failed=0, failures={})
        identity_filter = f" AND e.id IN ({','.join('?' for _ in entry_ids)})"
        params.extend(entry_ids)
    rows = conn.execute(
        f"""SELECT e.id,e.media_id,e.fingerprint FROM external_media_entries e
             JOIN media_assets m ON m.media_id=e.media_id
             WHERE e.source_id=? AND e.availability='available' {identity_filter}
               AND m.status='uploaded'
               AND NOT EXISTS (SELECT 1 FROM transcription_jobs j WHERE j.media_id=e.media_id)
             ORDER BY e.discovered_at,e.id LIMIT 500""",
        params,
    ).fetchall()
    # Reserve selected media before returning. Audio preparation is synchronous
    # inside create_pending_job and must not block the HTTP request for a large
    # SMB tree. The worker below creates each job and records any failure.
    media_ids = [str(row["media_id"]) for row in rows]
    if media_ids:
        conn.executemany(
            "UPDATE media_assets SET status='transcribing',error=NULL,updated_at=? WHERE media_id=? AND status='uploaded'",
            [(int(time.time()), media_id) for media_id in media_ids],
        )
        conn.commit()

    task_args = ([dict(row) for row in rows], str(source_id), scheme_id, str(profile_id), created_by)
    if background_tasks is None:
        process_external_enqueue_rows(*task_args)
    else:
        background_tasks.add_task(process_external_enqueue_rows, *task_args)
    return ExternalMediaEnqueueResult(requested=len(rows), enqueued=len(rows), failed=0, failures={})


def process_external_enqueue_rows(rows: list[dict[str, object]], source_id: str, scheme_id: str, profile_id: str, created_by: int) -> None:
    service = build_transcription_service()
    for row in rows:
        entry_id = str(row["id"])
        request_key = external_request_key(entry_id, str(row["fingerprint"]))
        try:
            job = service.create_pending_job(
                media_id=str(row["media_id"]),
                profile_id=profile_id,
                scheme_id=scheme_id,
                request_idempotency_key=request_key,
                created_by=created_by,
            )
            enqueue_transcription(job.id)
        except (ContractValidationError, StoreConflictError, OSError) as exc:
            failure = str(exc)
            worker_conn = connect()
            try:
                worker_conn.execute("UPDATE media_assets SET status='failed',error=?,updated_at=? WHERE media_id=?", (failure, int(time.time()), row["media_id"]))
                worker_conn.commit()
            finally:
                worker_conn.close()


@router.post("/sources/{source_id}/enqueue", response_model=ExternalMediaEnqueueResult)
def enqueue_entries(
    source_id: str,
    body: ExternalMediaEnqueueRequest,
    background_tasks: BackgroundTasks,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaEnqueueResult:
    try:
        return enqueue_source_entries(
            conn,
            source_id,
            entry_ids=None if body.entry_ids is None else tuple(body.entry_ids),
            created_by=admin.id,
            background_tasks=background_tasks,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="外部媒体源不存在") from exc
    except ExternalMediaError as exc:
        raise HTTPException(status_code=503, detail="自动转录当前不可用或转录方案失效") from exc

@router.post("/sources/{source_id}/enqueue-preview", response_model=ExternalMediaEnqueuePreview)
def preview_enqueue_entries(
    source_id: str,
    body: ExternalMediaEnqueueRequest,
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ExternalMediaEnqueuePreview:
    if conn.execute("SELECT 1 FROM external_media_sources WHERE id=?", (source_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="外部媒体源不存在")
    params: list[object] = [source_id]
    filter_sql = ""
    if body.entry_ids is not None:
        if not body.entry_ids:
            return ExternalMediaEnqueuePreview(items=[], selected_count=0)
        filter_sql = f" AND e.id IN ({','.join('?' for _ in body.entry_ids)})"
        params.extend(body.entry_ids)
    rows = conn.execute(f"""SELECT e.id,e.relative_path,e.file_size,e.modified_ns,
        EXISTS(SELECT 1 FROM transcription_jobs j WHERE j.media_id=e.media_id) AS has_job,
        EXISTS(SELECT 1 FROM transcription_jobs j WHERE j.media_id=e.media_id AND j.audio_sha256<>e.fingerprint) AS changed
        FROM external_media_entries e WHERE e.source_id=? AND e.availability='available' {filter_sql}
        ORDER BY e.relative_path COLLATE NOCASE LIMIT 500""", params).fetchall()
    items = [ExternalMediaEnqueuePreviewItem(entry_id=str(r["id"]), relative_path=str(r["relative_path"]), file_size=int(r["file_size"]), modified_ns=int(r["modified_ns"]), state="updated" if r["changed"] else "already_transcribed" if r["has_job"] else "new", selected=not bool(r["has_job"]) or bool(r["changed"])) for r in rows]
    return ExternalMediaEnqueuePreview(items=items, selected_count=sum(item.selected for item in items))


def run_due_external_scans() -> None:
    conn = connect()
    try:
        for source_id in due_source_ids(conn):
            try:
                result = reconcile_source(conn, source_id, trigger_type="scheduled")
                source = conn.execute(
                    "SELECT auto_enqueue,created_by FROM external_media_sources WHERE id=?", (source_id,)
                ).fetchone()
                if source is not None and source["auto_enqueue"] and source["created_by"] is not None and result.added_entry_ids:
                    outcome = enqueue_source_entries(
                        conn,
                        source_id,
                        entry_ids=result.added_entry_ids,
                        created_by=int(source["created_by"]),
                    )
                    conn.execute(
                        "UPDATE external_media_scan_runs SET enqueued_count=? WHERE id=?",
                        (outcome.enqueued, result.run_id),
                    )
                    conn.commit()
            except (ExternalMediaError, KeyError):
                continue
    finally:
        conn.close()
