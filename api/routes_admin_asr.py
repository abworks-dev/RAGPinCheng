"""System-admin view of immutable transcription profiles and release requests."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException

from services.asr_service.engine_protocol import (
    FASTER_WHISPER_SERVICE_CONFIG,
    QWEN3_ASR_SERVICE_CONFIG,
    SENSEVOICE_SERVICE_CONFIG,
    WHISPERX_FULL_DECODE_SERVICE_CONFIG,
    WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG,
    ServiceProfileConfig,
)
from src.config import (
    ASR_CONNECT_TIMEOUT_SECONDS,
    ASR_ENABLED,
    ASR_REQUEST_TIMEOUT_SECONDS,
    ASR_SERVICE_TOKEN,
    ASR_SERVICE_URL,
    TRANSCRIPTION_ADMITTED_PROFILE_IDS,
)
from src.transcription.profile_catalog import (
    FUNASR_SENSEVOICE_PROVIDER_KEY,
)
from src.transcription.terminology import BIM_ENGINEERING_TERMS_V1
from src.transcription.types import (
    ProfileQualification,
    ProviderAvailability,
    canonical_json_bytes,
    validate_uuid,
)

from .auth import CurrentUser, require_admin, require_csrf_admin
from .db import get_db
from .schemas import (
    AsrDecodeConfigDTO,
    AsrManagedProfileDTO,
    AsrProfileAuditEventDTO,
    AsrProfileReleaseRequestCreate,
    AsrProfileReleaseRequestDTO,
    AsrSegmentationDTO,
    AsrServiceStatusDTO,
    AsrSettingsResponse,
)
from .transcription_runtime import (
    RemoteAsrProviderFactory,
    build_phase4_profile_catalog,
)


router = APIRouter(prefix="/admin/asr", tags=["admin-asr"])

_SERVICE_CONFIGS: tuple[ServiceProfileConfig, ...] = (
    FASTER_WHISPER_SERVICE_CONFIG,
    SENSEVOICE_SERVICE_CONFIG,
    QWEN3_ASR_SERVICE_CONFIG,
    WHISPERX_FULL_DECODE_SERVICE_CONFIG,
    WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG,
)
_SERVICE_CONFIG_BY_ID = {
    item.service_profile_id: item for item in _SERVICE_CONFIGS
}


def _runtime_state() -> tuple[object | None, dict[str, object] | None]:
    if not ASR_ENABLED or not ASR_SERVICE_TOKEN:
        return None, None
    factory = RemoteAsrProviderFactory(
        ASR_SERVICE_URL,
        ASR_SERVICE_TOKEN,
        ASR_CONNECT_TIMEOUT_SECONDS,
        ASR_REQUEST_TIMEOUT_SECONDS,
        FUNASR_SENSEVOICE_PROVIDER_KEY,
    )
    try:
        return factory.capabilities(), factory.diagnostics()
    except Exception:
        return None, None


def _diagnostic_profiles(diagnostics: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if diagnostics is None or type(diagnostics.get("profiles")) is not list:
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in diagnostics["profiles"]:
        if type(item) is not dict:
            continue
        profile_id = item.get("service_profile_id")
        if type(profile_id) is not str or profile_id not in _SERVICE_CONFIG_BY_ID:
            continue
        result[profile_id] = item
    return result


def _service_status(diagnostics: dict[str, object] | None) -> AsrServiceStatusDTO:
    if not ASR_ENABLED:
        return AsrServiceStatusDTO(status="disabled")
    if diagnostics is None:
        return AsrServiceStatusDTO(status="unavailable")
    queue_depth = diagnostics.get("queue_depth")
    queue_limit = diagnostics.get("queue_limit")
    pause_reason = diagnostics.get("pause_reason")
    profiles = _diagnostic_profiles(diagnostics)
    all_available = bool(profiles) and all(item.get("available") is True for item in profiles.values())
    return AsrServiceStatusDTO(
        status="healthy" if diagnostics.get("enabled") is True and all_available and pause_reason is None else "degraded",
        queue_depth=queue_depth if type(queue_depth) is int and queue_depth >= 0 else None,
        queue_limit=queue_limit if type(queue_limit) is int and queue_limit > 0 else None,
        pause_reason=pause_reason if type(pause_reason) is str else None,
    )


def _profile_dtos(
    capabilities: object | None,
    diagnostics: dict[str, object] | None,
) -> list[AsrManagedProfileDTO]:
    entries = build_phase4_profile_catalog(
        service_enabled=ASR_ENABLED,
        service_healthy=capabilities is not None,
        service_capabilities=capabilities,
        admitted_profile_ids=TRANSCRIPTION_ADMITTED_PROFILE_IDS,
    )
    runtime_profiles = _diagnostic_profiles(diagnostics)
    result: list[AsrManagedProfileDTO] = []
    for entry in entries:
        profile = entry.profile
        if profile.qualification is not ProfileQualification.qualification_approved:
            continue
        service_config = _SERVICE_CONFIG_BY_ID[profile.provider_config.service_profile_id]
        runtime = runtime_profiles.get(service_config.service_profile_id)
        runtime_hash = None
        if runtime is not None and type(runtime.get("profile_config_hash")) is str:
            runtime_hash = runtime["profile_config_hash"]
        runtime_identity_matches = (
            runtime is not None
            and runtime.get("available") is True
            and runtime_hash == service_config.config_hash
        )
        segmentation = profile.segmentation_config
        result.append(
            AsrManagedProfileDTO(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                description=profile.description,
                profile_version=profile.profile_definition_version,
                application_config_hash=profile.config_hash,
                qualification=profile.qualification.value,
                admission=profile.admission.value,
                availability=entry.availability.value,
                unavailable_reason_code=entry.unavailable_reason_code,
                release_eligible=(
                    profile.qualification is ProfileQualification.qualification_approved
                    and entry.availability is ProviderAvailability.available
                    and runtime_identity_matches
                ),
                segmentation=(
                    None
                    if segmentation is None
                    else AsrSegmentationDTO(**segmentation.to_json_dict())
                ),
                terminology_rule_set=(
                    None
                    if profile.terminology_config is None
                    else profile.terminology_config.rule_set_id
                ),
                protected_terms=(
                    list(BIM_ENGINEERING_TERMS_V1)
                    if profile.terminology_config is not None
                    else []
                ),
                decode=AsrDecodeConfigDTO(
                    service_profile_id=service_config.service_profile_id,
                    model_name=(
                        "Whisper large-v3 + 中文对齐"
                        if service_config.provider_key == "whisperx"
                        else service_config.model_id
                    ),
                    beam_size=service_config.beam_size,
                    temperature=service_config.temperature,
                    hotword_count=len(service_config.hotwords),
                    prompt_asset_id=service_config.prompt_asset_id or None,
                    service_profile_config_hash=runtime_hash,
                    qualification_policy=(
                        runtime.get("qualification_policy")
                        if runtime is not None and type(runtime.get("qualification_policy")) is str
                        else None
                    ),
                ),
            )
        )
    return result


def _release_requests(
    conn: sqlite3.Connection,
    profile_names: dict[str, str],
) -> list[AsrProfileReleaseRequestDTO]:
    rows = conn.execute(
        """SELECT r.*, u.real_name AS requested_by_name
           FROM asr_profile_release_requests r
           LEFT JOIN users u ON u.id=r.requested_by
           ORDER BY r.created_at DESC, r.id DESC LIMIT 50"""
    ).fetchall()
    return [
        _release_request_dto(row, profile_names)
        for row in rows
    ]


def _release_request_dto(
    row: sqlite3.Row,
    profile_names: dict[str, str],
) -> AsrProfileReleaseRequestDTO:
    display_name = profile_names.get(row["profile_id"])
    if display_name is None:
        try:
            snapshot = json.loads(row["profile_snapshot_json"])
            stored_name = snapshot.get("display_name") if type(snapshot) is dict else None
            display_name = stored_name if type(stored_name) is str and stored_name else None
        except (json.JSONDecodeError, TypeError):
            display_name = None
    return AsrProfileReleaseRequestDTO(
            request_id=row["id"],
            profile_id=row["profile_id"],
            profile_display_name=display_name or "历史转录配置",
            profile_config_hash=row["profile_config_hash"],
            status=row["status"],
            request_reason=row["request_reason"],
            requested_by_name=row["requested_by_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _audit_events(
    conn: sqlite3.Connection,
    profile_names: dict[str, str],
) -> list[AsrProfileAuditEventDTO]:
    rows = conn.execute(
        """SELECT e.*, u.real_name AS actor_name
           FROM asr_profile_audit_events e
           LEFT JOIN users u ON u.id=e.actor_user_id
           ORDER BY e.created_at DESC, e.id DESC LIMIT 50"""
    ).fetchall()
    return [
        AsrProfileAuditEventDTO(
            event_id=row["id"],
            event_type=row["event_type"],
            profile_id=row["profile_id"],
            profile_display_name=profile_names.get(row["profile_id"], "历史转录配置"),
            actor_name=row["actor_name"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def _settings_response(conn: sqlite3.Connection) -> AsrSettingsResponse:
    capabilities, diagnostics = _runtime_state()
    profiles = _profile_dtos(capabilities, diagnostics)
    profile_names = {item.profile_id: item.display_name for item in profiles}
    return AsrSettingsResponse(
        service=_service_status(diagnostics),
        profiles=profiles,
        release_requests=_release_requests(conn, profile_names),
        audit_events=_audit_events(conn, profile_names),
    )


@router.get("", response_model=AsrSettingsResponse)
def get_asr_settings(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AsrSettingsResponse:
    return _settings_response(conn)


@router.post("/release-requests", response_model=AsrProfileReleaseRequestDTO)
def create_release_request(
    body: AsrProfileReleaseRequestCreate,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AsrProfileReleaseRequestDTO:
    try:
        validate_uuid(body.request_idempotency_key, "request_idempotency_key")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="发布申请幂等键不合法") from exc
    reason = (body.request_reason or "").strip() or None
    existing = conn.execute(
        """SELECT r.*, u.real_name AS requested_by_name
           FROM asr_profile_release_requests r
           LEFT JOIN users u ON u.id=r.requested_by
           WHERE r.idempotency_key=?""",
        (body.request_idempotency_key,),
    ).fetchone()
    if existing is not None:
        if existing["profile_id"] != body.profile_id or existing["request_reason"] != reason:
            raise HTTPException(status_code=409, detail="发布申请幂等键已用于其他内容")
        names = {
            item.profile_id: item.display_name
            for item in _profile_dtos(*_runtime_state())
        }
        return _release_request_dto(existing, names)

    capabilities, diagnostics = _runtime_state()
    profiles = _profile_dtos(capabilities, diagnostics)
    profile = next((item for item in profiles if item.profile_id == body.profile_id), None)
    if profile is None:
        raise HTTPException(status_code=404, detail="转录配置不存在")
    if not profile.release_eligible:
        raise HTTPException(status_code=409, detail="该转录配置尚未通过当前运行版本的发布门禁")

    request_id = str(uuid.uuid4())
    now = int(time.time())
    snapshot = profile.model_dump(mode="json")
    event = {
        "schema_version": "asr-profile-audit/1",
        "action": "release_requested",
        "profile_id": profile.profile_id,
        "profile_config_hash": profile.application_config_hash,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT r.*, u.real_name AS requested_by_name
               FROM asr_profile_release_requests r
               LEFT JOIN users u ON u.id=r.requested_by
               WHERE r.idempotency_key=?""",
            (body.request_idempotency_key,),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            if existing["profile_id"] != body.profile_id or existing["request_reason"] != reason:
                raise HTTPException(status_code=409, detail="发布申请幂等键已用于其他内容")
            return _release_request_dto(
                existing,
                {item.profile_id: item.display_name for item in profiles},
            )
        conn.execute(
            """INSERT INTO asr_profile_release_requests(
                   id,idempotency_key,profile_id,profile_config_hash,profile_snapshot_json,
                   status,request_reason,requested_by,created_at,updated_at
               ) VALUES (?,?,?,?,?,'requested',?,?,?,?)""",
            (
                request_id,
                body.request_idempotency_key,
                profile.profile_id,
                profile.application_config_hash,
                canonical_json_bytes(snapshot).decode("utf-8"),
                reason,
                admin.id,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO asr_profile_audit_events(
                   event_type,release_request_id,profile_id,profile_config_hash,
                   actor_user_id,event_json,created_at
               ) VALUES ('release_requested',?,?,?,?,?,?)""",
            (
                request_id,
                profile.profile_id,
                profile.application_config_hash,
                admin.id,
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return AsrProfileReleaseRequestDTO(
        request_id=request_id,
        profile_id=profile.profile_id,
        profile_display_name=profile.display_name,
        profile_config_hash=profile.application_config_hash,
        status="requested",
        request_reason=reason,
        requested_by_name=admin.real_name,
        created_at=now,
        updated_at=now,
    )
