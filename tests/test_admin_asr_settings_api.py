from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_admin_asr
from api.auth import require_admin, require_csrf_admin
from api.db import connect, get_db, init_db
from src.transcription.asr_service_contract import (
    ASR_API_VERSION,
    ASR_PROFILE_IDENTITIES_SCHEMA_VERSION,
    ServiceCapabilities,
    ServiceProfileIdentities,
    ServiceProfileIdentity,
)
from src.transcription.profile_catalog import (
    WHISPERX_BALANCED_PROFILE_ID,
    WHISPERX_V2_SERVICE_PROFILE_ID,
)
from src.transcription.service_profiles import WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG


def _route(path: str, method: str):
    return next(
        item
        for item in routes_admin_asr.router.routes
        if item.path == path and method in item.methods
    )


def _dependencies(path: str, method: str) -> set[object]:
    return {item.call for item in _route(path, method).dependant.dependencies}


def _runtime_state(*, config_hash: str | None = None):
    service_hash = config_hash or WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG.config_hash
    capabilities = ServiceCapabilities(
        ASR_API_VERSION,
        (WHISPERX_V2_SERVICE_PROFILE_ID,),
        16 * 1024**2,
        32 * 1024**2,
    )
    diagnostics = {
        "enabled": True,
        "queue_depth": 0,
        "queue_limit": 8,
        "pause_reason": None,
        "profiles": [
            {
                "service_profile_id": WHISPERX_V2_SERVICE_PROFILE_ID,
                "available": True,
                "unavailable_reason_code": None,
            }
        ],
    }
    identities = ServiceProfileIdentities(
        ASR_PROFILE_IDENTITIES_SCHEMA_VERSION,
        (
            ServiceProfileIdentity(
                WHISPERX_V2_SERVICE_PROFILE_ID,
                "whisperx",
                service_hash,
                "asr_engineering_zh_v2",
                "whisperx-r3/1",
            ),
        ),
    )
    return capabilities, diagnostics, identities


@pytest.fixture
def asr_api(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = connect(path)
    now = int(time.time())
    conn.executemany(
        """INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES (?,?,?,?,?,1,?)""",
        [
            (1, "admin", "合成管理员", "hash", "admin", now),
            (2, "member", "合成成员", "hash", "user", now),
        ],
    )
    conn.executemany(
        "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
        [
            ("admin-session", 1, "admin-csrf", now, now + 3600),
            ("member-session", 2, "member-csrf", now, now + 3600),
        ],
    )
    conn.commit()
    conn.close()

    app = FastAPI()
    app.include_router(routes_admin_asr.router, prefix="/api")

    def db_override():
        request_conn = connect(path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app.dependency_overrides[get_db] = db_override
    monkeypatch.setattr(routes_admin_asr, "ASR_ENABLED", True)
    monkeypatch.setattr(routes_admin_asr, "TRANSCRIPTION_ADMITTED_PROFILE_IDS", ())
    monkeypatch.setattr(routes_admin_asr, "_runtime_state", _runtime_state)
    with TestClient(app) as client:
        yield client, path


def _auth(user: str, *, csrf: bool = False) -> dict[str, object]:
    token = "admin-csrf" if user == "admin" else "member-csrf"
    return {
        "cookies": {"pc_sid": f"{user}-session"},
        "headers": {"X-CSRF-Token": token} if csrf else {},
    }


def _request_body(*, reason: str | None = "常规培训视频") -> dict[str, object]:
    return {
        "profile_id": WHISPERX_BALANCED_PROFILE_ID,
        "request_idempotency_key": "11111111-1111-4111-8111-111111111111",
        "request_reason": reason,
    }


def test_routes_enforce_admin_reads_and_csrf_admin_mutations():
    assert require_admin in _dependencies("/admin/asr", "GET")
    assert require_csrf_admin in _dependencies(
        "/admin/asr/release-requests", "POST"
    )


def test_http_auth_csrf_and_runtime_identity_boundaries(asr_api, monkeypatch):
    client, path = asr_api
    assert client.get("/api/admin/asr").status_code == 401
    assert client.get("/api/admin/asr", **_auth("member")).status_code == 403
    assert client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(),
        **_auth("admin"),
    ).status_code == 403
    assert client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(),
        **_auth("member", csrf=True),
    ).status_code == 403

    response = client.get("/api/admin/asr", **_auth("admin"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == {
        "status": "healthy",
        "queue_depth": 0,
        "queue_limit": 8,
        "pause_reason": None,
    }
    assert len(payload["profiles"]) == 3
    assert all(item["release_eligible"] is True for item in payload["profiles"])
    assert "BIM-2026-0805" in payload["profiles"][0]["protected_terms"]

    invalid = _request_body()
    invalid["request_idempotency_key"] = "not-a-uuid"
    assert client.post(
        "/api/admin/asr/release-requests",
        json=invalid,
        **_auth("admin", csrf=True),
    ).status_code == 422

    monkeypatch.setattr(
        routes_admin_asr,
        "_runtime_state",
        lambda: _runtime_state(config_hash="0" * 64),
    )
    mismatch = client.get("/api/admin/asr", **_auth("admin"))
    assert mismatch.status_code == 200
    assert all(item["release_eligible"] is False for item in mismatch.json()["profiles"])
    denied = client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(),
        **_auth("admin", csrf=True),
    )
    assert denied.status_code == 409
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM asr_profile_release_requests").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM asr_profile_audit_events").fetchone()[0] == 0
    conn.close()


def test_release_request_is_transactional_idempotent_and_replays_snapshot(asr_api, monkeypatch):
    client, path = asr_api
    created = client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(),
        **_auth("admin", csrf=True),
    )
    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["profile_display_name"] == "WhisperX 工程转录 均衡分段 v2"

    monkeypatch.setattr(
        routes_admin_asr,
        "_runtime_state",
        lambda: (None, None, None),
    )
    replayed = client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(),
        **_auth("admin", csrf=True),
    )
    assert replayed.status_code == 200
    assert replayed.json() == created_payload

    conflict = client.post(
        "/api/admin/asr/release-requests",
        json=_request_body(reason="另一项发布内容"),
        **_auth("admin", csrf=True),
    )
    assert conflict.status_code == 409

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM asr_profile_release_requests").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asr_profile_audit_events").fetchone()[0] == 1
    row = conn.execute(
        "SELECT profile_snapshot_json FROM asr_profile_release_requests"
    ).fetchone()
    snapshot = json.loads(row["profile_snapshot_json"])
    assert snapshot["decode"]["prompt_asset_id"] == "asr_engineering_zh_v2"
    assert "initial_prompt" not in snapshot["decode"]
    conn.close()
