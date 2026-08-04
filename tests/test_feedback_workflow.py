from __future__ import annotations

import json

import pytest

from api import db as app_db
from api import feedback


def test_feedback_migration_is_additive(tmp_path):
    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    conn = app_db.connect(path)
    try:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(feedback_workflow)")
        }
        assert {
            "feedback_id", "status", "resolution", "admin_note",
            "assignee_user_id", "updated_at", "resolved_at",
        }.issubset(columns)
        assert conn.execute(
            "SELECT version FROM app_schema_migrations WHERE name = 'feedback_workflow'"
        ).fetchone()[0] == 3
    finally:
        conn.close()


def test_legacy_feedback_ids_are_stable_and_unique_by_line(tmp_path, monkeypatch):
    path = tmp_path / "feedback.jsonl"
    line = json.dumps({"kind": "answer", "rating": "down"}, ensure_ascii=False)
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", path)

    first = feedback.read_records()
    second = feedback.read_records()

    assert [item["feedback_id"] for item in first] == [
        item["feedback_id"] for item in second
    ]
    assert first[0]["feedback_id"] != first[1]["feedback_id"]
    assert all(item["feedback_id"].startswith("legacy-") for item in first)


def test_admin_feedback_filters_and_persists_resolution(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from api import routes_admin
    from api.auth import CurrentUser
    from api.schemas import AdminFeedbackPatchRequest

    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    conn = app_db.connect(path)
    conn.execute(
        """INSERT INTO users(employee_id, real_name, password_hash, role, created_at)
           VALUES ('admin', '管理员', 'unused', 'admin', 1)"""
    )
    admin_id = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.commit()
    admin = CurrentUser(
        id=admin_id,
        employee_id="admin",
        real_name="管理员",
        role="admin",
        csrf_token="csrf",
    )
    records = [
        {
            "feedback_id": "feedback-1",
            "kind": "answer",
            "rating": "down",
            "query": "需要改进的回答",
        },
        {
            "feedback_id": "feedback-2",
            "kind": "citation",
            "rating": "down",
            "doc_title": "项目标准",
        },
    ]
    monkeypatch.setattr(routes_admin, "read_records", lambda: records)
    try:
        updated = routes_admin.patch_feedback(
            "feedback-1",
            AdminFeedbackPatchRequest(
                status="resolved",
                resolution="answer_improved",
                admin_note="已调整回答",
            ),
            conn,
            admin,
        )
        assert updated.status == "resolved"
        assert updated.assignee_name == "管理员"

        result = routes_admin.feedback(
            status="resolved",
            kind=None,
            rating=None,
            q="改进",
            page=1,
            page_size=20,
            conn=conn,
            _admin=admin,
        )
        assert result.total == 1
        assert result.counts["resolved"] == 1
        assert result.counts["pending"] == 1
        assert result.entries[0].resolution == "answer_improved"
    finally:
        conn.close()
