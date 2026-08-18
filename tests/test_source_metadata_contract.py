from __future__ import annotations

import dataclasses

from api.conversation_runtime import (
    _retrieved_parents_from_json,
    _retrieved_parents_to_json,
)
from api.schemas import source_to_dto
from src.retrieve import RetrievedParent
from src.session import ChatSession


def _parent() -> RetrievedParent:
    return RetrievedParent(
        parent_id="parent-1",
        doc_title="公司统计表",
        category="公司内部标准",
        section_path="内部 > 设备统计",
        source_path="content://item-1/version-1",
        text="引用正文",
        score=0.91,
        matched_children=["设备统计"],
        doc_type="xlsx",
        company="品茗股份",
        media_id="media-1",
        sheet_name="统计表",
        cell_range="B2:F20",
        slide_number=8,
        paragraph_anchor="第 3.2 节",
        transcript_version_id="transcript-1",
        content_item_id="item-1",
        content_version_id="version-1",
        category_key="company_standards",
        rrf_score=0.82,
        subquery_idx=2,
    )


def test_source_ui_and_dto_preserve_company_and_office_metadata():
    source = ChatSession()._sources_for_ui([_parent()])[0]
    dto = source_to_dto(source)

    assert dto.company == "品茗股份"
    assert dto.sheet_name == "统计表"
    assert dto.cell_range == "B2:F20"
    assert dto.slide_number == 8
    assert dto.paragraph_anchor == "第 3.2 节"
    assert dto.media_id == "media-1"


def test_retrieved_parent_json_round_trip_preserves_all_fields():
    parent = _parent()

    restored = _retrieved_parents_from_json(_retrieved_parents_to_json([parent]))

    assert [dataclasses.asdict(item) for item in restored] == [dataclasses.asdict(parent)]


def test_legacy_retrieved_parent_json_uses_safe_defaults():
    restored = _retrieved_parents_from_json(
        '[{"parent_id":"legacy","doc_title":"旧资料"}]'
    )[0]

    assert restored.parent_id == "legacy"
    assert restored.doc_type == "pdf"
    assert restored.media_id is None
    assert restored.company is None
    assert restored.sheet_name is None
    assert restored.cell_range is None
    assert restored.slide_number is None
    assert restored.paragraph_anchor is None
    assert restored.transcript_version_id is None
    assert restored.content_item_id is None
    assert restored.content_version_id is None
    assert restored.category_key is None
    assert restored.subquery_idx is None
