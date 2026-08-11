from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from src import index as index_module
from src import indexing_pipeline, retrieve
from src.chunk import Parent
from src.content_retrieval_visibility import PublishedContentSnapshot
from src.indexing_pipeline import ManagedIndexMetadata
from src.transcription_retrieval_visibility import PublishedTranscriptSnapshot


class _QdrantCapture:
    def __init__(self) -> None:
        self.points = []

    def upsert(self, *, collection_name, points) -> None:
        self.points.extend(points)


def test_managed_identity_reaches_parent_store_and_qdrant_payload(tmp_path, monkeypatch):
    source = tmp_path / "object"
    source.write_text("# Scope\n\nManaged indexing contract.", encoding="utf-8")
    metadata = ManagedIndexMetadata(
        content_item_id="item-1",
        content_version_id="version-1",
        publication_target_id="target-1",
        category_key="company_standards",
        category_display_name="公司内部标准",
        doc_title="管理标准",
        source_ref="content://item-1/version-1",
    )
    captured: dict[str, list] = {}
    monkeypatch.setattr(
        indexing_pipeline, "store_parents", lambda rows: captured.setdefault("parents", rows)
    )
    monkeypatch.setattr(
        indexing_pipeline, "index_children", lambda rows: captured.setdefault("children", rows)
    )

    result = indexing_pipeline.index_managed_content(source, "markdown", metadata)
    parents = captured["parents"]
    children = captured["children"]
    assert result.parents == len(parents) > 0
    assert result.children == len(children) > 0
    assert {
        (row.content_item_id, row.content_version_id, row.category_key, row.source_path)
        for row in [*parents, *children]
    } == {("item-1", "version-1", "company_standards", "content://item-1/version-1")}

    parents_db = tmp_path / "parents.sqlite"
    monkeypatch.setattr(index_module, "PARENTS_DB", parents_db)
    index_module.store_parents(parents)
    stored = index_module.fetch_parents([parents[0].parent_id])[parents[0].parent_id]
    assert stored["content_item_id"] == "item-1"
    assert stored["content_version_id"] == "version-1"
    assert stored["category_key"] == "company_standards"

    client = _QdrantCapture()
    monkeypatch.setattr(index_module, "_client", lambda: client)
    monkeypatch.setattr(index_module, "_ensure_collection", lambda _client, reset=False: True)
    monkeypatch.setattr(
        index_module,
        "encode",
        lambda texts: [
            SimpleNamespace(dense=[0.1, 0.2], sparse_indices=[1], sparse_values=[0.3])
            for _text in texts
        ],
    )
    index_module.index_children(children)
    payload = client.points[0].payload
    assert payload["content_item_id"] == "item-1"
    assert payload["content_version_id"] == "version-1"
    assert payload["category_key"] == "company_standards"
    assert payload["publication_target_id"] == "target-1"


def test_parent_expansion_enforces_content_head_and_uses_current_category_label(monkeypatch):
    point = SimpleNamespace(id="child", payload={"parent_id": "parent", "text": "match"})
    parent = {
        "doc_title": "Managed",
        "category": "旧名称",
        "section_path": "Scope",
        "source_path": "content://item/version-old",
        "text": "evidence",
        "doc_type": "markdown",
        "content_item_id": "item",
        "content_version_id": "version-old",
        "category_key": "company_standards",
    }
    monkeypatch.setattr(retrieve, "fetch_parents", lambda _ids: {"parent": parent})
    transcript = PublishedTranscriptSnapshot(frozenset())
    hidden = PublishedContentSnapshot(frozenset({"version-current"}), "strict")
    assert retrieve._dedup_to_parents(
        [(point, 1.0)], {"child": 0.5}, 5, transcript, hidden
    ) == []

    current = PublishedContentSnapshot(frozenset({"version-old"}), "strict")
    result = retrieve._dedup_to_parents(
        [(point, 1.0)],
        {"child": 0.5},
        5,
        transcript,
        current,
        {"company_standards": "公司内部标准"},
    )
    assert result[0].category == "公司内部标准"
    assert result[0].content_version_id == "version-old"


def test_managed_source_reference_resolves_to_content_object(tmp_path, monkeypatch):
    from api import routes
    from api.content_storage import ContentStorage
    from api.content_store import create_web_batch, register_uploaded_document
    from api.db import connect, init_db
    from api.content_storage import StoredContentObject

    app_db = tmp_path / "app.sqlite"
    init_db(app_db, backup_dir=tmp_path / "backups")
    conn = connect(app_db)
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('u','U','x','admin',1,1)"
    )
    actor = conn.execute("SELECT id FROM users WHERE employee_id='u'").fetchone()[0]
    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    object_path = storage.object_path_for_sha256("a" * 64)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(b"pdf fixture")
    batch = create_web_batch(conn, actor_user_id=actor)
    uploaded = register_uploaded_document(
        conn,
        batch_id=batch,
        category_id="cat-01",
        title="Standard",
        original_filename="standard.pdf",
        doc_type="pdf",
        stored=StoredContentObject(
            "a" * 64,
            object_path.stat().st_size,
            "application/pdf",
            object_path.relative_to(storage.root).as_posix(),
            object_path,
            True,
        ),
        actor_user_id=actor,
    )
    conn.close()

    parents_db = tmp_path / "parents.sqlite"
    monkeypatch.setattr(index_module, "PARENTS_DB", parents_db)
    index_module.store_parents(
        [
            Parent(
                parent_id="parent-1",
                text="evidence",
                doc_title="Standard",
                category="行业规范与标准",
                section_path="Scope",
                source_path=f"content://{uploaded.item_id}/{uploaded.version_id}",
                content_item_id=uploaded.item_id,
                content_version_id=uploaded.version_id,
            )
        ]
    )
    monkeypatch.setattr(routes, "APP_DB_PATH", app_db)
    monkeypatch.setattr(routes, "PARENTS_DB", parents_db)
    monkeypatch.setattr(routes, "_content_storage", storage)

    raw = routes.get_source_file("parent-1", 1)
    assert Path(raw.path) == object_path
    assert "standard.pdf" in raw.headers["content-disposition"]
    pdf = routes.get_pdf("parent-1", 1)
    assert Path(pdf.path) == object_path
