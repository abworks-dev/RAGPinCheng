import sqlite3
from pathlib import Path

import pytest

from api import db as app_db
from api import db_migrations, prompt_store
from src import prompts as prompt_loader
from src.prompts import load_prompt


def _fresh_db(tmp_path: Path) -> Path:
    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO users (employee_id, real_name, password_hash, role, is_active, created_at) "
        "VALUES ('admin-test', '测试管理员', 'x', 'admin', 1, strftime('%s','now'))"
    )
    conn.commit()
    conn.close()
    return path


ADMIN_USER_ID = 1


def _fresh_db_without_users(tmp_path: Path) -> Path:
    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    return path


def _force_db(path: Path) -> None:
    prompt_store._FORCED_PROMPT_DB_PATH = path  # type: ignore[attr-defined]


def test_migration_42_creates_system_prompts_table(tmp_path):
    path = _fresh_db(tmp_path)
    conn = sqlite3.connect(path)
    version = conn.execute(
        "SELECT max(version) FROM app_schema_migrations"
    ).fetchone()[0]
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert version == db_migrations.CURRENT_SCHEMA_VERSION
    assert "system_prompts" in tables


def test_seed_prompts_inserts_default_bodies_from_packaged_files(tmp_path):
    path = _fresh_db(tmp_path)
    _force_db(path)
    try:
        prompt_loader.clear_prompt_override_cache()
        seeded = prompt_store.seed_prompts()
        assert seeded >= 10  # every packaged prompt key
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT key, default_body, custom_body FROM system_prompts ORDER BY key"
        ).fetchall()
        conn.close()
        keys = {row[0] for row in rows}
        assert "answer_system" in keys and "table_summary_user" in keys
        for row in rows:
            assert row[1]  # default body non-empty
            assert row[2] is None  # no custom on seed
        # Idempotent
        assert prompt_store.seed_prompts() == 0
    finally:
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.clear_prompt_override_cache()


def test_get_active_prompt_prefers_custom_body(tmp_path):
    path = _fresh_db(tmp_path)
    _force_db(path)
    try:
        prompt_loader.clear_prompt_override_cache()
        prompt_store.seed_prompts()
        default = load_prompt("answer_system")
        assert prompt_store.get_active_prompt("answer_system") == default

        prompt_store.update_prompt("answer_system", custom_body="自定义回答规则", updated_by=ADMIN_USER_ID)
        assert prompt_store.get_active_prompt("answer_system") == "自定义回答规则"

        prompt_store.restore_prompt("answer_system", updated_by=ADMIN_USER_ID)
        assert prompt_store.get_active_prompt("answer_system") == default
    finally:
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.clear_prompt_override_cache()


def test_prompt_override_layer_uses_store_when_registered(tmp_path):
    path = _fresh_db(tmp_path)
    _force_db(path)
    try:
        prompt_loader.clear_prompt_override_cache()
        prompt_store.seed_prompts()
        prompt_loader.register_prompt_override(prompt_store.get_active_prompt)
        try:
            # Registered loader returns stored default (== packaged content).
            assert load_prompt("answer_system") == prompt_store.get_active_prompt("answer_system")
            prompt_store.update_prompt("answer_system", custom_body="管理面板自定义", updated_by=ADMIN_USER_ID)
            prompt_loader.clear_prompt_override_cache()
            assert load_prompt("answer_system") == "管理面板自定义"
            prompt_store.restore_prompt("answer_system", updated_by=ADMIN_USER_ID)
            prompt_loader.clear_prompt_override_cache()
            assert load_prompt("answer_system") == prompt_store.get_active_prompt("answer_system")
        finally:
            prompt_loader.register_prompt_override(None)
            prompt_loader.clear_prompt_override_cache()
    finally:
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.clear_prompt_override_cache()


def test_update_unknown_prompt_key_raises(tmp_path):
    path = _fresh_db(tmp_path)
    _force_db(path)
    try:
        with pytest.raises(KeyError):
            prompt_store.update_prompt("missing_prompt", custom_body="x", updated_by=ADMIN_USER_ID)
        with pytest.raises(KeyError):
            prompt_store.restore_prompt("missing_prompt", updated_by=ADMIN_USER_ID)
    finally:
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.clear_prompt_override_cache()


def test_load_prompt_without_override_uses_packaged_file(tmp_path):
    _force_db(tmp_path)  # unrelated temp path; loader not registered
    try:
        prompt_loader.register_prompt_override(None)
        prompt_loader.clear_prompt_override_cache()
        text = load_prompt("answer_system")
        assert "BIM" in text or "回答" in text
    finally:
        prompt_loader.clear_prompt_override_cache()


def _app_with_db(tmp_path: Path, monkeypatch):
    """Build a FastAPI app with the prompts router + seeded temp DB."""
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import db as app_db
    from api import routes_prompts
    from api.db import get_db
    from api.db import connect as db_connect

    path = _fresh_db_without_users(tmp_path)
    _force_db(path)
    prompt_loader.clear_prompt_override_cache()
    prompt_store.seed_prompts()

    conn = db_connect(path)
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
    app.include_router(routes_prompts.router, prefix="/api")

    def db_override():
        request_conn = db_connect(path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    return client


def _auth(user: str, *, csrf: bool = False) -> dict[str, object]:
    token = "admin-csrf" if user == "admin" else "member-csrf"
    return {
        "cookies": {"pc_sid": f"{user}-session"},
        "headers": {"X-CSRF-Token": token} if csrf else {},
    }


def test_prompts_api_enforces_admin_and_csrf(tmp_path, monkeypatch):
    client = _app_with_db(tmp_path, monkeypatch)
    try:
        assert client.get("/api/admin/prompts").status_code == 401
        assert client.get("/api/admin/prompts", **_auth("member")).status_code == 403
        assert client.put(
            "/api/admin/prompts/answer_system", json={"custom_body": "x"}, **_auth("admin")
        ).status_code == 403
        assert client.post(
            "/api/admin/prompts/answer_system/restore", **_auth("admin")
        ).status_code == 403
    finally:
        client.close()
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.register_prompt_override(None)
        prompt_loader.clear_prompt_override_cache()


def test_prompts_api_list_update_restore(tmp_path, monkeypatch):
    client = _app_with_db(tmp_path, monkeypatch)
    try:
        items = client.get("/api/admin/prompts", **_auth("admin")).json()
        keys = {item["key"] for item in items}
        assert "answer_system" in keys and "asr_engineering_zh_v2" in keys

        updated = client.put(
            "/api/admin/prompts/answer_system",
            json={"custom_body": "管理面板自定义"},
            **_auth("admin", csrf=True),
        )
        assert updated.status_code == 200
        assert updated.json()["custom_body"] == "管理面板自定义"
        assert updated.json()["updated_by"] == 1

        restored = client.post(
            "/api/admin/prompts/answer_system/restore",
            **_auth("admin", csrf=True),
        )
        assert restored.status_code == 200
        assert restored.json()["custom_body"] is None
        assert restored.json()["default_body"] == load_prompt("answer_system")

        missing = client.put(
            "/api/admin/prompts/does-not-exist",
            json={"custom_body": "x"},
            **_auth("admin", csrf=True),
        )
        assert missing.status_code == 404
    finally:
        client.close()
        prompt_store._FORCED_PROMPT_DB_PATH = None  # type: ignore[attr-defined]
        prompt_loader.register_prompt_override(None)
        prompt_loader.clear_prompt_override_cache()