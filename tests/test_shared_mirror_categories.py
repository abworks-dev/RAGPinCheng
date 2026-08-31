from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from api.content_store import (
    create_category,
    is_shared_mirror_category,
    move_category,
    rename_category,
    update_category,
    update_category_number,
)
from api.db import connect, init_db
from api.external_media import (
    materialize_shared_folder_mirrors,
    reconcile_source,
    resolve_shared_category_key,
)
from api.knowledge_scope import resolve_category_scope


def _shared_environment(tmp_path: Path, *, share_layout: dict[str, bytes]) -> tuple[sqlite3.Connection, str, str]:
    """Build a share dir, a linked shared-folder root category and a source."""
    share = tmp_path / "share"
    for rel, content in share_layout.items():
        target = share / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES (1,'mirror-admin','Mirror Admin','x','admin',1,1)"
    )
    used_codes = {
        str(row["display_code"])
        for row in conn.execute(
            "SELECT display_code FROM category_nodes WHERE parent_id IS NULL AND deleted_at IS NULL"
        ).fetchall()
    }
    display_code = next(f"{i:02d}" for i in range(1, 100) if f"{i:02d}" not in used_codes)
    root = create_category(
        conn, category_key=None, parent_id=None,
        display_code=display_code, display_name="共享培训镜像测试", sort_order=990, actor_user_id=1,
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
        (source_id, "共享培训源", "share", "", root["id"], scheme["id"], 1, 1),
    )
    conn.execute(
        """UPDATE category_nodes SET category_kind='shared_folder', external_source_id=?,
                  version=version+1 WHERE id=?""",
        (source_id, root["id"]),
    )
    conn.commit()
    return conn, source_id, str(root["id"])


def _mirror_rows(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """WITH RECURSIVE sub(id) AS (
               SELECT c.id FROM category_nodes c
               JOIN external_media_sources s ON s.id=c.external_source_id
               WHERE s.id=?
               UNION ALL
               SELECT c.id FROM category_nodes c JOIN sub s ON c.parent_id=s.id
           )
           SELECT id,parent_id,display_code,display_name,sort_order,external_relative_path,
                  category_kind,external_source_id,is_active,deleted_at,version
           FROM category_nodes c
           WHERE c.id IN (SELECT id FROM sub)
             AND c.external_relative_path IS NOT NULL
           ORDER BY c.external_relative_path""",
        (source_id,),
    ).fetchall()


def test_reconcile_materializes_mirror_tree_and_keeps_media_on_root(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1", "A/B/lesson2.mp4": b"v2", "root.mp4": b"root"},
    )
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": tmp_path / "share"}, now=10)
        mirrors = _mirror_rows(conn, source_id)
        by_path = {str(row["external_relative_path"]): row for row in mirrors}
        assert set(by_path) == {"A", "A/B"}
        assert by_path["A"]["parent_id"] == root_id
        assert by_path["A"]["display_code"] == "01"
        assert by_path["A/B"]["parent_id"] == by_path["A"]["id"]
        assert by_path["A/B"]["display_code"] == "01"
        # mirrors carry no external_source_id themselves; only the root does
        assert by_path["A"]["external_source_id"] is None
        assert by_path["A"]["category_kind"] == "shared_folder"
        assert by_path["A"]["is_active"] == 1
        assert by_path["A"]["deleted_at"] is None
        # media still attach to the shared root, not to mirrors
        root_link = conn.execute(
            "SELECT count(*) FROM media_assets WHERE target_category_id=?", (root_id,)
        ).fetchone()[0]
        assert root_link == 3
    finally:
        conn.close()


def test_reconcile_mirror_append_and_remove_vanished_folder(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1", "B/lesson2.mp4": b"v2"},
    )
    share = tmp_path / "share"
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        first = _mirror_rows(conn, source_id)
        a_id = next(str(r["id"]) for r in first if r["external_relative_path"] == "A")
        a_version = conn.execute("SELECT version FROM category_nodes WHERE id=?", (a_id,)).fetchone()[0]

        # A remote folder is removed, C appears, and a new file lands under A.
        (share / "A" / "lesson1.mp4").unlink()
        (share / "C").mkdir()
        (share / "C" / "lesson3.mp4").write_bytes(b"v3")
        (share / "A" / "lesson4.mp4").write_bytes(b"v4")
        reconcile_source(conn, source_id, trigger_type="scheduled", roots={"share": share}, now=20)

        mirrors = _mirror_rows(conn, source_id)
        by_path = {str(r["external_relative_path"]): r for r in mirrors if r["deleted_at"] is None}
        assert set(by_path) == {"A", "B", "C"}
        # surviving mirrors were not rewritten by the scan
        assert conn.execute("SELECT version FROM category_nodes WHERE id=?", (a_id,)).fetchone()[0] == a_version
        assert by_path["A"]["display_code"] == "01"
        # new remote folders are appended at the end of their sibling group
        assert by_path["C"]["display_code"] == "03"
        # the tree is fully mirrored at every level: no nested directory is dropped
        assert by_path["B"]["deleted_at"] is None
    finally:
        conn.close()


def test_reconcile_soft_deletes_vanished_subtree_and_renumbers(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1", "A/B/lesson2.mp4": b"v2", "C/lesson3.mp4": b"v3"},
    )
    share = tmp_path / "share"
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        # folder A (and its subtree) disappears remotely
        import shutil
        shutil.rmtree(share / "A")
        reconcile_source(conn, source_id, trigger_type="scheduled", roots={"share": share}, now=20)

        rows = _mirror_rows(conn, source_id)
        by_path = {str(r["external_relative_path"]): r for r in rows}
        assert by_path["A"]["deleted_at"] is not None
        assert by_path["A/B"]["deleted_at"] is not None
        assert by_path["C"]["deleted_at"] is None
        # C was renumbered to fill the gap left by A
        assert by_path["C"]["display_code"] == "01"
    finally:
        conn.close()


def test_mirror_guards_block_structure_mutations_but_allow_numbering(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1", "B/lesson2.mp4": b"v2"},
    )
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": tmp_path / "share"}, now=10)
        mirrors = _mirror_rows(conn, source_id)
        a = next(r for r in mirrors if r["external_relative_path"] == "A")
        a_id, a_version = str(a["id"]), int(a["version"])
        assert is_shared_mirror_category(conn, a_id)

        with pytest.raises(ValueError, match="shared_folder_mirror_move_blocked"):
            move_category(conn, a_id, target_parent_id=None, before_category_id=None, expected_version=a_version, actor_user_id=1)
        with pytest.raises(ValueError, match="shared_folder_mirror_rename_blocked"):
            rename_category(conn, a_id, display_name="改名", expected_version=a_version, actor_user_id=1)
        with pytest.raises(ValueError, match="shared_folder_mirror_rename_blocked"):
            update_category(
                conn, a_id, display_code=str(a["display_code"]), display_name="改名",
                sort_order=int(a["sort_order"]), is_active=True, expected_version=a_version, actor_user_id=1,
            )
        with pytest.raises(ValueError, match="shared_folder_mirror_parent_forbidden"):
            create_category(
                conn, category_key=None, parent_id=a_id,
                display_code="01", display_name="子目录", sort_order=10, actor_user_id=1,
            )
        ordinary = create_category(
            conn, category_key=None, parent_id=root_id,
            display_code="03", display_name="普通目录", sort_order=30, actor_user_id=1,
        )
        with pytest.raises(ValueError, match="shared_folder_mirror_parent_forbidden"):
            move_category(
                conn, str(ordinary["id"]), target_parent_id=a_id, before_category_id=None,
                expected_version=int(ordinary["version"]), actor_user_id=1,
            )

        # chat settings and status remain editable on a mirror
        updated = update_category(
            conn, a_id, display_code=str(a["display_code"]), display_name=str(a["display_name"]),
            sort_order=int(a["sort_order"]), is_active=True, chat_search_enabled=False,
            expected_version=int(a["version"]), actor_user_id=1,
        )
        assert updated["chat_search_enabled"] == 0

        # numbering adjustment is allowed (local display order only)
        siblings = conn.execute(
            "SELECT id FROM category_nodes WHERE parent_id=? AND deleted_at IS NULL ORDER BY sort_order",
            (root_id,),
        ).fetchall()
        assert len(siblings) == 3
        reordered = update_category_number(
            conn, a_id, target_position=2, confirm_number_shift=True,
            expected_version=int(conn.execute("SELECT version FROM category_nodes WHERE id=?", (a_id,)).fetchone()[0]),
            actor_user_id=1,
        )
        assert any(str(row["id"]) == a_id and str(row["display_code"]) == "02" for row in reordered)
    finally:
        conn.close()


def test_resolve_shared_category_key_resolves_mirror_root_and_fallback(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1", "A/B/lesson2.mp4": b"v2", "root.mp4": b"root"},
    )
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": tmp_path / "share"}, now=10)
        by_media = {
            str(r["media_id"]): str(r["relative_path"])
            for r in conn.execute("SELECT media_id,relative_path FROM external_media_entries").fetchall()
        }
        root_key = conn.execute("SELECT category_key FROM category_nodes WHERE id=?", (root_id,)).fetchone()[0]
        key_by_path = {
            path: conn.execute(
                "SELECT category_key FROM category_nodes WHERE external_relative_path=? AND deleted_at IS NULL",
                (path,),
            ).fetchone()[0]
            for path in ("A", "A/B")
        }
        # videos directly at the shared root resolve to the root key
        root_video = next(media for media, path in by_media.items() if "/" not in path)
        assert resolve_shared_category_key(conn, root_video) == root_key
        # videos under a materialized mirror resolve to the deepest mirror's key
        deep_video = next(media for media, path in by_media.items() if path.startswith("A/B/"))
        assert resolve_shared_category_key(conn, deep_video) == key_by_path["A/B"]
        # unknown media resolves to None
        assert resolve_shared_category_key(conn, "no-such-media") is None
        # a video whose folder is not yet materialized falls back to the root key
        conn.execute(
            """INSERT INTO media_assets(
                   media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
                   transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,
                   target_category_id,normalized_title,normalized_original_filename,storage_kind
               ) VALUES (?,?,?,?,'video/mp4',?,NULL,NULL,'generated','uploaded',1,1,1,?,NULL,NULL,'external')""",
            ("unmaterialized-media", "lesson", "lesson.mp4", "external/x/y", 1, root_id),
        )
        conn.execute(
            "INSERT INTO external_media_entries(id,source_id,media_id,relative_path,parent_relative_path,filename,file_size,modified_ns,fingerprint,availability,discovered_at,last_seen_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'available',1,1,1)",
            (
                str(uuid.uuid4()), source_id, "unmaterialized-media", "全新课程/lesson.mp4", "全新课程",
                "lesson.mp4", 1, 1, "fp-unmaterialized",
            ),
        )
        conn.commit()
        assert resolve_shared_category_key(conn, "unmaterialized-media") == root_key
    finally:
        conn.close()


def test_mirror_participates_in_knowledge_scope_and_respects_disabling(tmp_path: Path) -> None:
    conn, source_id, root_id = _shared_environment(
        tmp_path,
        share_layout={"A/lesson1.mp4": b"v1"},
    )
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": tmp_path / "share"}, now=10)
        root_key = conn.execute("SELECT category_key FROM category_nodes WHERE id=?", (root_id,)).fetchone()[0]
        mirror = _mirror_rows(conn, source_id)[0]
        mirror_key = conn.execute(
            "SELECT category_key FROM category_nodes WHERE id=?", (mirror["id"],)
        ).fetchone()[0]

        scoped = resolve_category_scope(conn, [root_key])
        assert root_key in scoped
        assert mirror_key in scoped

        # disabling chat search on the mirror removes it from the all-scope
        create_category(
            conn, category_key=None, parent_id=root_id,
            display_code="02", display_name="对照分类", sort_order=20, actor_user_id=1,
        )
        conn.execute(
            "UPDATE category_nodes SET chat_search_enabled=0,version=version+1 WHERE id=?",
            (mirror["id"],),
        )
        conn.commit()
        all_keys = resolve_category_scope(conn, None)
        assert root_key in all_keys
        assert mirror_key not in all_keys
    finally:
        conn.close()