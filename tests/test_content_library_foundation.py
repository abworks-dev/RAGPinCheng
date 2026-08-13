from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.content_permissions import has_content_permission
from api.content_import import import_server_batch, resolve_import_category
from api.content_storage import ContentStorage, StoredContentObject
from api.content_store import create_category, create_web_batch, register_uploaded_document
from api.content_store import (
    create_publication_job,
    review_version,
    submit_version_for_review,
)
from api import content_publication
from api import routes_content
from api.auth import CurrentUser
from api.schemas import (
    CreateContentPermissionGroupRequest,
    UpdateContentPermissionGroupRequest,
    UpdateContentPermissionsRequest,
)
from api.db import init_db
from api.content_view import rebuild_read_only_view
from src.content_retrieval_visibility import SQLitePublishedContentVisibility
from src.indexing_pipeline import IndexResult


def _db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('u1','整理员','x','user',1,1)"
    )
    conn.commit()
    return conn


def test_migration_seeds_approved_top_level_categories(tmp_path):
    conn = _db(tmp_path)
    rows = conn.execute(
        "SELECT display_code,display_name FROM category_nodes WHERE parent_id IS NULL ORDER BY sort_order"
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("01", "行业规范与标准"),
        ("02", "客户标准与要求"),
        ("03", "公司内部标准"),
        ("04", "项目资料"),
        ("05", "培训资料"),
        ("06", "项目经验与案例"),
        ("99", "待确认资料"),
    ]
    conn.close()


def test_migration_seeds_permission_group_templates(tmp_path):
    conn = _db(tmp_path)
    groups = routes_content.list_permission_groups(
        CurrentUser(999, "admin", "管理员", "admin", "csrf"), conn
    )
    assert [(group.group_key, group.display_name, group.permissions) for group in groups] == [
        ("member", "普通成员", []),
        ("bim_engineer", "BIM工程师", ["organize"]),
        ("content_owner", "资料负责人", ["review"]),
        ("system_admin", "系统管理员", ["import_server", "manage_categories", "organize", "publish", "review"]),
    ]
    conn.close()


def test_custom_permission_group_is_a_template_not_a_user_binding(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(routes_content, "CONTENT_MANAGEMENT_ENABLED", True)
    actor = CurrentUser(999, "admin", "管理员", "admin", "csrf")
    created = routes_content.create_permission_group(
        CreateContentPermissionGroupRequest(display_name="项目发布员", permissions=["publish"]), actor, conn
    )
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    routes_content.put_content_permissions(
        user_id, UpdateContentPermissionsRequest(permissions=created.permissions), actor, conn
    )
    routes_content.update_permission_group(
        created.id, UpdateContentPermissionGroupRequest(permissions=["review"]), actor, conn
    )
    actual = [row[0] for row in conn.execute(
        "SELECT permission FROM content_permissions WHERE user_id=?", (user_id,)
    )]
    assert actual == ["publish"]
    conn.close()


def test_content_permission_is_additive_and_admin_has_fallback(tmp_path):
    conn = _db(tmp_path)
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    user = SimpleNamespace(id=user_id, role="user")
    admin = SimpleNamespace(id=999, role="admin")
    assert has_content_permission(conn, user, "organize") is False
    conn.execute(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,'organize',1)",
        (user_id,),
    )
    assert has_content_permission(conn, user, "organize") is True
    assert has_content_permission(conn, admin, "publish") is True
    conn.close()


def test_admin_can_assign_scoped_content_permissions(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    monkeypatch.setattr(routes_content, "CONTENT_MANAGEMENT_ENABLED", True)
    actor = CurrentUser(999, "admin", "管理员", "admin", "csrf")
    result = routes_content.put_content_permissions(
        user_id,
        UpdateContentPermissionsRequest(permissions=["organize", "review"]),
        actor,
        conn,
    )
    assert result.permissions == ["organize", "review"]
    user = SimpleNamespace(id=user_id, role="user")
    assert has_content_permission(conn, user, "review") is True
    assert has_content_permission(conn, user, "publish") is False
    conn.close()


def test_category_depth_is_limited_to_four(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    parent = "cat-01"
    for level in range(2, 5):
        row = create_category(
            conn,
            category_key=f"level_{level}",
            parent_id=parent,
            display_code=str(level),
            display_name=f"第{level}级",
            sort_order=level,
            actor_user_id=actor,
        )
        parent = row["id"]
    with pytest.raises(ValueError, match="category_depth_exceeded"):
        create_category(
            conn,
            category_key="level_5",
            parent_id=parent,
            display_code="5",
            display_name="第五级",
            sort_order=5,
            actor_user_id=actor,
        )
    conn.close()


def test_same_object_can_back_independent_content_items(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    batch = create_web_batch(conn, actor_user_id=actor)
    payload = b"same-content"
    digest = hashlib.sha256(payload).hexdigest()
    stored = StoredContentObject(
        sha256=digest,
        size_bytes=len(payload),
        mime_type="application/pdf",
        storage_rel_path=f"objects/sha256/{digest[:2]}/{digest}",
        absolute_path=tmp_path / digest,
        created=True,
    )
    first = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-04",
        title="项目甲",
        original_filename="a.pdf",
        doc_type="pdf",
        stored=stored,
        actor_user_id=actor,
    )
    second = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-04",
        title="项目乙",
        original_filename="b.pdf",
        doc_type="pdf",
        stored=stored,
        actor_user_id=actor,
    )
    assert first.item_id != second.item_id
    assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 2
    conn.close()


def test_storage_rejects_path_escape(tmp_path):
    storage = ContentStorage(tmp_path / "content")
    with pytest.raises(ValueError, match="content_path_escape"):
        storage.resolve_object("../outside.pdf")


def test_publication_materialization_preserves_original_extension(tmp_path):
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    source = storage.object_path_for_sha256("a" * 64)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"office fixture")
    target = storage.materialize_published_source(
        source,
        content_item_id="item-123",
        content_version_id="version-456",
        filename="standard.docx",
    )
    assert target == (
        storage.published_root / "item-123" / "version-456" / "standard.docx"
    )
    assert target.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, target)


def test_review_publish_promotes_only_completed_candidate(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    payload = b"# Managed document\n\ncontent"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    stored = StoredContentObject(
        sha256=digest,
        size_bytes=len(payload),
        mime_type="text/markdown",
        storage_rel_path=object_path.relative_to(storage.root).as_posix(),
        absolute_path=object_path,
        created=True,
    )
    batch = create_web_batch(conn, actor_user_id=actor)
    uploaded = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-03",
        title="受管资料",
        original_filename="managed.md",
        doc_type="markdown",
        stored=stored,
        actor_user_id=actor,
    )
    submit_version_for_review(conn, uploaded.version_id, actor_user_id=actor)
    review_version(
        conn,
        uploaded.version_id,
        approved=True,
        note="确认",
        category_id=None,
        actor_user_id=actor,
    )
    _publication_id, job_id = create_publication_job(
        conn, uploaded.version_id, actor_user_id=actor
    )
    conn.close()

    monkeypatch.setattr(content_publication, "connect", lambda: _db_connect(tmp_path / "app.sqlite"))
    monkeypatch.setattr(content_publication, "_storage", storage)

    def fake_index(_path, _doc_type, _metadata, on_status):
        on_status("uploading")
        on_status("queued_mineru")
        on_status("parsing")
        on_status("embedding")
        return IndexResult(parents=1, children=1)

    monkeypatch.setattr(content_publication, "index_managed_content", fake_index)
    content_publication.run_content_publication(job_id)

    check = _db_connect(tmp_path / "app.sqlite")
    assert check.execute(
        "SELECT current_version_id FROM content_item_heads WHERE item_id=?",
        (uploaded.item_id,),
    ).fetchone()[0] == uploaded.version_id
    assert check.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (uploaded.version_id,)
    ).fetchone()[0] == "published"
    assert check.execute("SELECT status FROM content_index_jobs WHERE id=?", (job_id,)).fetchone()[0] == "done"
    check.close()
    snapshot = SQLitePublishedContentVisibility(tmp_path / "app.sqlite", "strict").snapshot()
    assert snapshot.allows(uploaded.version_id)
    assert snapshot.allows(None) is False


def test_server_batch_dry_run_maps_numbered_directory_without_writes(tmp_path):
    conn = _db(tmp_path)
    batch_root = tmp_path / "01"
    source_dir = batch_root / "03_公司内部标准"
    source_dir.mkdir(parents=True)
    (source_dir / "guide.md").write_text("# 指南", encoding="utf-8")
    batch_id, entries = import_server_batch(
        conn,
        ContentStorage(tmp_path / "content"),
        batch_root,
        actor_user_id=1,
        max_bytes=1024,
        apply=False,
    )
    assert batch_id is None
    assert entries[0].category_id == "cat-03"
    assert entries[0].needs_mapping is False
    assert entries[0].status == "planned"
    assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 0
    conn.close()


def test_unknown_server_folder_routes_to_pending_confirmation(tmp_path):
    conn = _db(tmp_path)
    category_id, needs_mapping = resolve_import_category(conn, ("未知目录",))
    assert category_id == "cat-99"
    assert needs_mapping is True
    conn.close()


def test_server_batch_apply_registers_items_for_review(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    batch_root = storage.inbox_root / "server" / "batch-001" / "05_培训资料"
    batch_root.mkdir(parents=True)
    (batch_root / "lesson.md").write_text("# 课程", encoding="utf-8")
    batch_id, entries = import_server_batch(
        conn,
        storage,
        storage.inbox_root / "server" / "batch-001",
        actor_user_id=actor,
        max_bytes=1024,
        apply=True,
    )
    assert batch_id
    assert entries[0].status == "imported"
    assert entries[0].category_id == "cat-05"
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (entries[0].version_id,)
    ).fetchone()[0] == "awaiting_review"
    conn.close()


def test_read_only_view_uses_category_code_and_published_head(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    payload = b"published"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    stored = StoredContentObject(
        digest,
        len(payload),
        "application/pdf",
        object_path.relative_to(storage.root).as_posix(),
        object_path,
        True,
    )
    batch = create_web_batch(conn, actor_user_id=actor)
    uploaded = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-01",
        title="规范",
        original_filename="standard.pdf",
        doc_type="pdf",
        stored=stored,
        actor_user_id=actor,
    )
    now = 10
    conn.execute(
        "INSERT INTO content_publications(id,version_id,status,publisher_id,created_at,updated_at,published_at) VALUES ('pub',?,'published',?,?,?,?)",
        (uploaded.version_id, actor, now, now, now),
    )
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,'pub',?)",
        (uploaded.item_id, uploaded.version_id, now),
    )
    conn.commit()
    assert rebuild_read_only_view(conn, storage) == 1
    exported = storage.views_root / "01_行业规范与标准" / "standard.pdf"
    assert exported.read_bytes() == payload
    assert not os.path.samefile(object_path, exported)
    assert object_path.stat().st_mode & 0o200
    conn.close()


def _db_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
