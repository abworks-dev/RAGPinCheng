from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.legacy_index_retirement import (
    LegacyIndexRetirementError,
    apply_plan,
    build_plan,
    plan_sha256,
    verify_retired,
    write_plan,
)


class Point:
    def __init__(self, point_id: str, payload: dict[str, object]):
        self.id = point_id
        self.payload = payload


class FakeQdrant:
    def __init__(self, points: list[Point]):
        self.points = {point.id: point for point in points}
        self.deleted: list[str] = []

    def scroll(self, **_kwargs):
        return list(self.points.values()), None

    def delete(self, *, points_selector, **_kwargs):
        for point_id in points_selector.points:
            self.deleted.append(point_id)
            self.points.pop(point_id, None)


def app_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE content_items(id TEXT PRIMARY KEY, archived_at INTEGER);
        CREATE TABLE content_versions(
          id TEXT PRIMARY KEY,item_id TEXT,source_rel_path TEXT,original_filename TEXT,
          source_origin TEXT,lifecycle_status TEXT
        );
        CREATE TABLE content_item_heads(item_id TEXT,current_version_id TEXT);
        CREATE TABLE content_publications(
          id TEXT,version_id TEXT,status TEXT,created_at INTEGER
        );
        CREATE TABLE content_index_jobs(
          id TEXT,version_id TEXT,status TEXT,attempt_number INTEGER,created_at INTEGER
        );
        CREATE TABLE index_jobs(id TEXT,status TEXT);
        CREATE TABLE transcript_publication_index_jobs(id TEXT,status TEXT);
        CREATE TABLE transcription_jobs(id TEXT,status TEXT);
        INSERT INTO content_items VALUES ('item-1',NULL),('preview',1);
        INSERT INTO content_versions VALUES
          ('version-1','item-1','公司标准/a.pdf','a.pdf','legacy','published'),
          ('version-preview','preview','公司标准/a.preview.pdf','a.preview.pdf','legacy','publication_failed');
        INSERT INTO content_item_heads VALUES ('item-1','version-1');
        INSERT INTO content_publications VALUES ('pub','version-1','published',1);
        INSERT INTO content_index_jobs VALUES ('job','version-1','done',1,1);
        """
    )
    return connection


def parents_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE parents(
          parent_id TEXT PRIMARY KEY,source_path TEXT,
          content_item_id TEXT,content_version_id TEXT,transcript_version_id TEXT
        );
        INSERT INTO parents VALUES
          ('legacy-parent','/app/docs/公司标准/a.pdf',NULL,NULL,NULL),
          ('preview-parent','/app/docs/公司标准/a.preview.pdf',NULL,NULL,NULL),
          ('managed-parent','content://item-1/version-1','item-1','version-1',NULL),
          ('video-parent','/app/docs/教学视频/transcript.md',NULL,NULL,'transcript-1'),
          ('unrelated-parent','/app/docs/未迁移/other.pdf',NULL,NULL,NULL);
        """
    )
    return connection


def qdrant() -> FakeQdrant:
    return FakeQdrant(
        [
            Point("legacy-point", {"parent_id": "legacy-parent", "source_path": "/app/docs/公司标准/a.pdf"}),
            Point("preview-point", {"parent_id": "preview-parent", "source_path": "/app/docs/公司标准/a.preview.pdf"}),
            Point("managed-point", {"parent_id": "managed-parent", "source_path": "content://item-1/version-1", "content_version_id": "version-1"}),
            Point("video-point", {"parent_id": "video-parent", "source_path": "/app/docs/教学视频/transcript.md", "transcript_version_id": "transcript-1"}),
            Point("unrelated-point", {"parent_id": "unrelated-parent", "source_path": "/app/docs/未迁移/other.pdf"}),
        ]
    )


def test_plan_selects_only_matching_unversioned_document_and_preview():
    plan = build_plan(
        app_database(),
        parents_database(),
        qdrant(),
        collection="pincheng_docs",
        legacy_docs_root="/app/docs",
        expected_head_count=1,
        expected_archived_preview_count=1,
    )
    assert plan["managed"]["version_ids"] == ["version-1"]
    assert plan["candidates"]["parent_ids"] == ["legacy-parent", "preview-parent"]
    assert plan["candidates"]["point_ids"] == ["legacy-point", "preview-point"]
    assert plan["candidates"]["source_count"] == 2


def test_plan_fails_closed_for_active_jobs_and_managed_identity_conflicts():
    app = app_database()
    app.execute("INSERT INTO transcription_jobs VALUES ('active','running')")
    with pytest.raises(LegacyIndexRetirementError, match="active_jobs_present"):
        build_plan(app, parents_database(), qdrant(), collection="pincheng_docs", legacy_docs_root="/app/docs", expected_head_count=1, expected_archived_preview_count=1)

    app = app_database()
    parents = parents_database()
    parents.execute("UPDATE parents SET content_version_id='wrong' WHERE parent_id='legacy-parent'")
    with pytest.raises(LegacyIndexRetirementError, match="candidate_parent_identity_conflict"):
        build_plan(app, parents, qdrant(), collection="pincheng_docs", legacy_docs_root="/app/docs", expected_head_count=1, expected_archived_preview_count=1)


def test_plan_requires_exact_archived_preview_count():
    with pytest.raises(LegacyIndexRetirementError, match="archived_preview_count_mismatch:1:0"):
        build_plan(
            app_database(),
            parents_database(),
            qdrant(),
            collection="pincheng_docs",
            legacy_docs_root="/app/docs",
            expected_head_count=1,
            expected_archived_preview_count=0,
        )


def test_apply_uses_explicit_ids_and_preserves_managed_video_and_unrelated_rows():
    app = app_database()
    parents = parents_database()
    client = qdrant()
    plan = build_plan(app, parents, client, collection="pincheng_docs", legacy_docs_root="/app/docs", expected_head_count=1, expected_archived_preview_count=1)
    result = apply_plan(parents, client, plan, batch_size=1)
    assert result.parent_count == 2
    assert result.point_count == 2
    assert client.deleted == ["legacy-point", "preview-point"]
    assert {row[0] for row in parents.execute("SELECT parent_id FROM parents")} == {
        "managed-parent", "video-parent", "unrelated-parent"
    }
    assert set(client.points) == {"managed-point", "video-point", "unrelated-point"}
    assert verify_retired(parents, client, plan) == result


def test_plan_fingerprint_detects_any_identity_drift(tmp_path: Path):
    plan = build_plan(app_database(), parents_database(), qdrant(), collection="pincheng_docs", legacy_docs_root="/app/docs", expected_head_count=1, expected_archived_preview_count=1)
    output = tmp_path / "plan.json"
    write_plan(output, plan)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert plan_sha256(loaded) == plan_sha256(plan)
    loaded["candidates"]["point_ids"].append("unexpected")
    assert plan_sha256(loaded) != plan_sha256(plan)
