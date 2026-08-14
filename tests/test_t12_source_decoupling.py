from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from scripts.preflight_source_decoupling_t12 import build_summary
from scripts.source_decoupling_t12 import (
    SourceDecouplingError,
    apply_plan,
    assert_expected_plan,
    build_plan,
    plan_sha256,
    verify_applied,
)


@dataclass
class Point:
    id: str
    payload: dict[str, object]


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


def databases() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    app = sqlite3.connect(":memory:")
    app.row_factory = sqlite3.Row
    app.executescript(
        """
        CREATE TABLE content_item_heads(item_id TEXT,current_version_id TEXT);
        CREATE TABLE content_index_jobs(id TEXT,status TEXT);
        CREATE TABLE index_jobs(id TEXT,status TEXT);
        CREATE TABLE transcript_publication_index_jobs(id TEXT,status TEXT);
        CREATE TABLE transcription_jobs(id TEXT,status TEXT);
        CREATE TABLE media_assets(media_id TEXT,status TEXT,updated_at INTEGER);
        CREATE TABLE media_transcript_heads(media_id TEXT,current_version_id TEXT);
        CREATE TABLE transcript_versions(
          id TEXT,source TEXT,markdown_storage_kind TEXT,publication_status TEXT
        );
        INSERT INTO content_item_heads VALUES ('item-1','managed-1');
        INSERT INTO media_assets VALUES
          ('media-1','ready',1),('media-2','failed',1);
        INSERT INTO media_transcript_heads VALUES ('media-1','transcript-1');
        INSERT INTO transcript_versions VALUES
          ('transcript-1','automatic','managed_artifact','published'),
          ('transcript-2','automatic','managed_artifact','published');
        INSERT INTO transcription_jobs VALUES ('transcription-job-1','done');
        INSERT INTO transcript_publication_index_jobs VALUES ('publish-job-1','done');
        """
    )
    parents = sqlite3.connect(":memory:")
    parents.row_factory = sqlite3.Row
    parents.executescript(
        """
        CREATE TABLE parents(
          parent_id TEXT PRIMARY KEY,content_item_id TEXT,content_version_id TEXT,
          transcript_version_id TEXT,media_id TEXT
        );
        INSERT INTO parents VALUES
          ('managed-parent','item-1','managed-1',NULL,NULL),
          ('legacy-parent',NULL,NULL,NULL,NULL),
          ('transcript-parent',NULL,NULL,'transcript-1','media-1');
        """
    )
    return app, parents


def qdrant() -> FakeQdrant:
    return FakeQdrant(
        [
            Point(
                "managed-point",
                {
                    "parent_id": "managed-parent",
                    "content_item_id": "item-1",
                    "content_version_id": "managed-1",
                },
            ),
            Point("legacy-point", {"parent_id": "legacy-parent"}),
            Point(
                "transcript-point",
                {
                    "parent_id": "transcript-parent",
                    "transcript_version_id": "transcript-1",
                    "media_id": "media-1",
                },
            ),
        ]
    )


def test_plan_matches_t12_a_digest_contract(tmp_path):
    app, parents = databases()
    client = qdrant()
    docs = tmp_path / "docs"
    media = tmp_path / "media"
    docs.mkdir()
    media.mkdir()
    preflight = build_summary(
        app,
        parents,
        client,
        collection="pincheng_docs",
        docs_root=docs,
        media_root=media,
        expected_managed_heads=1,
    )
    plan = build_plan(
        app,
        parents,
        client,
        collection="pincheng_docs",
        expected_managed_heads=1,
    )

    assert plan["plan_digest"] == preflight["retirement_candidates"]["plan_digest"]
    assert plan["candidates"]["parent_ids"] == ["legacy-parent", "transcript-parent"]
    assert plan["candidates"]["point_ids"] == ["legacy-point", "transcript-point"]
    assert plan["media"]["asset_count"] == 2
    assert plan["media"]["head_count"] == 1


def test_exact_frozen_counts_and_digest_fail_closed():
    app, parents = databases()
    plan = build_plan(app, parents, qdrant(), collection="pincheng_docs", expected_managed_heads=1)
    assert_expected_plan(
        plan,
        expected_plan_digest=plan["plan_digest"],
        expected_managed_heads=1,
        expected_candidate_parents=2,
        expected_candidate_points=2,
        expected_media_assets=2,
        expected_transcript_heads=1,
        expected_details={
            "managed_parents_current": 1,
            "managed_parents_noncurrent": 0,
            "managed_points_current": 1,
            "managed_points_noncurrent": 0,
            "parents_unversioned_legacy": 1,
            "parents_versioned_transcript": 1,
            "points_unversioned_legacy": 1,
            "points_versioned_transcript": 1,
            "media_statuses": {"failed": 1, "ready": 1},
            "transcript_versions": 2,
            "transcript_contracts": {"automatic|managed_artifact|published": 2},
            "parents_total": 3,
            "qdrant_points_total": 3,
        },
    )
    with pytest.raises(SourceDecouplingError, match="frozen_plan_mismatch"):
        assert_expected_plan(
            plan,
            expected_plan_digest=plan["plan_digest"],
            expected_managed_heads=1,
            expected_candidate_parents=2,
            expected_candidate_points=3,
            expected_media_assets=2,
            expected_transcript_heads=1,
        )


def test_apply_archives_media_removes_only_heads_and_candidate_index_rows():
    app, parents = databases()
    client = qdrant()
    plan = build_plan(app, parents, client, collection="pincheng_docs", expected_managed_heads=1)
    audit_before = plan["audit"]

    result = apply_plan(app, parents, client, plan, batch_size=1, updated_at=123)

    assert result.media_archived == 2
    assert result.transcript_heads_deleted == 1
    assert result.parents_deleted == 2
    assert result.points_deleted == 2
    assert client.deleted == ["legacy-point", "transcript-point"]
    assert [
        tuple(row)
        for row in app.execute("SELECT status,updated_at FROM media_assets ORDER BY media_id")
    ] == [
        ("archived", 123),
        ("archived", 123),
    ]
    assert app.execute("SELECT count(*) FROM media_transcript_heads").fetchone()[0] == 0
    assert {row[0] for row in parents.execute("SELECT parent_id FROM parents")} == {
        "managed-parent"
    }
    assert set(client.points) == {"managed-point"}
    assert plan["audit"] == audit_before
    assert verify_applied(app, parents, client, plan) == result


def test_active_jobs_or_identity_drift_stops_before_apply():
    app, parents = databases()
    app.execute("INSERT INTO transcription_jobs VALUES ('active-job','running')")
    with pytest.raises(SourceDecouplingError, match="active_jobs_present"):
        build_plan(app, parents, qdrant(), collection="pincheng_docs", expected_managed_heads=1)

    app, parents = databases()
    plan = build_plan(app, parents, qdrant(), collection="pincheng_docs", expected_managed_heads=1)
    app.execute("UPDATE media_assets SET status='uploaded' WHERE media_id='media-1'")
    changed = build_plan(app, parents, qdrant(), collection="pincheng_docs", expected_managed_heads=1)
    assert plan_sha256(changed) != plan_sha256(plan)


def test_verify_rejects_loss_of_audit_history():
    app, parents = databases()
    client = qdrant()
    plan = build_plan(app, parents, client, collection="pincheng_docs", expected_managed_heads=1)
    apply_plan(app, parents, client, plan)
    app.execute("DELETE FROM transcript_versions WHERE id='transcript-2'")
    with pytest.raises(SourceDecouplingError, match="audit_table_changed:transcript_versions"):
        verify_applied(app, parents, client, plan)
