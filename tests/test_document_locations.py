import json

from src.document_locations import (
    DocumentLocation,
    match_locations,
    mineru_locations,
    read_location_sidecar,
    write_location_sidecar,
)


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


def test_location_sidecar_is_versioned_and_tolerates_invalid_payload(tmp_path):
    path = tmp_path / "document.locations.json"
    write_location_sidecar(path, [DocumentLocation(text="证据", page_number=2)])
    assert read_location_sidecar(path)[0].page_number == 2
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    assert read_location_sidecar(path) == []
