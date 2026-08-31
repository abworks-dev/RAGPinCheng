from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from api.db import connect, init_db

from scripts import cleanup_leftover_media as tool


def _seed_admin(conn: sqlite3.Connection) -> int:
    now = int(time.time())
    cursor = conn.execute(
        """INSERT INTO users
           (employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES (?,?,?, 'admin',1,?)""",
        ("leftover-clean-admin", "清理管理员", "unused", now),
    )
    admin_id = int(cursor.lastrowid)
    conn.commit()
    return admin_id


def _ensure_active_category(conn: sqlite3.Connection) -> str:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(category_nodes)")]
    row = conn.execute(
        "SELECT id FROM category_nodes WHERE id='cat-05' AND is_active=1"
    ).fetchone()
    if row is not None:
        return str(row["id"])
    now = int(time.time())
    if "display_code" in columns:
        conn.execute(
            """INSERT INTO category_nodes
               (id,display_code,display_name,parent_id,is_active,created_at,updated_at)
               VALUES ('cat-05','05','培训资料',NULL,1,?,?)""",
            (now, now),
        )
    else:
        conn.execute(
            """INSERT INTO category_nodes (id,is_active,created_at,updated_at)
               VALUES ('cat-05',1,?,?)""",
            (now, now),
        )
    conn.commit()
    return "cat-05"


def _seed_media(
    conn: sqlite3.Connection,
    media_id: str,
    *,
    title: str,
    filename: str,
    status: str = "uploaded",
    created_by: int | None = None,
    with_valid_shell: bool = False,
) -> None:
    now = int(time.time())
    conn.execute(
        """INSERT INTO media_assets
           (media_id,title,original_filename,storage_rel_path,mime_type,file_size,
            sha256,transcript_source_path,transcript_origin,status,created_by,
            created_at,updated_at,error,target_category_id,storage_kind)
           VALUES (?,?,?,?, 'video/mp4',1234, 'x'*64, NULL,'automatic',?,?, ?,?,NULL,'cat-05','managed')""",
        (
            media_id,
            title,
            filename,
            f"{media_id}/original.mp4",
            status,
            created_by,
            now,
            now,
        ),
    )
    if with_valid_shell:
        conn.execute(
            """INSERT INTO content_items
               (id,title,content_kind,category_id,media_id,created_by,created_at,
                updated_at,archived_at,normalized_filename)
               VALUES (?,?, 'media_transcript','cat-05',?,?,?,?,NULL,NULL)""",
            (f"media-transcript-{media_id}", title, media_id, created_by, now, now),
        )
    conn.commit()


def _seed_job(
    conn: sqlite3.Connection,
    media_id: str,
    *,
    status: str = "failed",
    attempt: int = 1,
) -> None:
    now = int(time.time())
    conn.execute(
        """INSERT INTO transcription_jobs
           (id,media_id,attempt_number,request_idempotency_key,execution_identity,
            profile_id,provider_key,profile_definition_version,config_hash,
            profile_snapshot_json,execution_config_json,execution_fingerprint,
            audio_sha256,input_kind,input_size_bytes,total_ms,status,created_at,updated_at)
           VALUES (?,?,?,?,?, 'profile-test','provider-test','1','hash',
                   '{}','{}','fingerprint','audio-hash','video',0,1,?,?,?)""",
        (
            f"job-{media_id}-{attempt}",
            media_id,
            attempt,
            f"idem-{media_id}-{attempt}",
            f"exec-{media_id}-{attempt}",
            status,
            now,
            now,
        ),
    )
    conn.commit()


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    parents_path = tmp_path / "parents.sqlite"
    parents_conn = sqlite3.connect(parents_path)
    parents_conn.execute(
        "CREATE TABLE parents (id TEXT PRIMARY KEY, media_id TEXT)"
    )
    parents_conn.commit()
    parents_conn.close()
    monkeypatch.setattr(tool, "qdrant_point_count", lambda media_id: 0)
    conn = connect(db_path)
    admin_id = _seed_admin(conn)
    _ensure_active_category(conn)
    media_root = tmp_path / "media"
    yield {
        "database": db_path,
        "parents_database": parents_path,
        "media_root": media_root,
        "conn": conn,
        "admin_id": admin_id,
    }
    conn.close()


def _seed_version(
    conn: sqlite3.Connection,
    version_id: str,
    media_id: str,
    *,
    transcription_job_id: str | None,
    supersedes_version_id: str | None = None,
) -> None:
    now = int(time.time())
    conn.execute(
        """INSERT INTO transcript_versions
           (id,media_id,transcription_job_id,source,profile_id,markdown_storage_kind,
            markdown_rel_path,markdown_sha256,markdown_size_bytes,review_status,
            publication_status,supersedes_version_id,created_at,updated_at)
           VALUES (?,?,?, 'automatic','profile-test','managed_artifact',
                   ?, 'sha', 10,'awaiting_review','not_published',?,?,?)""",
        (
            version_id,
            media_id,
            transcription_job_id,
            f"artifacts/{version_id}.md",
            supersedes_version_id,
            now,
            now,
        ),
    )
    conn.commit()


def _seed_version_artifact(conn: sqlite3.Connection, version_id: str) -> None:
    now = int(time.time())
    conn.execute(
        """INSERT INTO transcript_version_artifacts
           (version_id,artifact_id,kind,content_sha256,size_bytes)
           VALUES (?,?, 'markdown','sha',10)""",
        (version_id, version_id + "-artifact"),
    )
    conn.commit()


def _seed_media_with_valid_shell(conn: sqlite3.Connection, media_id: str, title: str) -> None:
    _seed_media(conn, media_id, title=title, filename=f"{media_id}.mp4", with_valid_shell=True)


def test_inventory_only_lists_leftover_without_valid_shell(fixture: dict) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-left-1", title="测试遗留一", filename="a.mp4", status="transcript_ready")
    _seed_job(conn, "m-left-1", status="failed")
    _seed_media(conn, "m-valid", title="正常媒体", filename="b.mp4", with_valid_shell=True)
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=False,
    )
    ids = sorted(str(item["media_id"]) for item in items)
    assert ids == ["m-left-1"]
    report = tool.summarize(items)
    assert report["candidate_count"] == 1
    assert report["manifest_sha256"]
    assert report["delete_blocked_count"] == 0


def test_archive_apply_backfills_shell_and_archives(fixture: dict) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-arc", title="待归档遗留", filename="c.mp4", status="transcript_ready")
    _seed_job(conn, "m-arc", status="failed")
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=False,
    )
    assert len(items) == 1
    result = tool.archive_leftover(conn, items, actor_user_id=fixture["admin_id"])
    assert result["archived_count"] == 1
    assert result["blocked"] == {}
    row = conn.execute(
        "SELECT status FROM media_assets WHERE media_id='m-arc'"
    ).fetchone()
    assert row["status"] == "archived"
    shell = conn.execute(
        """SELECT id,archived_at FROM content_items
           WHERE media_id='m-arc' AND content_kind='media_transcript'"""
    ).fetchone()
    assert shell is not None and shell["archived_at"] is not None
    audit = conn.execute(
        "SELECT event_type FROM content_audit_events WHERE event_type='content.archived'"
    ).fetchone()
    assert audit is not None
    # Archived media no longer appears as leftover.
    remaining = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=False,
    )
    assert remaining == []


def test_archive_blocks_external_without_writing(fixture: dict) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-ext", title="共享目录遗留", filename="d.mp4", status="failed")
    conn.execute(
        "UPDATE media_assets SET storage_kind='external' WHERE media_id='m-ext'"
    )
    conn.commit()
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=False,
    )
    result = tool.archive_leftover(conn, items, actor_user_id=fixture["admin_id"])
    assert result["archived_count"] == 0
    assert "m-ext" in result["blocked"]
    row = conn.execute(
        "SELECT status FROM media_assets WHERE media_id='m-ext'"
    ).fetchone()
    assert row["status"] == "failed"


def test_delete_apply_removes_rows_and_media_dir(fixture: dict, tmp_path: Path) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-del", title="删除遗留", filename="e.mp4", status="failed")
    _seed_job(conn, "m-del", status="failed")
    media_dir = fixture["media_root"] / "m-del"
    media_dir.mkdir(parents=True)
    (media_dir / "original.mp4").write_bytes(b"fake-video-bytes")
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=True,
    )
    assert len(items) == 1
    result = tool.delete_leftover(
        conn, items, actor_user_id=fixture["admin_id"], media_roots=[fixture["media_root"]]
    )
    assert result["deleted_count"] == 1
    assert conn.execute(
        "SELECT 1 FROM media_assets WHERE media_id='m-del'"
    ).fetchone() is None
    assert not media_dir.exists()
    audit = conn.execute(
        "SELECT metadata_json FROM content_audit_events WHERE event_type='content.media_leftover_deleted'"
    ).fetchone()
    assert audit is not None
    assert json.loads(audit["metadata_json"])["media_id"] == "m-del"


def test_delete_fails_closed_on_active_job_or_parents(fixture: dict, tmp_path: Path) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-busy", title="处理中遗留", filename="f.mp4", status="transcribing")
    _seed_job(conn, "m-busy", status="running")
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=True,
    )
    with pytest.raises(tool.LeftoverMediaError, match="delete_blocked"):
        tool.delete_leftover(
            conn,
            items,
            actor_user_id=fixture["admin_id"],
            media_roots=[fixture["media_root"]],
        )
    _seed_media(conn, "m-idx", title="已索引遗留", filename="g.mp4", status="failed")
    parents_conn = sqlite3.connect(fixture["parents_database"])
    parents_conn.execute(
        "INSERT INTO parents (id,media_id) VALUES ('parent-1','m-idx')"
    )
    parents_conn.commit()
    parents_conn.close()
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=True,
    )
    ids = {str(item["media_id"]): item for item in items}
    # Only the indexed one remains a candidate; busy one was isolated above.
    single = [ids["m-idx"]]
    with pytest.raises(tool.LeftoverMediaError, match="delete_blocked"):
        tool.delete_leftover(
            conn,
            single,
            actor_user_id=fixture["admin_id"],
            media_roots=[fixture["media_root"]],
        )


def test_delete_handles_version_artifacts_and_supersede_chain(fixture: dict) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-fk", title="外键图遗留", filename="i.mp4", status="transcript_ready")
    _seed_job(conn, "m-fk", status="failed")
    _seed_version(
        conn, "v1", "m-fk", transcription_job_id="job-m-fk-1"
    )
    _seed_version(
        conn, "v2", "m-fk", transcription_job_id=None, supersedes_version_id="v1"
    )
    _seed_version_artifact(conn, "v1")
    _seed_version_artifact(conn, "v2")
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=True,
    )
    assert len(items) == 1
    result = tool.delete_leftover(
        conn, items, actor_user_id=fixture["admin_id"], media_roots=[fixture["media_root"]]
    )
    assert result["deleted_count"] == 1
    assert conn.execute("SELECT 1 FROM transcript_versions WHERE media_id='m-fk'").fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM transcript_version_artifacts"
    ).fetchone() is None
    assert conn.execute("SELECT 1 FROM transcription_jobs WHERE media_id='m-fk'").fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM media_assets WHERE media_id='m-fk'"
    ).fetchone() is None


def test_manifest_mismatch_and_apply_guards(fixture: dict) -> None:
    conn = fixture["conn"]
    _seed_media(conn, "m-guard", title="门禁遗留", filename="h.mp4", status="failed")
    items = tool.find_candidates(
        conn,
        media_roots=[fixture["media_root"]],
        parents_database=fixture["parents_database"],
        with_qdrant=False,
    )
    frozen = tool.manifest_sha256(items)
    assert len(frozen) == 64
    # Wrong manifest must not match the frozen inventory.
    assert tool.manifest_sha256([]) != frozen