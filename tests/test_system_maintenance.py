import sqlite3

import pytest

from api import maintenance
from api.db import connect, init_db


@pytest.fixture
def maintenance_db(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(maintenance, "connect", lambda: connect(path))
    conn = connect(path)
    conn.execute(
        """INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES (1,'admin','管理员','x','admin',1,1)"""
    )
    conn.commit()
    conn.close()
    return path


def seed_expired(path, *, now=4_000_000):
    conn = connect(path)
    conn.execute(
        "INSERT INTO conversations(id,user_id,title,created_at,updated_at) VALUES ('old',1,'旧对话',1,?)",
        (now - 31 * 86400,),
    )
    conn.execute(
        "INSERT INTO conversations(id,user_id,title,created_at,updated_at) VALUES ('recent',1,'近期对话',1,?)",
        (now - 29 * 86400,),
    )
    conn.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES ('old','user','q',1)")
    conn.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES ('old','assistant','a',1)")
    conn.execute("INSERT INTO auth_sessions VALUES ('expired',1,'csrf',1,?)", (now - 1,))
    conn.execute("INSERT INTO auth_sessions VALUES ('active',1,'csrf',1,?)", (now + 1,))
    conn.commit()
    conn.close()


def test_default_policy_and_preview_are_read_only(maintenance_db):
    seed_expired(maintenance_db)
    settings = maintenance.get_settings()
    preview = maintenance.preview_cleanup(now=4_000_000)

    assert settings.conversation_cleanup_enabled is True
    assert settings.conversation_retention_days == 30
    assert settings.updated_at is None
    assert (preview.conversations, preview.messages, preview.auth_sessions) == (1, 2, 1)
    conn = connect(maintenance_db)
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 2
    conn.close()


def test_saved_policy_validates_bounds_and_does_not_delete(maintenance_db):
    saved = maintenance.save_settings(enabled=True, retention_days=90, updated_by=1)
    assert saved.conversation_retention_days == 90
    assert saved.updated_by == 1
    with pytest.raises(ValueError, match="invalid_retention_days"):
        maintenance.save_settings(enabled=True, retention_days=6, updated_by=1)


def test_permanent_retention_skips_conversations_but_clears_auth(maintenance_db):
    seed_expired(maintenance_db)
    saved = maintenance.save_settings(enabled=True, retention_days=None, updated_by=1)
    assert saved.conversation_retention_days is None
    preview = maintenance.preview_cleanup(now=4_000_000)
    assert (preview.conversations, preview.messages, preview.auth_sessions) == (0, 0, 1)
    result = maintenance.run_cleanup(trigger_source="automatic", now=4_000_000)
    assert result.retention_days is None
    conn = connect(maintenance_db)
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1
    conn.close()


def test_automatic_cleanup_respects_disabled_conversations_but_clears_auth(maintenance_db):
    seed_expired(maintenance_db)
    maintenance.save_settings(enabled=False, retention_days=30, updated_by=1)

    result = maintenance.run_cleanup(trigger_source="automatic", now=4_000_000)

    assert result.deleted_conversations == 0
    assert result.deleted_messages == 0
    assert result.deleted_auth_sessions == 1
    assert maintenance.list_runs()[0]["trigger_source"] == "automatic"
    conn = connect(maintenance_db)
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 2
    conn.close()


def test_manual_cleanup_uses_saved_policy_and_cascades_messages(maintenance_db):
    seed_expired(maintenance_db)
    maintenance.save_settings(enabled=False, retention_days=30, updated_by=1)

    result = maintenance.run_cleanup(trigger_source="manual", now=4_000_000)

    assert (result.deleted_conversations, result.deleted_messages, result.deleted_auth_sessions) == (1, 2, 1)
    conn = connect(maintenance_db)
    assert [row["id"] for row in conn.execute("SELECT id FROM conversations").fetchall()] == ["recent"]
    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    conn.close()
