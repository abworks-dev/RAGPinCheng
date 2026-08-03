from __future__ import annotations

from dataclasses import dataclass

import pytest
from qdrant_client import models

from src import retrieve
from src.transcription.retrieval_visibility import PublishedTranscriptSnapshot

VISIBLE_VERSION = "123e4567-e89b-12d3-a456-426614174001"
HIDDEN_VERSION = "123e4567-e89b-12d3-a456-426614174002"


@dataclass
class _Point:
    id: str
    payload: dict[str, object]


class _Visibility:
    def __init__(self, snapshot: PublishedTranscriptSnapshot):
        self._snapshot = snapshot
        self.calls = 0

    def snapshot(self) -> PublishedTranscriptSnapshot:
        self.calls += 1
        return self._snapshot


def _parent(version_id: str | None) -> dict[str, object]:
    return {
        "doc_title": "Fixture",
        "category": "教学视频",
        "section_path": "",
        "source_path": "教学视频/_media/media.md",
        "text": "parent text",
        "doc_type": "transcript",
        "start_time": "00:00:00",
        "company": None,
        "media_id": "123e4567-e89b-12d3-a456-426614174000",
        "transcript_version_id": version_id,
    }


def test_parent_expansion_rechecks_same_snapshot_and_drops_version_mismatch(monkeypatch):
    point = _Point("child-1", {"parent_id": "parent-1", "text": "matched", "start_time": "00:00:01"})
    snapshot = PublishedTranscriptSnapshot(frozenset({VISIBLE_VERSION}))
    monkeypatch.setattr(retrieve, "fetch_parents", lambda _ids: {"parent-1": _parent(HIDDEN_VERSION)})

    assert retrieve._dedup_to_parents([(point, 1.0)], {"child-1": 0.5}, 5, snapshot) == []


def test_multi_query_reuses_one_visibility_snapshot_for_every_recall(monkeypatch):
    snapshot = PublishedTranscriptSnapshot(frozenset({VISIBLE_VERSION}))
    visibility = _Visibility(snapshot)
    seen: list[PublishedTranscriptSnapshot] = []

    def fake_recall(_query, _categories, recall_snapshot):
        seen.append(recall_snapshot)
        return [], {}

    monkeypatch.setattr(retrieve, "_recall_scored", fake_recall)
    assert retrieve.retrieve_multi(["first", "second"], "original", visibility=visibility) == []
    assert visibility.calls == 1
    assert seen == [snapshot, snapshot]
    assert seen[0] is seen[1]


def test_merge_filters_preserves_nested_filter_semantics():
    category = models.Filter(
        must=[models.FieldCondition(key="category", match=models.MatchValue(value="教学视频"))],
        must_not=[models.IsEmptyCondition(is_empty=models.PayloadField(key="category"))],
    )
    visibility = models.Filter(
        should=[models.IsEmptyCondition(is_empty=models.PayloadField(key="transcript_version_id"))]
    )

    merged = retrieve._merge_filters(category, visibility)

    assert merged is not None
    assert len(merged.must or []) == 2
    assert merged.must[0] == category
    assert merged.must[1] == visibility
    assert merged.must[0].must_not == category.must_not


@pytest.mark.parametrize("doc_type", ["pdf", "markdown", "docx", "xlsx", "pptx", "transcript"])
def test_unversioned_ordinary_and_legacy_parents_remain_visible(doc_type, monkeypatch):
    point = _Point("child-legacy", {"parent_id": "parent-legacy", "text": "ordinary"})
    parent = _parent(None)
    parent["doc_type"] = doc_type
    monkeypatch.setattr(retrieve, "fetch_parents", lambda _ids: {"parent-legacy": parent})
    snapshot = PublishedTranscriptSnapshot(frozenset())
    assert retrieve._dedup_to_parents([(point, 1.0)], {"child-legacy": 0.5}, 5, snapshot)
