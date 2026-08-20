from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.content_permissions import has_content_permission
from api.content_permission_catalog import (
    LEGACY_CONTENT_PERMISSION_MAP,
    SYSTEM_CONTENT_PERMISSION_GROUPS,
)
from api.content_import import import_server_batch, resolve_import_category
from api.content_storage import ContentStorage, StoredContentObject
from api.content_store import archive_content_item, create_category, create_content_revision, create_web_batch, delete_category, force_delete_category, get_category_delete_preview, get_category_force_delete_preview, list_categories, move_category, move_content_item, register_uploaded_document, restore_content_item
from api.content_store import (
    create_publication_job,
    review_version,
    submit_version_for_review,
)
from api import content_publication
from api import content_trash_cleanup
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


def test_category_delete_removes_empty_subtree_and_renumbers_siblings(tmp_path):
    conn = _db(tmp_path)
    first = create_category(conn, category_key=None, parent_id="cat-04", display_code="01", display_name="保留一", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=1)
    target = create_category(conn, category_key=None, parent_id="cat-04", display_code="02", display_name="待删除", sort_order=20, target_position=2, confirm_number_shift=True, actor_user_id=1)
    child = create_category(conn, category_key=None, parent_id=target["id"], display_code="01", display_name="空子目录", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=1)
    last = create_category(conn, category_key=None, parent_id="cat-04", display_code="03", display_name="保留二", sort_order=30, target_position=3, confirm_number_shift=True, actor_user_id=1)
    conn.execute(
        """INSERT INTO category_import_aliases
           (id,parent_category_id,folder_name,target_category_id,created_at,updated_at)
           VALUES ('alias-delete-test',NULL,'待删除别名',?,1,1)""",
        (target["id"],),
    )
    conn.commit()

    preview = get_category_delete_preview(conn, target["id"])
    assert preview["can_delete"] is True
    assert preview["descendant_count"] == 1
    assert preview["renumbered_sibling_count"] == 1
    result = delete_category(conn, target["id"], expected_version=target["version"], confirmed=True, actor_user_id=1)

    assert result["deleted_folder_count"] == 2
    assert [tuple(row) for row in conn.execute(
        "SELECT id,display_code FROM category_nodes WHERE parent_id='cat-04' AND deleted_at IS NULL ORDER BY sort_order"
    )] == [(first["id"], "01"), (last["id"], "02")]
    assert conn.execute("SELECT is_active FROM category_import_aliases WHERE id='alias-delete-test'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM category_nodes WHERE id IN (?,?) AND deleted_at IS NOT NULL", (target["id"], child["id"])).fetchone()[0] == 2
    assert all(row["id"] not in {target["id"], child["id"]} for row in list_categories(conn, include_inactive=True))
    conn.close()


def test_category_move_updates_every_descendant_beyond_four_levels(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    moving_root = create_category(
        conn,
        category_key="moving_root",
        parent_id="cat-03",
        display_code="01",
        display_name="待移动树",
        sort_order=10,
        actor_user_id=actor,
    )
    moving_child = create_category(
        conn,
        category_key="moving_child",
        parent_id=moving_root["id"],
        display_code="01",
        display_name="子目录",
        sort_order=10,
        actor_user_id=actor,
    )
    moving_leaf = create_category(
        conn,
        category_key="moving_leaf",
        parent_id=moving_child["id"],
        display_code="01",
        display_name="叶目录",
        sort_order=10,
        actor_user_id=actor,
    )
    target_parent = "cat-04"
    for level in range(2, 6):
        target = create_category(
            conn,
            category_key=f"move_target_level_{level}",
            parent_id=target_parent,
            display_code="01",
            display_name=f"目标第{level}级",
            sort_order=level,
            actor_user_id=actor,
        )
        target_parent = target["id"]

    rows = move_category(
        conn,
        moving_root["id"],
        target_parent_id=target_parent,
        before_category_id=None,
        expected_version=moving_root["version"],
        actor_user_id=actor,
    )

    levels = {row["id"]: row["level"] for row in rows}
    assert levels[moving_root["id"]] == 6
    assert levels[moving_child["id"]] == 7
    assert levels[moving_leaf["id"]] == 8
    assert conn.execute(
        "SELECT parent_id FROM category_nodes WHERE id=?", (moving_root["id"],)
    ).fetchone()[0] == target_parent
    conn.close()


def test_category_delete_is_blocked_by_archived_content(tmp_path):
    conn = _db(tmp_path)
    target = create_category(conn, category_key=None, parent_id="cat-04", display_code="01", display_name="有回收站资料", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=1)
    conn.execute(
        """INSERT INTO content_items
           (id,title,content_kind,category_id,created_by,created_at,updated_at,archived_at)
           VALUES ('item-delete-blocker','历史资料','document',?,1,1,1,2)""",
        (target["id"],),
    )
    conn.commit()
    preview = get_category_delete_preview(conn, target["id"])
    assert preview["can_delete"] is False
    assert preview["content_count"] == 1
    with pytest.raises(ValueError, match="category_delete_blocked"):
        delete_category(conn, target["id"], expected_version=target["version"], confirmed=True, actor_user_id=1)
    assert conn.execute("SELECT deleted_at FROM category_nodes WHERE id=?", (target["id"],)).fetchone()[0] is None
    conn.close()


def test_category_force_delete_purges_documents_tasks_batches_and_renumbers(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    conn.execute("PRAGMA foreign_keys=ON")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    target = create_category(conn, category_key=None, parent_id="cat-04", display_code="01", display_name="待强删", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=actor)
    child = create_category(conn, category_key=None, parent_id=target["id"], display_code="01", display_name="子目录", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=actor)
    sibling = create_category(conn, category_key=None, parent_id="cat-04", display_code="02", display_name="保留", sort_order=20, target_position=2, confirm_number_shift=True, actor_user_id=actor)
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()

    def upload(category_id: str, filename: str, payload: bytes):
        digest = hashlib.sha256(payload).hexdigest()
        object_path = storage.object_path_for_sha256(digest)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(payload)
        batch_id = create_web_batch(conn, actor_user_id=actor, target_category_id=category_id)
        batch_path = storage.root / f"inbox/web/{batch_id}"
        batch_path.mkdir(parents=True, exist_ok=True)
        (batch_path / "staged.upload").write_bytes(payload)
        uploaded = register_uploaded_document(
            conn, batch_id=batch_id, category_id=category_id, title=filename,
            original_filename=filename, doc_type="markdown",
            stored=StoredContentObject(digest, len(payload), "text/markdown",
                                       object_path.relative_to(storage.root).as_posix(), object_path, True),
            actor_user_id=actor,
        )
        return batch_id, uploaded, object_path

    active_batch, active, active_object = upload(target["id"], "active.md", b"active")
    archived_batch, archived, archived_object = upload(child["id"], "archived.md", b"archived")
    shared_batch = create_web_batch(conn, actor_user_id=actor, target_category_id="cat-03")
    shared = register_uploaded_document(
        conn, batch_id=shared_batch, category_id="cat-03", title="共享对象",
        original_filename="shared.md", doc_type="markdown",
        stored=StoredContentObject(
            hashlib.sha256(b"active").hexdigest(), len(b"active"), "text/markdown",
            active_object.relative_to(storage.root).as_posix(), active_object, False,
        ),
        actor_user_id=actor,
    )
    archive_content_item(
        conn, archived.item_id, expected_version_id=archived.version_id, actor_user_id=actor,
        can_archive_draft=True, can_archive_published=True,
    )
    conn.execute("UPDATE content_versions SET lifecycle_status='publishing' WHERE id=?", (active.version_id,))
    conn.execute(
        "INSERT INTO content_publications(id,version_id,status,publisher_id,created_at,updated_at) VALUES ('pub-force',?,'indexing',?,1,1)",
        (active.version_id, actor),
    )
    conn.execute(
        """INSERT INTO content_index_jobs
           (id,publication_id,version_id,attempt_number,target_index_id,status,created_at,updated_at)
           VALUES ('job-force','pub-force',?,1,'target-force','embedding',1,1)""",
        (active.version_id,),
    )
    conn.commit()

    preview = get_category_force_delete_preview(conn, target["id"])
    assert (preview["active_content_count"], preview["archived_content_count"]) == (1, 1)
    assert (preview["active_index_count"], preview["upload_batch_count"]) == (1, 2)
    with pytest.raises(ValueError, match="category_force_delete_path_confirmation_required"):
        force_delete_category(
            conn, target["id"], expected_version=target["version"], confirmed=True,
            typed_path="错误路径", actor_user_id=actor,
        )
    assert conn.execute("SELECT deleted_at FROM category_nodes WHERE id=?", (target["id"],)).fetchone()[0] is None

    monkeypatch.setattr(content_trash_cleanup, "_storage", storage)
    monkeypatch.setattr(content_trash_cleanup, "_delete_external", lambda *_args: (3, 2))
    result = force_delete_category(
        conn, target["id"], expected_version=target["version"], confirmed=True,
        typed_path=preview["full_path"], actor_user_id=actor,
    )

    assert result["cleanup_status"] == "succeeded"
    assert result["deleted_item_count"] == 2
    assert result["deleted_upload_batch_count"] == 2
    assert result["deleted_index_job_count"] == 1
    assert result["qdrant_point_count"] == 6
    assert conn.execute("SELECT count(*) FROM content_items WHERE id IN (?,?)", (active.item_id, archived.item_id)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM upload_batches WHERE id IN (?,?)", (active_batch, archived_batch)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM content_audit_events WHERE batch_id IN (?,?)", (active_batch, archived_batch)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM category_nodes WHERE id IN (?,?) AND deleted_at IS NOT NULL", (target["id"], child["id"])).fetchone()[0] == 2
    assert conn.execute("SELECT display_code FROM category_nodes WHERE id=?", (sibling["id"],)).fetchone()[0] == "01"
    assert active_object.exists() and not archived_object.exists()
    assert conn.execute("SELECT 1 FROM content_items WHERE id=?", (shared.item_id,)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM upload_batches WHERE id=?", (shared_batch,)).fetchone() is not None
    assert not (storage.root / f"inbox/web/{active_batch}").exists()
    run = conn.execute("SELECT status,item_count,upload_batch_count FROM category_force_delete_runs WHERE id=?", (result["run_id"],)).fetchone()
    assert tuple(run) == ("succeeded", 2, 2)
    assert conn.execute("SELECT 1 FROM content_audit_events WHERE event_type='category.force_deleted'").fetchone() is not None
    conn.close()


def test_category_force_delete_protects_system_roots_and_media_transcripts(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    protected = get_category_force_delete_preview(conn, "cat-04")
    assert protected["protected_category"] is True
    assert protected["can_force_delete"] is False
    with pytest.raises(ValueError, match="category_force_delete_protected"):
        force_delete_category(
            conn, "cat-04", expected_version=protected["version"], confirmed=True,
            typed_path=protected["full_path"], actor_user_id=actor,
        )

    target = create_category(conn, category_key=None, parent_id="cat-04", display_code="01", display_name="含转录稿", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=actor)
    conn.execute(
        """INSERT INTO content_items(id,title,content_kind,category_id,created_by,created_at,updated_at)
           VALUES ('media-force-block','转录稿','media_transcript',?, ?,1,1)""",
        (target["id"], actor),
    )
    conn.commit()
    preview = get_category_force_delete_preview(conn, target["id"])
    assert preview["media_transcript_count"] == 1
    assert preview["can_force_delete"] is False
    with pytest.raises(ValueError, match="category_force_delete_media_blocked"):
        force_delete_category(
            conn, target["id"], expected_version=target["version"], confirmed=True,
            typed_path=preview["full_path"], actor_user_id=actor,
        )
    conn.close()


def test_category_force_delete_records_partial_cleanup_instead_of_leaving_running(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    target = create_category(conn, category_key=None, parent_id="cat-04", display_code="01", display_name="部分清理", sort_order=10, target_position=1, confirm_number_shift=True, actor_user_id=actor)
    batch_id = create_web_batch(conn, actor_user_id=actor, target_category_id=target["id"])
    conn.execute("UPDATE upload_batches SET storage_rel_path='objects/not-batch-owned' WHERE id=?", (batch_id,))
    conn.commit()
    monkeypatch.setattr(content_trash_cleanup, "_storage", ContentStorage(tmp_path / "content"))
    preview = get_category_force_delete_preview(conn, target["id"])

    result = force_delete_category(
        conn, target["id"], expected_version=target["version"], confirmed=True,
        typed_path=preview["full_path"], actor_user_id=actor,
    )

    assert result["cleanup_status"] == "partial"
    assert result["cleanup_error_count"] == 1
    run = conn.execute(
        "SELECT status,error_summary,finished_at FROM category_force_delete_runs WHERE id=?",
        (result["run_id"],),
    ).fetchone()
    assert run["status"] == "partial"
    assert run["error_summary"] == "batch:ValueError"
    assert run["finished_at"] is not None
    assert conn.execute("SELECT 1 FROM upload_batches WHERE id=?", (batch_id,)).fetchone() is not None
    conn.close()


def test_migration_seeds_permission_group_templates(tmp_path):
    conn = _db(tmp_path)
    groups = routes_content.list_permission_groups(
        CurrentUser(999, "admin", "管理员", "admin", "csrf"), conn
    )
    expected_order = ["member", "viewer", "bim_engineer", "content_owner", "publisher", "category_admin", "system_admin"]
    assert [(group.group_key, group.display_name, group.permissions) for group in groups] == [
            (key, SYSTEM_CONTENT_PERMISSION_GROUPS[key][0], sorted(SYSTEM_CONTENT_PERMISSION_GROUPS[key][1] - {"item.submit", "item.review", "item.move_review"}))
        for key in expected_order
    ]
    conn.close()


def test_custom_permission_group_is_a_template_not_a_user_binding(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(routes_content, "CONTENT_MANAGEMENT_ENABLED", True)
    actor = CurrentUser(999, "admin", "管理员", "admin", "csrf")
    created = routes_content.create_permission_group(
        CreateContentPermissionGroupRequest(
            display_name="项目发布员",
            permissions=sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"]),
        ), actor, conn
    )
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    routes_content.put_content_permissions(
        user_id, UpdateContentPermissionsRequest(permissions=created.permissions), actor, conn
    )
    routes_content.update_permission_group(
        created.id,
        UpdateContentPermissionGroupRequest(permissions=sorted(LEGACY_CONTENT_PERMISSION_MAP["review"])),
        actor,
        conn,
    )
    actual = [row[0] for row in conn.execute(
        "SELECT permission FROM content_permissions WHERE user_id=?", (user_id,)
    )]
    assert actual == sorted(LEGACY_CONTENT_PERMISSION_MAP["publish"])
    conn.close()


def test_content_permission_is_additive_and_admin_has_fallback(tmp_path):
    conn = _db(tmp_path)
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    user = SimpleNamespace(id=user_id, role="user")
    admin = SimpleNamespace(id=999, role="admin")
    assert has_content_permission(conn, user, "item.upload") is False
    conn.execute(
        "INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,'item.upload',1)",
        (user_id,),
    )
    assert has_content_permission(conn, user, "item.upload") is True
    assert has_content_permission(conn, admin, "item.publish") is True
    conn.close()


def test_admin_can_assign_scoped_content_permissions(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    monkeypatch.setattr(routes_content, "CONTENT_MANAGEMENT_ENABLED", True)
    actor = CurrentUser(999, "admin", "管理员", "admin", "csrf")
    requested = sorted(
        LEGACY_CONTENT_PERMISSION_MAP["organize"] | LEGACY_CONTENT_PERMISSION_MAP["review"]
    )
    result = routes_content.put_content_permissions(
        user_id,
        UpdateContentPermissionsRequest(permissions=requested),
        actor,
        conn,
    )
    assert result.permissions == requested
    user = SimpleNamespace(id=user_id, role="user")
    assert has_content_permission(conn, user, "item.review") is True
    assert has_content_permission(conn, user, "item.publish") is False
    conn.close()


def test_category_creation_supports_depth_beyond_four(tmp_path):
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
    row = create_category(
        conn,
        category_key="level_5",
        parent_id=parent,
        display_code="5",
        display_name="第五级",
        sort_order=5,
        actor_user_id=actor,
    )
    assert row["level"] == 5
    conn.close()


def test_category_listing_is_depth_first_with_stable_sibling_order(tmp_path):
    conn = _db(tmp_path)
    rows = [
        ("cat-sort-first", "sort_first", "cat-03", "01", "第一项", 5, 2),
        ("cat-sort-grandchild", "sort_grandchild", "cat-sort-first", "01", "子项", 1, 3),
        ("cat-sort-code-02", "sort_code_02", "cat-03", "02", "第二项", 10, 2),
        ("cat-sort-code-03", "sort_code_03", "cat-03", "03", "第三项", 10, 2),
        ("cat-sort-code", "sort_code", "cat-03", "10", "编号靠后", 10, 2),
    ]
    conn.executemany(
        """INSERT INTO category_nodes
           (id,category_key,parent_id,display_code,display_name,sort_order,level,is_active,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,1,1,1)""",
        rows,
    )
    conn.commit()

    inserted_ids = {row[0] for row in rows}
    actual = [row["id"] for row in list_categories(conn) if row["id"] in inserted_ids]

    assert actual == [
        "cat-sort-first",
        "cat-sort-grandchild",
        "cat-sort-code-02",
        "cat-sort-code-03",
        "cat-sort-code",
    ]
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


def test_revision_keeps_published_head_until_the_new_version_is_promoted(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    batch = create_web_batch(conn, actor_user_id=actor)
    payload = b"published-content"
    digest = hashlib.sha256(payload).hexdigest()
    stored = StoredContentObject(
        sha256=digest,
        size_bytes=len(payload),
        mime_type="application/pdf",
        storage_rel_path=f"objects/sha256/{digest[:2]}/{digest}",
        absolute_path=tmp_path / digest,
        created=True,
    )
    uploaded = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-03",
        title="已发布资料",
        original_filename="published.pdf",
        doc_type="pdf",
        stored=stored,
        actor_user_id=actor,
    )
    conn.execute(
        "UPDATE content_versions SET lifecycle_status='published' WHERE id=?",
        (uploaded.version_id,),
    )
    conn.execute(
        """INSERT INTO content_publications
           (id,version_id,status,publisher_id,created_at,updated_at,published_at)
           VALUES ('publication-head',?,'published',?,1,1,1)""",
        (uploaded.version_id, actor),
    )
    conn.execute(
        """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
           VALUES (?,?,'publication-head',1)""",
        (uploaded.item_id, uploaded.version_id),
    )
    conn.commit()

    revised = create_content_revision(
        conn,
        uploaded.item_id,
        expected_version_id=uploaded.version_id,
        title="重命名后的资料",
        original_filename="renamed.pdf",
        actor_user_id=actor,
        can_revise=True,
        can_archive_draft=True,
        can_archive_published=False,
    )

    assert conn.execute(
        "SELECT current_version_id FROM content_item_heads WHERE item_id=?",
        (uploaded.item_id,),
    ).fetchone()[0] == uploaded.version_id
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (uploaded.version_id,)
    ).fetchone()[0] == "published"
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (revised.version_id,)
    ).fetchone()[0] == "draft"
    with pytest.raises(ValueError, match="content_delete_forbidden"):
        archive_content_item(
            conn,
            uploaded.item_id,
            expected_version_id=revised.version_id,
            actor_user_id=actor,
            can_archive_draft=True,
            can_archive_published=False,
        )
    with pytest.raises(ValueError, match="content_move_requires_republication"):
        move_content_item(
            conn,
            uploaded.item_id,
            target_category_id="cat-04",
            expected_version_id=revised.version_id,
            actor_user_id=actor,
            can_move_draft=True,
            can_move_review=False,
        )
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


def test_force_deleted_publication_job_cannot_resume_or_promote(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    payload = b"# Cancelled publication"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    uploaded = register_uploaded_document(
        conn, batch_id=create_web_batch(conn, actor_user_id=actor), category_id="cat-03",
        title="取消发布", original_filename="cancelled.md", doc_type="markdown",
        stored=StoredContentObject(digest, len(payload), "text/markdown",
                                   object_path.relative_to(storage.root).as_posix(), object_path, True),
        actor_user_id=actor,
    )
    submit_version_for_review(conn, uploaded.version_id, actor_user_id=actor)
    review_version(conn, uploaded.version_id, approved=True, note="确认", category_id=None, actor_user_id=actor)
    _publication_id, job_id = create_publication_job(conn, uploaded.version_id, actor_user_id=actor)
    conn.close()

    monkeypatch.setattr(content_publication, "connect", lambda: _db_connect(tmp_path / "app.sqlite"))
    monkeypatch.setattr(content_publication, "_storage", storage)
    cleaned: list[str] = []
    monkeypatch.setattr(content_publication, "_delete_cancelled_artifacts", lambda row: cleaned.append(str(row["version_id"])))

    def cancelled_index(_path, _doc_type, _metadata, on_status):
        on_status("parsing")
        check = _db_connect(tmp_path / "app.sqlite")
        check.execute(
            "UPDATE content_index_jobs SET status='failed',error_code='category_force_deleted' WHERE id=?",
            (job_id,),
        )
        check.commit()
        check.close()
        on_status("embedding")
        raise AssertionError("cancelled indexing must stop at the next stage boundary")

    monkeypatch.setattr(content_publication, "index_managed_content", cancelled_index)
    content_publication.run_content_publication(job_id)

    check = _db_connect(tmp_path / "app.sqlite")
    assert tuple(check.execute("SELECT status,error_code FROM content_index_jobs WHERE id=?", (job_id,)).fetchone()) == ("failed", "category_force_deleted")
    assert check.execute("SELECT count(*) FROM content_item_heads WHERE item_id=?", (uploaded.item_id,)).fetchone()[0] == 0
    assert cleaned == [uploaded.version_id]
    check.close()


def test_archiving_published_content_withdraws_head_but_preserves_history_and_object(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    payload = b"# Published synthetic content"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    uploaded = register_uploaded_document(
        conn,
        batch_id=create_web_batch(conn, actor_user_id=actor),
        category_id="cat-03",
        title="待删除已发布资料",
        original_filename="published.md",
        doc_type="markdown",
        stored=StoredContentObject(
            digest,
            len(payload),
            "text/markdown",
            object_path.relative_to(storage.root).as_posix(),
            object_path,
            True,
        ),
        actor_user_id=actor,
    )
    conn.execute(
        "UPDATE content_versions SET lifecycle_status='published' WHERE id=?",
        (uploaded.version_id,),
    )
    conn.execute(
        """INSERT INTO content_publications
           (id,version_id,status,publisher_id,created_at,updated_at,published_at)
           VALUES ('publication-delete',?,'published',?,1,1,1)""",
        (uploaded.version_id, actor),
    )
    conn.execute(
        """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
           VALUES (?,?,'publication-delete',1)""",
        (uploaded.item_id, uploaded.version_id),
    )
    conn.commit()

    result = archive_content_item(
        conn,
        uploaded.item_id,
        expected_version_id=uploaded.version_id,
        actor_user_id=actor,
        can_archive_draft=False,
        can_archive_published=True,
    )
    assert result.publication_withdrawn is True
    assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM content_publications WHERE id='publication-delete'"
    ).fetchone()[0] == "withdrawn"
    assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM content_objects").fetchone()[0] == 1
    assert object_path.read_bytes() == payload

    restored = restore_content_item(
        conn,
        uploaded.item_id,
        expected_version_id=uploaded.version_id,
        actor_user_id=actor,
        can_restore=True,
    )
    assert restored.restored_status == "approved"
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (uploaded.version_id,)
    ).fetchone()[0] == "approved"
    assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
    conn.close()

    snapshot = SQLitePublishedContentVisibility(tmp_path / "app.sqlite", "strict").snapshot()
    assert snapshot.allows(uploaded.version_id) is False


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


def test_server_batch_skips_office_when_processing_is_disabled(tmp_path, monkeypatch):
    from api import content_import

    conn = _db(tmp_path)
    batch_root = tmp_path / "office-disabled"
    source_dir = batch_root / "03_公司内部标准"
    source_dir.mkdir(parents=True)
    (source_dir / "guide.docx").write_bytes(b"not-read")
    monkeypatch.setattr(content_import, "OFFICE_PROCESSING_ENABLED", False)

    batch_id, entries = content_import.import_server_batch(
        conn,
        ContentStorage(tmp_path / "content"),
        batch_root,
        actor_user_id=1,
        max_bytes=1024,
        apply=False,
    )

    assert batch_id is None
    assert entries[0].status == "skipped"
    assert entries[0].reason == "office_processing_disabled"
    assert entries[0].category_id == "cat-03"
    assert conn.execute("SELECT count(*) FROM content_items").fetchone()[0] == 0
    conn.close()


def test_unknown_server_folder_routes_to_pending_confirmation(tmp_path):
    conn = _db(tmp_path)
    category_id, needs_mapping = resolve_import_category(conn, ("未知目录",))
    assert category_id == "cat-99"
    assert needs_mapping is True
    conn.close()


def test_server_import_resolves_category_paths_beyond_four_levels(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    parent = "cat-01"
    folder_names = []
    for level in range(2, 7):
        folder_name = f"第{level}级"
        row = create_category(
            conn,
            category_key=f"import_level_{level}",
            parent_id=parent,
            display_code="01",
            display_name=folder_name,
            sort_order=level,
            actor_user_id=actor,
        )
        parent = row["id"]
        folder_names.append(folder_name)

    category_id, needs_mapping = resolve_import_category(
        conn,
        ("01_行业规范与标准", *folder_names),
    )

    assert category_id == parent
    assert needs_mapping is False
    conn.close()


def test_server_batch_apply_imports_into_category_beyond_four_levels(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    parent = "cat-01"
    folder_names = ["01_行业规范与标准"]
    for level in range(2, 7):
        folder_name = f"第{level}级"
        row = create_category(
            conn,
            category_key=f"apply_import_level_{level}",
            parent_id=parent,
            display_code="01",
            display_name=folder_name,
            sort_order=level,
            actor_user_id=actor,
        )
        parent = row["id"]
        folder_names.append(folder_name)
    batch_root = storage.inbox_root / "server" / "batch-deep"
    source_dir = batch_root.joinpath(*folder_names)
    source_dir.mkdir(parents=True)
    (source_dir / "guide.md").write_text("# 深层指南", encoding="utf-8")

    batch_id, entries = import_server_batch(
        conn,
        storage,
        batch_root,
        actor_user_id=actor,
        max_bytes=1024,
        apply=True,
    )

    assert batch_id
    assert len(entries) == 1
    assert entries[0].status == "imported"
    assert entries[0].category_id == parent
    assert entries[0].needs_mapping is False
    stored = conn.execute(
        "SELECT category_id FROM content_items WHERE id=?", (entries[0].item_id,)
    ).fetchone()
    assert stored[0] == parent
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (entries[0].version_id,)
    ).fetchone()[0] == "awaiting_review"
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


def test_read_only_view_exports_published_content_beyond_four_levels(tmp_path):
    conn = _db(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u1'").fetchone()[0]
    parent = "cat-01"
    expected_parts = ["01_行业规范与标准"]
    for level in range(2, 7):
        row = create_category(
            conn,
            category_key=f"view_level_{level}",
            parent_id=parent,
            display_code="01",
            display_name=f"第{level}级",
            sort_order=level,
            actor_user_id=actor,
        )
        parent = row["id"]
        expected_parts.append(f"01_第{level}级")

    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    payload = b"deep published"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    uploaded = register_uploaded_document(
        conn,
        batch_id=create_web_batch(conn, actor_user_id=actor),
        category_id=parent,
        title="深层资料",
        original_filename="deep.pdf",
        doc_type="pdf",
        stored=StoredContentObject(
            digest,
            len(payload),
            "application/pdf",
            object_path.relative_to(storage.root).as_posix(),
            object_path,
            True,
        ),
        actor_user_id=actor,
    )
    conn.execute(
        "INSERT INTO content_publications(id,version_id,status,publisher_id,created_at,updated_at,published_at) VALUES ('deep-pub',?,'published',?,?,?,?)",
        (uploaded.version_id, actor, 10, 10, 10),
    )
    conn.execute(
        "INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,'deep-pub',10)",
        (uploaded.item_id, uploaded.version_id),
    )
    conn.commit()

    assert rebuild_read_only_view(conn, storage) == 1
    assert storage.views_root.joinpath(*expected_parts, "deep.pdf").read_bytes() == payload
    conn.close()


def test_create_publication_job_accepts_a_newly_uploaded_draft(tmp_path):
    conn = _db(tmp_path)
    actor = 1
    storage = ContentStorage(tmp_path / "objects")
    payload = b"# direct publish"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    uploaded = register_uploaded_document(
        conn,
        batch_id=create_web_batch(conn, actor_user_id=actor),
        category_id="cat-03",
        title="直接发布",
        original_filename="direct.md",
        doc_type="markdown",
        stored=StoredContentObject(
            sha256=digest,
            size_bytes=len(payload),
            mime_type="text/markdown",
            storage_rel_path=object_path.relative_to(storage.root).as_posix(),
            absolute_path=object_path,
            created=True,
        ),
        actor_user_id=actor,
    )

    publication_id, job_id = create_publication_job(conn, uploaded.version_id, actor_user_id=actor)

    assert publication_id.startswith("publication-")
    assert job_id.startswith("content-index-")
    assert conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (uploaded.version_id,)
    ).fetchone()[0] == "publishing"
    conn.close()


def _db_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
