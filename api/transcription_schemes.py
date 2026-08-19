from __future__ import annotations

import json
import time
import uuid
from typing import Any

from src.transcription.scheme import SchemeValidationError, canonical_parameters, parameters_hash
from src.transcription.profile_catalog import (
    FASTER_WHISPER_PROFILE_ID,
    FUNASR_SENSEVOICE_PROFILE_ID,
    WHISPERX_BALANCED_PROFILE_ID,
)


SYSTEM_SCHEMES = (
    ("funasr-sensevoice-zh-experimental-v1", "SenseVoice 快速中文", "SenseVoice 中文快速转录", "sensevoice-v1", {"segmentation_preset": "natural", "max_duration_ms": None, "max_chars": 500, "merge_gap_ms": 1000}),
    ("faster-whisper-zh-experimental-v1", "faster-whisper 工程术语", "固定工程术语与服务端解码预设", "faster-whisper-v1", {"segmentation_preset": "natural", "max_duration_ms": None, "max_chars": 500, "merge_gap_ms": 1000}),
    ("whisperx-large-v3-zh-natural-v2", "WhisperX 自然分段", "WhisperX v2 自然分段", "whisperx-v2", {"segmentation_preset": "natural", "max_duration_ms": None, "max_chars": 500, "merge_gap_ms": 1000}),
    ("whisperx-large-v3-zh-balanced-v2", "WhisperX 均衡分段", "WhisperX v2 均衡分段", "whisperx-v2", {"segmentation_preset": "balanced", "max_duration_ms": 30000, "max_chars": 500, "merge_gap_ms": 750}),
    ("whisperx-large-v3-zh-fine-v2", "WhisperX 精细分段", "WhisperX v2 精细分段", "whisperx-v2", {"segmentation_preset": "fine", "max_duration_ms": 15000, "max_chars": 240, "merge_gap_ms": 500}),
)


_BASE_RUNTIME_PROFILE_IDS = {
    "sensevoice-v1": FUNASR_SENSEVOICE_PROFILE_ID,
    "faster-whisper-v1": FASTER_WHISPER_PROFILE_ID,
    "whisperx-v2": WHISPERX_BALANCED_PROFILE_ID,
}


def _now() -> int:
    return int(time.time())


def _row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["parameters"] = json.loads(item.pop("config_json"))
    item["enabled"] = bool(item["enabled"])
    item["archived"] = bool(item["archived"])
    item["system_preset"] = bool(item["system_preset"])
    return item


def list_schemes(conn, *, include_archived: bool = True) -> list[dict[str, Any]]:
    where = "" if include_archived else " WHERE archived=0"
    rows = conn.execute("SELECT * FROM transcription_schemes" + where + " ORDER BY sort_order,id").fetchall()
    return [_row(row) for row in rows]


def get_scheme(conn, scheme_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM transcription_schemes WHERE id=?", (scheme_id,)).fetchone()
    return None if row is None else _row(row)


def resolve_scheme_runtime(conn, scheme_id: str) -> tuple[dict[str, Any], str]:
    scheme = get_scheme(conn, scheme_id)
    if scheme is None or scheme["archived"] or not scheme["enabled"]:
        raise SchemeValidationError("transcription scheme is unavailable")
    base = conn.execute(
        "SELECT admission,availability FROM transcription_bases WHERE id=?",
        (scheme["base_id"],),
    ).fetchone()
    if (
        base is None
        or base["admission"] != "enabled"
        or base["availability"] != "runtime"
    ):
        raise SchemeValidationError("transcription base is not admitted")
    try:
        runtime_profile_id = _BASE_RUNTIME_PROFILE_IDS[scheme["base_id"]]
    except KeyError as exc:
        raise SchemeValidationError("unknown transcription base runtime") from exc
    return scheme, runtime_profile_id


def available_schemes(conn) -> list[dict[str, Any]]:
    return [item for item in list_schemes(conn, include_archived=False) if item["enabled"]]


def _audit(conn, scheme_id: str, actor_id: int | None, event_type: str, payload: dict[str, Any], now: int) -> None:
    conn.execute("INSERT INTO transcription_scheme_audit_events(scheme_id,event_type,actor_user_id,event_json,created_at) VALUES (?,?,?,?,?)", (scheme_id, event_type, actor_id, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), now))


def create_scheme(conn, *, name: str, description: str, base_id: str, parameters: dict[str, Any], actor_id: int | None, system_preset: bool = False, source_id: str | None = None) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not 1 <= len(name) <= 120 or len(description) > 500:
        raise SchemeValidationError("invalid scheme name or description")
    base = conn.execute(
        "SELECT admission,availability FROM transcription_bases WHERE id=?", (base_id,)
    ).fetchone()
    if base is None:
        raise SchemeValidationError("unknown transcription base")
    if base["admission"] != "enabled" or base["availability"] == "disabled":
        raise SchemeValidationError("transcription base is not admitted")
    normalized = canonical_parameters(parameters)
    scheme_id = str(uuid.uuid4())
    now = _now()
    order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM transcription_schemes").fetchone()[0]
    conn.execute("INSERT INTO transcription_schemes(id,name,description,base_id,config_json,config_hash,enabled,archived,system_preset,sort_order,version,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,1,0,?,?,?,?,?,?,?)", (scheme_id, name, description, base_id, json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")), parameters_hash(normalized), int(system_preset), order, 1, actor_id, actor_id, now, now))
    _audit(conn, scheme_id, actor_id, "created", {"source_id": source_id}, now)
    return get_scheme(conn, scheme_id)


def update_scheme(conn, scheme_id: str, *, name: str | None, description: str | None, parameters: dict[str, Any] | None, enabled: bool | None, archived: bool | None, expected_version: int, actor_id: int | None) -> dict[str, Any]:
    current = get_scheme(conn, scheme_id)
    if current is None:
        raise KeyError(scheme_id)
    if current["version"] != expected_version:
        raise RuntimeError("scheme_version_conflict")
    if current["system_preset"] and (parameters is not None or archived is True):
        raise SchemeValidationError("system preset core cannot be changed")
    next_name = current["name"] if name is None else name.strip()
    next_description = current["description"] if description is None else description.strip()
    next_params = current["parameters"] if parameters is None else canonical_parameters(parameters)
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    next_archived = current["archived"] if archived is None else bool(archived)
    now = _now()
    changed = conn.execute("UPDATE transcription_schemes SET name=?,description=?,config_json=?,config_hash=?,enabled=?,archived=?,version=version+1,updated_by=?,updated_at=? WHERE id=? AND version=?", (next_name, next_description, json.dumps(next_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")), parameters_hash(next_params), int(next_enabled), int(next_archived), actor_id, now, scheme_id, expected_version)).rowcount
    if changed != 1:
        raise RuntimeError("scheme_version_conflict")
    _audit(conn, scheme_id, actor_id, "updated", {"version": expected_version + 1}, now)
    return get_scheme(conn, scheme_id)


def reorder_schemes(conn, order: list[dict[str, Any]], *, expected_version: int | None, actor_id: int | None) -> list[dict[str, Any]]:
    current = list_schemes(conn)
    if len(order) != len(current) or {item.get("id") for item in order} != {item["id"] for item in current}:
        raise SchemeValidationError("order must include every scheme")
    now = _now()
    for index, item in enumerate(order):
        row = next(row for row in current if row["id"] == item["id"])
        item_version = item.get("expected_version") or expected_version
        if item_version is None or row["version"] != item_version:
            raise RuntimeError("scheme_version_conflict")
        conn.execute("UPDATE transcription_schemes SET sort_order=?,version=version+1,updated_by=?,updated_at=? WHERE id=?", (index, actor_id, now, row["id"]))
        _audit(conn, row["id"], actor_id, "reordered", {"sort_order": index}, now)
    return list_schemes(conn)
