from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.content_reclassification as reclassification
import api.db as app_db
from api.content_storage import ContentStorage, StoredContentObject
from api.content_store import create_web_batch, register_uploaded_document
from api.content_view import rebuild_read_only_view


class FakeQdrant:
    def __init__(self, points: list[SimpleNamespace]) -> None:
        self.points = points

    def collection_exists(self, _collection: str) -> bool:
        return True

    def scroll(self, **_kwargs):
        return self.points, None

    def set_payload(self, *, payload, points, **_kwargs) -> None:
        selected = {str(point_id) for point_id in points}
        for point in self.points:
            if str(point.id) in selected:
                point.payload.update(payload)


def _published_document(tmp_path: Path, monkeypatch):
    app_path = tmp_path / "app.sqlite"
    parents_path = tmp_path / "parents.sqlite"
    storage = ContentStorage(tmp_path / "content")
    app_db.init_db(app_path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(app_db, "APP_DB_PATH", app_path)
    monkeypatch.setattr(reclassification, "PARENTS_DB", parents_path)
    monkeypatch.setattr(reclassification, "_storage", storage)

    conn = app_db.connect(app_path)
    actor = conn.execute(
        """INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES ('publisher','发布人员','x','admin',1,1)"""
    ).lastrowid
    payload = b"published-content"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = storage.object_path_for_sha256(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(payload)
    batch = create_web_batch(conn, actor_user_id=actor)
    uploaded = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-01",
        title="正式资料",
        original_filename="published.pdf",
        doc_type="pdf",
        stored=StoredContentObject(
            sha256=digest,
            size_bytes=len(payload),
            mime_type="application/pdf",
            storage_rel_path=object_path.relative_to(storage.root).as_posix(),
            absolute_path=object_path,
            created=True,
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
           VALUES ('publication',?,'published',?,1,1,1)""",
        (uploaded.version_id, actor),
    )
    conn.execute(
        """INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at)
           VALUES (?,?,'publication',1)""",
        (uploaded.item_id, uploaded.version_id),
    )
    conn.commit()
    rebuild_read_only_view(conn, storage)

    parents = sqlite3.connect(parents_path)
    parents.execute(
        """CREATE TABLE parents (
            parent_id TEXT PRIMARY KEY,content_item_id TEXT,content_version_id TEXT,
            category TEXT,category_key TEXT
        )"""
    )
    parents.executemany(
        """INSERT INTO parents
           (parent_id,content_item_id,content_version_id,category,category_key)
           VALUES (?,?,?,?,?)""",
        [
            ("parent-1", uploaded.item_id, uploaded.version_id, "行业规范与标准", "industry_standards"),
            ("parent-2", uploaded.item_id, uploaded.version_id, "行业规范与标准", "industry_standards"),
        ],
    )
    parents.commit()
    parents.close()
    points = [
        SimpleNamespace(
            id=f"point-{index}",
            vector={"dense": [float(index)]},
            payload={
                "content_item_id": uploaded.item_id,
                "content_version_id": uploaded.version_id,
                "category": "行业规范与标准",
                "category_key": "industry_standards",
            },
        )
        for index in (1, 2, 3)
    ]
    qdrant = FakeQdrant(points)
    monkeypatch.setattr(reclassification, "_client", lambda: qdrant)
    return conn, actor, uploaded, storage, parents_path, qdrant


def _create_job(conn, actor, uploaded):
    return reclassification.create_reclassification_job(
        conn,
        uploaded.item_id,
        target_category_id="cat-02",
        expected_version_id=uploaded.version_id,
        actor_user_id=actor,
        can_reclassify=True,
    )


def test_reclassification_updates_only_category_metadata(tmp_path, monkeypatch):
    conn, actor, uploaded, storage, parents_path, qdrant = _published_document(tmp_path, monkeypatch)
    before_vectors = {point.id: point.vector for point in qdrant.points}
    before_head = tuple(conn.execute(
        "SELECT current_version_id,publication_id FROM content_item_heads WHERE item_id=?",
        (uploaded.item_id,),
    ).fetchone())
    job = _create_job(conn, actor, uploaded)

    reclassification.run_content_reclassification(job["id"])

    completed = conn.execute(
        "SELECT * FROM content_reclassification_jobs WHERE id=?", (job["id"],)
    ).fetchone()
    assert completed["status"] == "succeeded"
    assert completed["qdrant_point_count"] == 3
    assert completed["parent_count"] == 2
    assert conn.execute(
        "SELECT category_id FROM content_items WHERE id=?", (uploaded.item_id,)
    ).fetchone()[0] == "cat-02"
    assert tuple(conn.execute(
        "SELECT current_version_id,publication_id FROM content_item_heads WHERE item_id=?",
        (uploaded.item_id,),
    ).fetchone()) == before_head
    assert {point.id: point.vector for point in qdrant.points} == before_vectors
    assert all(point.payload["category_key"] == "client_requirements" for point in qdrant.points)
    parents = sqlite3.connect(parents_path)
    assert parents.execute(
        "SELECT count(*) FROM parents WHERE category_key='client_requirements'"
    ).fetchone()[0] == 2
    parents.close()
    exports = list(storage.views_root.rglob("published.pdf"))
    assert len(exports) == 1
    assert "02_客户标准与要求" in exports[0].as_posix()
    assert conn.execute(
        "SELECT count(*) FROM content_audit_events WHERE event_type='content.reclassified'"
    ).fetchone()[0] == 1
    conn.close()


def test_activation_failure_restores_every_store(tmp_path, monkeypatch):
    conn, actor, uploaded, storage, parents_path, qdrant = _published_document(tmp_path, monkeypatch)
    job = _create_job(conn, actor, uploaded)
    monkeypatch.setattr(
        reclassification,
        "activate_prepared_read_only_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("activation failed")),
    )

    reclassification.run_content_reclassification(job["id"])

    failed = conn.execute(
        "SELECT status,error_code FROM content_reclassification_jobs WHERE id=?", (job["id"],)
    ).fetchone()
    assert tuple(failed) == ("failed", "reclassification_view_failed")
    assert conn.execute(
        "SELECT category_id FROM content_items WHERE id=?", (uploaded.item_id,)
    ).fetchone()[0] == "cat-01"
    assert all(point.payload["category_key"] == "industry_standards" for point in qdrant.points)
    parents = sqlite3.connect(parents_path)
    assert parents.execute(
        "SELECT count(*) FROM parents WHERE category_key='industry_standards'"
    ).fetchone()[0] == 2
    parents.close()
    exports = list(storage.views_root.rglob("published.pdf"))
    assert len(exports) == 1
    assert "01_行业规范与标准" in exports[0].as_posix()
    assert conn.execute(
        "SELECT count(*) FROM content_audit_events WHERE event_type='content.reclassified'"
    ).fetchone()[0] == 0
    conn.close()


def test_boot_recovery_rolls_back_nonterminal_job(tmp_path, monkeypatch):
    conn, actor, uploaded, _storage, parents_path, qdrant = _published_document(tmp_path, monkeypatch)
    job = _create_job(conn, actor, uploaded)
    conn.execute(
        """UPDATE content_reclassification_jobs
           SET status='committing',qdrant_applied=1,parents_applied=1,item_committed=1
           WHERE id=?""",
        (job["id"],),
    )
    conn.execute("UPDATE content_items SET category_id='cat-02' WHERE id=?", (uploaded.item_id,))
    conn.commit()
    for point in qdrant.points:
        point.payload.update(category="客户标准与要求", category_key="client_requirements")
    parents = sqlite3.connect(parents_path)
    parents.execute(
        "UPDATE parents SET category='客户标准与要求',category_key='client_requirements'"
    )
    parents.commit()
    parents.close()

    reclassification.recover_reclassifications_on_boot(lambda _job_id: pytest.fail("must not resume"))

    assert conn.execute(
        "SELECT status FROM content_reclassification_jobs WHERE id=?", (job["id"],)
    ).fetchone()[0] == "failed"
    assert conn.execute(
        "SELECT category_id FROM content_items WHERE id=?", (uploaded.item_id,)
    ).fetchone()[0] == "cat-01"
    assert all(point.payload["category_key"] == "industry_standards" for point in qdrant.points)
    conn.close()
