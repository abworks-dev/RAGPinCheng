from __future__ import annotations

import os
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_external_media
from api.db import connect, init_db
from api.db import get_db
from api import external_document_import
from api.content_storage import ContentStorage
from api.content_store import create_batch, create_category, register_uploaded_document
from api.external_document_import import import_source_documents
from api.external_media import (
    ENTRY_KIND_DOCUMENT,
    ENTRY_KIND_VIDEO,
    ExternalMediaError,
    discover_video_files,
    project_external_lifecycle,
    reconcile_source,
    scan_share,
)
from api.media_storage import MediaStorageError, normalize_external_relative_path, require_mutable_media_source, resolve_media_path
from src.config import parse_external_unc_roots, resolve_external_unc_path


@pytest.mark.parametrize(
    ("media_status", "job_status", "publication_status", "index_status", "expected"),
    [
        ("uploaded", None, None, None, "awaiting_transcription"),
        ("transcribing", "running", None, None, "transcribing"),
        ("failed", "failed", None, None, "transcription_failed"),
        ("transcript_ready", "succeeded", "pending", None, "transcript_ready"),
        ("transcript_ready", "succeeded", "rejected", None, "transcript_rejected"),
        ("transcript_ready", "succeeded", "publishing", "embedding", "publishing"),
        ("transcript_ready", "succeeded", "publication_failed", "failed", "publication_failed"),
        ("ready", "succeeded", "published", "done", "published"),
    ],
)
def test_external_lifecycle_matches_managed_video_statuses(
    media_status: str,
    job_status: str | None,
    publication_status: str | None,
    index_status: str | None,
    expected: str,
) -> None:
    assert project_external_lifecycle(
        media_status=media_status,
        transcription_job_status=job_status,
        publication_status=publication_status,
        index_status=index_status,
    ) == expected


def test_external_lifecycle_prefers_published_head_over_newer_revision() -> None:
    assert project_external_lifecycle(
        media_status="transcript_ready",
        transcription_job_status="succeeded",
        publication_status="pending",
        index_status=None,
        has_published_head=True,
    ) == "published"


def _source_database(tmp_path: Path, share: Path) -> tuple[sqlite3.Connection, str]:
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    scheme = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()
    assert scheme is not None
    source_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO external_media_sources(
               id,name,root_alias,relative_path,target_category_id,default_scheme_id,
               created_at,updated_at,created_by
           ) VALUES (?,?,?,?,?,?,?,?,NULL)""",
        (source_id, "Synthetic share", "share", "", "cat-05", scheme["id"], 1, 1),
    )
    conn.commit()
    return conn, source_id


def test_unc_root_mapping_accepts_unicode_path_and_rejects_escape_or_unknown_share(tmp_path: Path) -> None:
    roots = parse_external_unc_roots(json.dumps({"training": {"unc": r"\\192.168.0.252\项目工程", "path": str(tmp_path)}}))
    assert resolve_external_unc_path(r"\\192.168.0.252\项目工程\品成知识库\1.2内部教学视频（MP4）", roots) == (
        "training", "品成知识库/1.2内部教学视频（MP4）"
    )
    with pytest.raises(ValueError, match="invalid_external_unc_path"):
        resolve_external_unc_path(r"\\192.168.0.252\项目工程\..\秘密", roots)
    with pytest.raises(ValueError, match="external_unc_root_unconfigured"):
        resolve_external_unc_path(r"\\192.168.0.252\其他共享\视频", roots)


def test_create_external_source_accepts_full_unc_path_within_configured_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    share = tmp_path / "share"
    share.mkdir()
    conn, _ = _source_database(tmp_path, share)
    scheme_id = conn.execute("SELECT id FROM transcription_schemes ORDER BY sort_order LIMIT 1").fetchone()[0]
    monkeypatch.setattr(routes_external_media, "EXTERNAL_MEDIA_ROOTS", {"training": share})
    monkeypatch.setattr(
        routes_external_media,
        "EXTERNAL_MEDIA_UNC_ROOTS",
        {"training": (r"\\192.168.0.252\项目工程".casefold(), share)},
    )
    body = routes_external_media.ExternalMediaSourceCreate(
        name="内部教学视频",
        unc_path=r"\\192.168.0.252\项目工程\品成知识库\1.2内部教学视频（MP4）",
        target_category_id="cat-05",
        default_scheme_id=scheme_id,
    )
    admin = type("Admin", (), {"id": None})()
    created = routes_external_media.create_source(body, admin, conn)
    assert created.root_alias == "training"
    assert created.relative_path == "品成知识库/1.2内部教学视频（MP4）"
    conn.close()


def test_scan_reconciles_add_change_missing_recovery_and_outage(tmp_path: Path) -> None:
    share = tmp_path / "share"
    (share / "课程").mkdir(parents=True)
    first = share / "课程" / "lesson.mp4"
    first.write_bytes(b"video-v1")
    conn, source_id = _source_database(tmp_path, share)
    roots = {"share": share}
    try:
        initial = reconcile_source(conn, source_id, trigger_type="manual", roots=roots, now=10)
        assert (initial.discovered_count, initial.added_count, initial.changed_count) == (1, 1, 0)
        original = conn.execute(
            "SELECT id,media_id,fingerprint FROM external_media_entries WHERE source_id=? AND availability='available'",
            (source_id,),
        ).fetchone()
        assert original is not None

        repeat = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=20)
        assert (repeat.added_count, repeat.changed_count, repeat.missing_count) == (0, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM external_media_entries").fetchone()[0] == 1

        first.write_bytes(b"video-v2-longer")
        changed = reconcile_source(conn, source_id, trigger_type="manual", roots=roots, now=30)
        assert (changed.added_count, changed.changed_count) == (0, 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=? AND availability='superseded'", (source_id,)
        ).fetchone()[0] == 1

        current = conn.execute(
            "SELECT id,media_id FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()
        current_identity = first.stat()
        first.unlink()
        missing = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=40)
        assert missing.missing_count == 1
        assert conn.execute(
            "SELECT availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()[0] == "missing"

        first.write_bytes(b"video-v2-longer")
        os.utime(first, ns=(current_identity.st_atime_ns, current_identity.st_mtime_ns))
        recovered = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=50)
        assert (recovered.added_count, recovered.changed_count) == (0, 0)
        recovered_row = conn.execute(
            "SELECT media_id,availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()
        assert (recovered_row["media_id"], recovered_row["availability"]) == (current["media_id"], "available")

        offline = tmp_path / "share-offline"
        share.rename(offline)
        with pytest.raises(ExternalMediaError, match="external_source_unavailable"):
            reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=60)
        assert conn.execute(
            "SELECT availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()[0] == "available"
        assert conn.execute("SELECT status FROM external_media_sources WHERE id=?", (source_id,)).fetchone()[0] == "unavailable"
    finally:
        conn.close()


def test_scan_database_failure_does_not_leave_running_state(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    (share / "clip.mp4").write_bytes(b"video")
    conn, source_id = _source_database(tmp_path, share)
    conn.execute(
        """CREATE TRIGGER fail_external_entry_insert
           BEFORE INSERT ON external_media_entries
           BEGIN
             SELECT RAISE(ABORT, 'synthetic reconcile failure');
           END"""
    )
    conn.commit()
    try:
        with pytest.raises(ExternalMediaError, match="external_reconcile_failed"):
            reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)

        source = conn.execute(
            "SELECT status,last_error_code FROM external_media_sources WHERE id=?", (source_id,)
        ).fetchone()
        run = conn.execute(
            "SELECT status,error_code,finished_at FROM external_media_scan_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()
        assert (source["status"], source["last_error_code"]) == (
            "scan_failed",
            "external_reconcile_failed",
        )
        assert (run["status"], run["error_code"]) == ("failed", "external_reconcile_failed")
        assert run["finished_at"] is not None
        assert conn.execute("SELECT COUNT(*) FROM external_media_entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    finally:
        conn.close()


def test_external_media_resolver_checks_identity_and_never_returns_host_path(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    video = share / "clip.mp4"
    video.write_bytes(b"range-video")
    conn, source_id = _source_database(tmp_path, share)
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        row = conn.execute(
            "SELECT media_id FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()
        resolved = resolve_media_path(conn, row["media_id"], external_roots={"share": share})
        assert resolved.path == video
        assert resolved.storage_kind == "external"
        with pytest.raises(MediaStorageError, match="external_media_read_only"):
            require_mutable_media_source(conn, row["media_id"])
        video.write_bytes(b"mutated")
        with pytest.raises(MediaStorageError, match="external_media_changed"):
            resolve_media_path(conn, row["media_id"], external_roots={"share": share})
        persisted = conn.execute(
            "SELECT storage_rel_path FROM media_assets WHERE media_id=?", (row["media_id"],)
        ).fetchone()[0]
        assert str(share) not in persisted
    finally:
        conn.close()


@pytest.mark.parametrize("value", ["../secret.mp4", "folder/../../secret.mp4", "/absolute.mp4", r"folder\clip.mp4", "./clip.mp4"])
def test_external_relative_path_rejects_escape_and_ambiguous_separators(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_external_relative_path"):
        normalize_external_relative_path(value)


def test_scanner_collects_videos_and_documents_and_rejects_bounded_overflow(tmp_path: Path) -> None:
    (tmp_path / "one.mp4").write_bytes(b"1")
    (tmp_path / "notes.txt").write_text("unsupported suffix", encoding="utf-8")
    (tmp_path / "spec.pdf").write_bytes(b"%PDF-1.4")
    scanned = scan_share(tmp_path, max_files=10)
    assert [(item.filename, item.entry_kind, item.doc_type) for item in scanned.files] == [
        ("one.mp4", ENTRY_KIND_VIDEO, None),
        ("spec.pdf", ENTRY_KIND_DOCUMENT, "pdf"),
    ]
    assert [item.filename for item in discover_video_files(tmp_path, max_files=10)] == ["one.mp4"]
    with pytest.raises(ExternalMediaError, match="external_source_file_limit_exceeded"):
        scan_share(tmp_path, max_files=1)


def test_scanner_skips_symbolic_links_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.mp4"
    target.write_bytes(b"video")
    link = tmp_path / "linked.mp4"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable for this account")
    found = discover_video_files(tmp_path)
    assert [item.filename for item in found] == ["target.mp4"]


def test_external_media_api_requires_admin_and_csrf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "app.sqlite"
    share = tmp_path / "share"
    share.mkdir()
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    now = int(time.time())
    conn.executemany(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at) VALUES (?,?,?,?,?,1,?)",
        [(1, "admin", "Admin", "x", "admin", now), (2, "user", "User", "x", "user", now)],
    )
    conn.executemany(
        "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
        [("admin-sid", 1, "admin-csrf", now, now + 3600), ("user-sid", 2, "user-csrf", now, now + 3600)],
    )
    scheme_id = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()[0]
    conn.commit()
    conn.close()

    def override_db():
        request_conn = connect(db_path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    monkeypatch.setattr(routes_external_media, "EXTERNAL_MEDIA_ROOTS", {"share": share})
    app = FastAPI()
    app.include_router(routes_external_media.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        assert client.get("/api/admin/external-media/roots").status_code == 401
        assert client.get(
            "/api/admin/external-media/roots", cookies={"pc_sid": "user-sid"}
        ).status_code == 403
        assert client.get(
            "/api/admin/external-media/roots", cookies={"pc_sid": "admin-sid"}
        ).json() == [{"alias": "share"}]
        body = {
            "name": "Training", "root_alias": "share", "relative_path": "",
            "target_category_id": "cat-05", "default_scheme_id": scheme_id,
            "auto_enqueue": False, "scan_interval_seconds": 900,
        }
        assert client.post(
            "/api/admin/external-media/sources", json=body, cookies={"pc_sid": "admin-sid"}
        ).status_code == 403
        created = client.post(
            "/api/admin/external-media/sources", json=body,
            cookies={"pc_sid": "admin-sid"}, headers={"X-CSRF-Token": "admin-csrf"},
        )
        assert created.status_code == 201
        assert created.json()["root_alias"] == "share"
        assert created.json()["document_import_mode"] == "manual"
        source_id = created.json()["id"]
        import_url = f"/api/admin/external-media/sources/{source_id}/documents/import"
        assert client.post(
            import_url, json={"entry_ids": None}, cookies={"pc_sid": "admin-sid"}
        ).status_code == 403
        assert client.post(
            import_url, json={"entry_ids": None}, cookies={"pc_sid": "user-sid"},
            headers={"X-CSRF-Token": "user-csrf"},
        ).status_code == 403
        ready = client.post(
            import_url, json={"entry_ids": None}, cookies={"pc_sid": "admin-sid"},
            headers={"X-CSRF-Token": "admin-csrf"},
        )
        assert ready.status_code == 200
        assert ready.json()["requested"] == 0


def _document_share(tmp_path: Path) -> tuple[sqlite3.Connection, str, Path]:
    share = tmp_path / "share"
    (share / "公司标准").mkdir(parents=True)
    (share / "公司标准" / "设计标准.md").write_text("# 设计标准\n\n正文内容\n", encoding="utf-8")
    (share / "说明.md").write_text("# 说明\n\n根目录文档\n", encoding="utf-8")
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)"
        " VALUES (1,'importer','Importer','x','admin',1,1)"
    )
    used_codes = {
        str(row["display_code"])
        for row in conn.execute(
            "SELECT display_code FROM category_nodes WHERE parent_id IS NULL AND deleted_at IS NULL"
        ).fetchall()
    }
    display_code = next(f"{i:02d}" for i in range(1, 100) if f"{i:02d}" not in used_codes)
    root = create_category(
        conn,
        category_key=None,
        parent_id=None,
        display_code=display_code,
        display_name="共享标准镜像测试",
        sort_order=990,
        actor_user_id=1,
    )
    scheme = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()
    assert scheme is not None
    source_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO external_media_sources(
               id,name,root_alias,relative_path,target_category_id,default_scheme_id,
               created_at,updated_at,created_by
           ) VALUES (?,?,?,?,?,?,?,?,1)""",
        (source_id, "共享标准源", "share", "", "cat-05", scheme["id"], 1, 1),
    )
    conn.execute(
        """UPDATE category_nodes SET category_kind='shared_folder', external_source_id=?,
                  version=version+1 WHERE id=?""",
        (source_id, root["id"]),
    )
    conn.commit()
    return conn, source_id, share


def test_scan_registers_documents_without_media_assets(tmp_path: Path) -> None:
    conn, source_id, share = _document_share(tmp_path)
    try:
        result = reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        assert result.added_count == 2
        assert result.added_entry_ids == ()
        assert len(result.added_document_entry_ids) == 2
        rows = {
            row["relative_path"]: row
            for row in conn.execute("SELECT * FROM external_media_entries").fetchall()
        }
        document = rows["说明.md"]
        assert (document["entry_kind"], document["doc_type"]) == (ENTRY_KIND_DOCUMENT, "markdown")
        assert document["media_id"] is None
        assert document["import_status"] == "not_imported"
        assert document["content_item_id"] is None
        # Documents are library candidates, not transcription media.
        assert conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
        mirror = conn.execute(
            "SELECT id FROM category_nodes WHERE external_relative_path='公司标准' AND deleted_at IS NULL"
        ).fetchone()
        assert mirror is not None
    finally:
        conn.close()


def test_import_documents_registers_pending_publication_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, source_id, share = _document_share(tmp_path)
    monkeypatch.setattr(external_document_import, "_storage", ContentStorage(tmp_path / "content"))
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        result = import_source_documents(conn, source_id, entry_ids=None, actor_user_id=1, roots={"share": share})
        assert (result.imported, result.skipped, result.failed) == (2, 0, 0)

        entry = conn.execute(
            "SELECT * FROM external_media_entries WHERE relative_path='公司标准/设计标准.md'"
        ).fetchone()
        assert entry["import_status"] == "imported"
        assert entry["imported_fingerprint"] == entry["fingerprint"]
        assert entry["import_error"] is None
        assert entry["content_item_id"] and entry["content_version_id"]

        version = conn.execute(
            "SELECT * FROM content_versions WHERE id=?", (entry["content_version_id"],)
        ).fetchone()
        assert (version["doc_type"], version["source_origin"]) == ("markdown", "server")
        assert version["lifecycle_status"] == "pending_publication"
        assert conn.execute(
            "SELECT 1 FROM content_objects WHERE sha256=?", (version["object_sha256"],)
        ).fetchone() is not None

        mirror = conn.execute(
            "SELECT id FROM category_nodes WHERE external_relative_path='公司标准' AND deleted_at IS NULL"
        ).fetchone()
        item = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (entry["content_item_id"],)
        ).fetchone()
        assert item["category_id"] == mirror["id"]

        root_entry = conn.execute(
            "SELECT content_item_id FROM external_media_entries WHERE relative_path='说明.md'"
        ).fetchone()
        root_item = conn.execute(
            "SELECT category_id FROM content_items WHERE id=?", (root_entry["content_item_id"],)
        ).fetchone()
        assert root_item["category_id"] == "cat-05"

        # The remote share stays read-only and untouched.
        assert (share / "公司标准" / "设计标准.md").exists()

        again = import_source_documents(conn, source_id, entry_ids=None, actor_user_id=1, roots={"share": share})
        assert (again.requested, again.imported) == (0, 0)
    finally:
        conn.close()


def test_changed_document_creates_new_version_on_reimport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, source_id, share = _document_share(tmp_path)
    monkeypatch.setattr(external_document_import, "_storage", ContentStorage(tmp_path / "content"))
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        import_source_documents(conn, source_id, entry_ids=None, actor_user_id=1, roots={"share": share})
        first = conn.execute(
            "SELECT content_item_id,content_version_id FROM external_media_entries"
            " WHERE relative_path='说明.md' AND availability='available'"
        ).fetchone()

        (share / "说明.md").write_text("# 说明\n\n更新后的正文更长一些\n", encoding="utf-8")
        changed = reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=20)
        assert (changed.added_count, changed.changed_count) == (0, 1)

        entry = conn.execute(
            "SELECT * FROM external_media_entries WHERE relative_path='说明.md' AND availability='available'"
        ).fetchone()
        # The superseded row keeps the previous version; the new row inherits the item.
        assert entry["import_status"] == "pending"
        assert entry["content_item_id"] == first["content_item_id"]

        again = import_source_documents(conn, source_id, entry_ids=None, actor_user_id=1, roots={"share": share})
        assert (again.requested, again.imported) == (1, 1)
        versions = conn.execute(
            "SELECT version_number,lifecycle_status FROM content_versions WHERE item_id=? ORDER BY version_number",
            (first["content_item_id"],),
        ).fetchall()
        assert [(row["version_number"], row["lifecycle_status"]) for row in versions] == [
            (1, "superseded"),
            (2, "pending_publication"),
        ]
        entry_after = conn.execute(
            "SELECT content_version_id FROM external_media_entries"
            " WHERE relative_path='说明.md' AND availability='available'"
        ).fetchone()
        assert entry_after["content_version_id"] != first["content_version_id"]
    finally:
        conn.close()


def test_import_skips_document_when_target_filename_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, source_id, share = _document_share(tmp_path)
    storage = ContentStorage(tmp_path / "content")
    monkeypatch.setattr(external_document_import, "_storage", storage)
    try:
        stored = storage.ingest_path(share / "说明.md", mime_type="text/markdown", max_bytes=1024 * 1024)
        batch_id = create_batch(conn, origin="web", actor_user_id=1)
        register_uploaded_document(
            conn,
            batch_id=batch_id,
            category_id="cat-05",
            title="说明",
            original_filename="说明.md",
            doc_type="markdown",
            stored=stored,
            actor_user_id=1,
        )
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        result = import_source_documents(conn, source_id, entry_ids=None, actor_user_id=1, roots={"share": share})
        # The sub-folder document imports; the root one collides with the existing item.
        assert (result.imported, result.skipped, result.failed) == (1, 1, 0)
        conflict = [item for item in result.outcomes if item.status == "skipped"]
        assert [item.reason_code for item in conflict] == ["content_filename_conflict"]
        assert conn.execute(
            "SELECT import_status FROM external_media_entries WHERE relative_path='说明.md'"
        ).fetchone()[0] == "not_imported"
        assert conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0] == 2
    finally:
        conn.close()


def test_scheduled_scan_auto_imports_documents_for_auto_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, source_id, share = _document_share(tmp_path)
    db_path = tmp_path / "app.sqlite"
    conn.execute(
        "UPDATE external_media_sources SET document_import_mode='auto',created_by=1 WHERE id=?", (source_id,)
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(external_document_import, "_storage", ContentStorage(tmp_path / "content"))
    monkeypatch.setattr(routes_external_media, "EXTERNAL_MEDIA_ROOTS", {"share": share})
    real_reconcile = routes_external_media.reconcile_source
    monkeypatch.setattr(
        routes_external_media,
        "reconcile_source",
        lambda conn, sid, *, trigger_type, **kwargs: real_reconcile(
            conn, sid, trigger_type=trigger_type, roots={"share": share}, **kwargs
        ),
    )
    monkeypatch.setattr(routes_external_media, "connect", lambda: connect(db_path))
    real_auto_import = routes_external_media.run_auto_import
    monkeypatch.setattr(
        routes_external_media,
        "run_auto_import",
        lambda conn, sid, *, actor_user_id: real_auto_import(
            conn, sid, actor_user_id=actor_user_id, roots={"share": share}
        ),
    )
    try:
        routes_external_media.run_due_external_scans()
        conn = connect(db_path)
        entry = conn.execute(
            "SELECT import_status,import_error,content_version_id FROM external_media_entries"
            " WHERE relative_path='说明.md'"
        ).fetchone()
        assert entry["import_error"] is None, entry["import_error"]
        assert entry["import_status"] == "imported"
        assert conn.execute(
            "SELECT lifecycle_status FROM content_versions WHERE id=?", (entry["content_version_id"],)
        ).fetchone()[0] == "pending_publication"
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - connection may already be closed
            pass
