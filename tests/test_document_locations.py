import json

from src.chunk import chunk_document
from src.document_locations import (
    DocumentLocation,
    match_locations,
    mineru_locations,
    read_location_sidecar,
    write_location_sidecar,
)
from src.ingest import ParsedDoc


def test_mineru_content_list_normalizes_zero_based_pages():
    locations = mineru_locations({"content_list": [{"type": "text", "text": "第五页证据", "page_idx": 4}]})
    assert locations[0].page_number == 5
    assert locations[0].text == "第五页证据"


def test_location_matching_returns_page_span(tmp_path):
    locations = [
        DocumentLocation(text="第一段证据", page_number=5),
        DocumentLocation(text="后续跨页证据", page_number=6),
    ]
    result = match_locations("回答引用：第一段证据以及后续跨页证据", locations)
    assert result is not None
    assert result.page_number == 5
    assert result.page_end == 6


def test_location_matching_prefers_exact_section_path_for_short_xmind_topic():
    locations = [
        DocumentLocation(
            text="交付",
            topic_id="root-topic",
            heading_anchor="画布：项目计划 > 交付",
        )
    ]

    result = match_locations(
        "# 画布：项目计划\n## 交付",
        locations,
        section_path="画布：项目计划 > 交付",
    )

    assert result is not None
    assert result.topic_id == "root-topic"


def test_location_matching_accepts_exact_short_office_line():
    locations = [
        DocumentLocation(text="交付", paragraph_anchor="short-anchor"),
    ]

    result = match_locations("# 计划\n\n交付", locations)

    assert result is not None
    assert result.paragraph_anchor == "short-anchor"


def test_location_matching_rejects_matches_across_different_slides():
    locations = [
        DocumentLocation(text="项目总览", page_number=1, slide_number=1),
        DocumentLocation(text="项目总览详细计划", page_number=2, slide_number=2),
    ]

    result = match_locations("# 项目总览详细计划", locations)

    assert result is None


def test_location_matching_selects_range_within_exact_sheet():
    locations = [
        DocumentLocation(
            text="## Sheet: 统计\n\n| 编号 | 数量 |\n| --- | --- |\n| A | 1 |",
            sheet_name="统计",
            cell_range="A1:B2",
            heading_anchor="Sheet: 统计",
        ),
        DocumentLocation(
            text="## Sheet: 统计\n\n| 编号 | 数量 |\n| --- | --- |\n| B | 2 |",
            sheet_name="统计",
            cell_range="A3:B4",
            heading_anchor="Sheet: 统计",
        ),
    ]

    result = match_locations(
        "## Sheet: 统计\n\n| 编号 | 数量 |\n| --- | --- |\n| B | 2 |",
        locations,
        section_path="Sheet: 统计",
    )

    assert result is not None
    assert result.cell_range == "A3:B4"


def test_chunk_document_propagates_office_location_fields(tmp_path):
    markdown_path = tmp_path / "office.md"
    markdown_path.write_text("# 交付\n\n交付证据", encoding="utf-8")
    location_path = tmp_path / "office.locations.json"
    write_location_sidecar(
        location_path,
        [
            DocumentLocation(
                text="交付证据",
                sheet_name="统计",
                cell_range="A1:B2",
                slide_number=5,
                paragraph_anchor="abcd1234",
            )
        ],
    )
    document = ParsedDoc(
        source_path=tmp_path / "office.pptx",
        category="项目资料",
        doc_title="office",
        markdown_path=markdown_path,
        doc_type="pptx",
        location_map_path=location_path,
    )

    parents, children = chunk_document(document)

    for item in [*parents, *children]:
        assert item.sheet_name == "统计"
        assert item.cell_range == "A1:B2"
        assert item.slide_number == 5
        assert item.paragraph_anchor == "abcd1234"


def test_location_sidecar_is_versioned_and_tolerates_invalid_payload(tmp_path):
    path = tmp_path / "document.locations.json"
    write_location_sidecar(path, [DocumentLocation(text="证据", page_number=2)])
    assert read_location_sidecar(path)[0].page_number == 2
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    assert read_location_sidecar(path) == []
