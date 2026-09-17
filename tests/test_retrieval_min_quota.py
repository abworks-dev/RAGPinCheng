"""Tests for the transcript min-quota reservation in result dedup.

Ensures that when specs/standards outrank teaching-video fragments, at least
TRANSCRIPT_MIN_QUOTA transcript parents still reach the final top-k."""
from __future__ import annotations

import pytest

from src import config, retrieve as retrieve_mod
from src.retrieve import _dedup_to_parents
from src.transcription_retrieval_visibility import PublishedTranscriptSnapshot


class _Payload:
    def __init__(self, parent_id: str, doc_type: str = "pdf", category_key: str | None = None):
        self.payload = {"parent_id": parent_id, "doc_type": doc_type, "text": "片段正文" * 10, "category_key": category_key}
        self.id = parent_id


def _snapshot(version_ids: set[str] | None = None) -> PublishedTranscriptSnapshot:
    return PublishedTranscriptSnapshot(frozenset(version_ids or set()))


def _scores(items: list[tuple[str, str]]) -> list:
    # (parent_id, doc_type) best-first
    scored = []
    child_rrf = {}
    for i, (pid, dtype) in enumerate(items):
        point = _Payload(pid, dtype)
        scored.append((point, 1.0 - i * 0.01))
        child_rrf[pid] = 1.0
    return scored, child_rrf


def test_transcript_min_quota_reserves_a_transcript_slot(monkeypatch):
    items = [(f"spec-{i}", "pdf") for i in range(4)] + [("video-1", "transcript")]
    scored, child_rrf = _scores(items)
    monkeypatch.setattr(retrieve_mod, "TRANSCRIPT_MIN_QUOTA", 1)

    def fake_fetch(parent_ids):
        return {pid: {"parent_id": pid, "doc_title": f"doc-{pid}", "category": "x", "section_path": "s", "source_path": "p", "text": "t", "doc_type": "transcript" if "video" in pid else "pdf", "start_time": None, "company": None, "media_id": "m", "sheet_name": None, "cell_range": None, "slide_number": None, "paragraph_anchor": None, "page_number": None, "page_end": None, "topic_id": None, "heading_anchor": None, "location_quote": None, "location_confidence": None, "transcript_version_id": None, "content_item_id": None, "content_version_id": None, "category_key": "ck"} for pid in parent_ids}
    monkeypatch.setattr(retrieve_mod, "fetch_parents", fake_fetch)

    results = _dedup_to_parents(scored, child_rrf, top_k=4, snapshot=_snapshot())
    # Even though 4 specs filled the cap first, the transcript slot is reserved,
    # so the returned set includes the training video.
    assert any(r.doc_type == "transcript" for r in results)


def test_without_quota_transcript_may_be_dropped(monkeypatch):
    items = [(f"spec-{i}", "pdf") for i in range(4)] + [("video-1", "transcript")]
    scored, child_rrf = _scores(items)
    monkeypatch.setattr(retrieve_mod, "TRANSCRIPT_MIN_QUOTA", 0)

    def fake_fetch(parent_ids):
        return {pid: {"parent_id": pid, "doc_title": f"doc-{pid}", "category": "x", "section_path": "s", "source_path": "p", "text": "t", "doc_type": "transcript" if "video" in pid else "pdf", "start_time": None, "company": None, "media_id": "m", "sheet_name": None, "cell_range": None, "slide_number": None, "paragraph_anchor": None, "page_number": None, "page_end": None, "topic_id": None, "heading_anchor": None, "location_quote": None, "location_confidence": None, "transcript_version_id": None, "content_item_id": None, "content_version_id": None, "category_key": "ck"} for pid in parent_ids}
    monkeypatch.setattr(retrieve_mod, "fetch_parents", fake_fetch)

    results = _dedup_to_parents(scored, child_rrf, top_k=4, snapshot=_snapshot())
    assert all(r.doc_type != "transcript" for r in results)  # quota=0 → open