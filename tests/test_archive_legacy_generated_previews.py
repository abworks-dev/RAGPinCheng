from __future__ import annotations

import sqlite3
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from api.db import init_db
from scripts.archive_legacy_generated_previews import (
    GeneratedPreviewArchiveError,
    archive_candidates,
    find_candidates,
    summarize,
)


ROOT = Path(__file__).resolve().parents[1]


def _database(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES ('admin','Admin','x','admin',1,1)"
    )
    actor = conn.execute("SELECT id FROM users WHERE employee_id='admin'").fetchone()[0]
    return conn, actor


def _insert_item(conn: sqlite3.Connection, item_id: str, filename: str, status: str = "publication_failed"):
    conn.execute(
        "INSERT INTO content_objects(sha256,size_bytes,mime_type,storage_rel_path,created_at) "
        "VALUES (?,?,?, ?,1)",
        (item_id.ljust(64, "0"), 1, "application/pdf", f"objects/{item_id}"),
    )
    conn.execute(
        "INSERT INTO content_items(id,title,content_kind,category_id,created_by,created_at,updated_at) "
        "VALUES (?,?,'document','cat-03',1,1,1)",
        (item_id, filename),
    )
    conn.execute(
        """INSERT INTO content_versions(
             id,item_id,version_number,object_sha256,original_filename,doc_type,source_origin,
             lifecycle_status,created_by,created_at,updated_at
           ) VALUES (?,?,1,?,?,?,'legacy',?,1,1,1)""",
        (
            f"version-{item_id}",
            item_id,
            item_id.ljust(64, "0"),
            filename,
            "xlsx" if filename.lower().endswith("xlsx") else "pdf",
            status,
        ),
    )
    conn.commit()


def test_archives_only_legacy_generated_previews_and_writes_audit(tmp_path):
    conn, actor = _database(tmp_path)
    _insert_item(conn, "preview-pdf", "report.preview.pdf")
    _insert_item(conn, "preview-xlsx", "sheet.PREVIEW.XLSX", "awaiting_review")
    _insert_item(conn, "source-pdf", "report.pdf")

    candidates = find_candidates(conn)
    assert summarize(candidates) == {
        "candidate_count": 2,
        "blocked_count": 0,
        "by_lifecycle_status": {"awaiting_review": 1, "publication_failed": 1},
    }
    assert archive_candidates(conn, candidates, actor_user_id=actor) == 2
    assert conn.execute("SELECT count(*) FROM content_items WHERE archived_at IS NOT NULL").fetchone()[0] == 2
    assert conn.execute(
        "SELECT count(*) FROM content_audit_events WHERE event_type='content.generated_preview_archived'"
    ).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 3
    conn.close()


def test_refuses_entire_archive_when_candidate_has_published_head(tmp_path):
    conn, actor = _database(tmp_path)
    _insert_item(conn, "blocked", "report.preview.pdf", "published")
    conn.execute(
        "INSERT INTO content_publications(id,version_id,status,publisher_id,created_at,updated_at,published_at) "
        "VALUES ('publication','version-blocked','published',?,1,1,1)",
        (actor,),
    )
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) "
        "VALUES ('blocked','version-blocked','publication',1)"
    )
    conn.commit()

    candidates = find_candidates(conn)
    assert summarize(candidates)["blocked_count"] == 1
    with pytest.raises(GeneratedPreviewArchiveError, match="generated_preview_archive_blocked"):
        archive_candidates(conn, candidates, actor_user_id=actor)
    assert conn.execute("SELECT archived_at FROM content_items WHERE id='blocked'").fetchone()[0] is None
    conn.close()


def test_refuses_published_state_even_when_relations_are_incomplete(tmp_path):
    conn, actor = _database(tmp_path)
    _insert_item(conn, "blocked-state", "report.preview.pdf", "published")

    candidates = find_candidates(conn)
    assert summarize(candidates)["blocked_count"] == 1
    with pytest.raises(GeneratedPreviewArchiveError, match="generated_preview_archive_blocked"):
        archive_candidates(conn, candidates, actor_user_id=actor)
    conn.close()


def test_apply_requires_an_active_admin_actor(tmp_path):
    conn, _actor = _database(tmp_path)
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES ('user','User','x','user',1,1)"
    )
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='user'").fetchone()[0]
    _insert_item(conn, "preview-user", "report.preview.pdf")

    with pytest.raises(GeneratedPreviewArchiveError, match="active_admin_actor_not_found"):
        archive_candidates(conn, find_candidates(conn), actor_user_id=user_id)
    assert conn.execute("SELECT archived_at FROM content_items WHERE id='preview-user'").fetchone()[0] is None
    conn.close()


def test_cli_defaults_to_redacted_read_only_dry_run(tmp_path):
    conn, _actor = _database(tmp_path)
    _insert_item(conn, "preview", "secret-project.preview.pdf")
    database = tmp_path / "app.sqlite"
    conn.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "archive_legacy_generated_previews.py"),
            "--database",
            str(database),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert "secret-project" not in result.stdout
    assert json.loads(result.stdout) == {
        "blocked_count": 0,
        "by_lifecycle_status": {"publication_failed": 1},
        "candidate_count": 1,
        "status": "dry_run",
    }
