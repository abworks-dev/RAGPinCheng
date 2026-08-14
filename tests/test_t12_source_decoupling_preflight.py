from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from scripts.preflight_source_decoupling_t12 import PreflightError, build_summary


@dataclass
class _Point:
    id: str
    payload: dict[str, object]


class _Qdrant:
    def __init__(self, points: list[_Point]):
        self.points = points

    def scroll(self, **_kwargs):
        return self.points, None


def _databases():
    app = sqlite3.connect(":memory:")
    app.row_factory = sqlite3.Row
    app.executescript(
        """
        CREATE TABLE content_item_heads(item_id TEXT,current_version_id TEXT);
        CREATE TABLE content_index_jobs(status TEXT);
        CREATE TABLE index_jobs(status TEXT);
        CREATE TABLE transcript_publication_index_jobs(status TEXT);
        CREATE TABLE transcription_jobs(status TEXT);
        CREATE TABLE media_assets(media_id TEXT,status TEXT);
        CREATE TABLE media_transcript_heads(media_id TEXT,current_version_id TEXT);
        CREATE TABLE transcript_versions(
            id TEXT,source TEXT,markdown_storage_kind TEXT,publication_status TEXT
        );
        INSERT INTO content_item_heads VALUES ('item-1','managed-1');
        INSERT INTO media_assets VALUES ('media-1','ready');
        INSERT INTO media_transcript_heads VALUES ('media-1','transcript-1');
        INSERT INTO transcript_versions VALUES (
            'transcript-1','automatic','managed_artifact','published'
        );
        """
    )
    parents = sqlite3.connect(":memory:")
    parents.row_factory = sqlite3.Row
    parents.executescript(
        """
        CREATE TABLE parents(
            parent_id TEXT,content_item_id TEXT,content_version_id TEXT,
            transcript_version_id TEXT,media_id TEXT
        );
        INSERT INTO parents VALUES ('managed-parent','item-1','managed-1',NULL,NULL);
        INSERT INTO parents VALUES ('legacy-parent',NULL,NULL,NULL,NULL);
        INSERT INTO parents VALUES (
            'transcript-parent',NULL,NULL,'transcript-1','media-1'
        );
        """
    )
    return app, parents


def _points():
    return [
        _Point(
            "managed-point",
            {
                "parent_id": "managed-parent",
                "content_item_id": "item-1",
                "content_version_id": "managed-1",
            },
        ),
        _Point("legacy-point", {"parent_id": "legacy-parent"}),
        _Point(
            "transcript-point",
            {
                "parent_id": "transcript-parent",
                "transcript_version_id": "transcript-1",
                "media_id": "media-1",
            },
        ),
    ]


def test_preflight_returns_only_aggregates_and_stable_plan_digest(tmp_path):
    app, parents = _databases()
    docs = tmp_path / "docs"
    media = tmp_path / "media"
    docs.mkdir()
    media.mkdir()
    (docs / "sample.pdf").write_bytes(b"pdf")
    (media / "sample.mp4").write_bytes(b"video")

    summary = build_summary(
        app,
        parents,
        _Qdrant(_points()),
        collection="pincheng_docs",
        docs_root=docs,
        media_root=media,
        expected_managed_heads=1,
    )

    assert summary["status"] == "ready"
    assert summary["managed"]["head_versions"] == 1
    assert summary["retirement_candidates"]["parents_total"] == 2
    assert summary["retirement_candidates"]["points_total"] == 2
    assert len(summary["retirement_candidates"]["plan_digest"]) == 64
    assert summary["legacy_files"]["docs"] == {
        "files": 1,
        "directories": 0,
        "symlinks": 0,
        "bytes": 3,
    }
    assert "sample.pdf" not in str(summary)
    assert "legacy-parent" not in str(summary)


def test_preflight_fails_closed_when_a_job_is_active(tmp_path):
    app, parents = _databases()
    app.execute("INSERT INTO transcription_jobs VALUES ('running')")
    docs = tmp_path / "docs"
    media = tmp_path / "media"
    docs.mkdir()
    media.mkdir()

    with pytest.raises(PreflightError, match="active_jobs_present"):
        build_summary(
            app,
            parents,
            _Qdrant(_points()),
            collection="pincheng_docs",
            docs_root=docs,
            media_root=media,
            expected_managed_heads=1,
        )


def test_preflight_fails_when_candidate_point_identity_disagrees(tmp_path):
    app, parents = _databases()
    points = _points()
    points[-1].payload["transcript_version_id"] = None
    docs = tmp_path / "docs"
    media = tmp_path / "media"
    docs.mkdir()
    media.mkdir()

    with pytest.raises(PreflightError, match="candidate_point_parent_identity_mismatch"):
        build_summary(
            app,
            parents,
            _Qdrant(points),
            collection="pincheng_docs",
            docs_root=docs,
            media_root=media,
            expected_managed_heads=1,
        )
