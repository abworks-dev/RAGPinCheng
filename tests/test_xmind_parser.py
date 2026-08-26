import json
import zipfile
from pathlib import Path

import pytest

from src.chunk import chunk_document
from src.indexing_pipeline import _build_xmind_doc
from src.xmind_parser import XMindParseError, parse_xmind, xmind_to_markdown


def _write_modern(path: Path, sheets: list[dict]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps(sheets, ensure_ascii=False))
        archive.writestr("metadata.json", "{}")


def test_parses_modern_xmind_and_preserves_hierarchy(tmp_path: Path):
    path = tmp_path / "plan.xmind"
    _write_modern(path, [{
        "id": "sheet-1",
        "title": "项目计划",
        "rootTopic": {
            "id": "root",
            "title": "交付",
            "notes": {"plain": {"content": "按期完成"}},
            "children": {"attached": [{"id": "child", "title": "设计", "children": {"attached": []}}]},
        },
    }])

    document = parse_xmind(path)

    assert document.sheets[0].title == "项目计划"
    assert document.sheets[0].root_topic.notes == "按期完成"
    assert document.sheets[0].root_topic.children[0].title == "设计"
    markdown = xmind_to_markdown(document)
    assert "# 画布：项目计划" in markdown
    assert "## 交付" in markdown
    assert "### 设计" in markdown


def test_xmind_index_document_preserves_topic_location(tmp_path: Path):
    path = tmp_path / "plan.xmind"
    _write_modern(path, [{
        "id": "sheet-1",
        "title": "项目计划",
        "rootTopic": {
            "id": "root-topic",
            "title": "交付",
            "children": {"attached": []},
        },
    }])

    document = _build_xmind_doc(path, lambda _status: None, parsed_dir=tmp_path / "parsed")
    parents, children = chunk_document(document)

    assert document.location_map_path is not None
    assert document.location_map_path.is_file()
    assert {parent.topic_id for parent in parents} == {"root-topic"}
    assert {child.topic_id for child in children} == {"root-topic"}


def test_xmind_topic_location_uses_full_hierarchy_for_duplicate_titles(tmp_path: Path):
    path = tmp_path / "duplicate-topics.xmind"
    _write_modern(path, [{
        "id": "sheet-1",
        "title": "项目计划",
        "rootTopic": {
            "id": "root-topic",
            "title": "总览",
            "children": {"attached": [
                {
                    "id": "design",
                    "title": "设计",
                    "children": {"attached": [{
                        "id": "design-review",
                        "title": "复核",
                        "children": {"attached": []},
                    }]},
                },
                {
                    "id": "delivery",
                    "title": "交付",
                    "children": {"attached": [{
                        "id": "delivery-review",
                        "title": "复核",
                        "children": {"attached": []},
                    }]},
                },
            ]},
        },
    }])

    document = _build_xmind_doc(path, lambda _status: None, parsed_dir=tmp_path / "parsed")
    parents, _children = chunk_document(document)
    topic_by_section = {parent.section_path: parent.topic_id for parent in parents}

    assert topic_by_section["画布：项目计划 > 总览 > 设计 > 复核"] == "design-review"
    assert topic_by_section["画布：项目计划 > 总览 > 交付 > 复核"] == "delivery-review"


def test_parses_legacy_content_xml(tmp_path: Path):
    path = tmp_path / "legacy.xmind"
    content = """<?xml version="1.0" encoding="UTF-8"?>
    <xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0">
      <sheet id="s1"><title>旧版画布</title><topic id="r1"><title>中心主题</title>
        <children><topics type="attached"><topic id="c1"><title>子主题</title></topic></topics></children>
      </topic></sheet>
    </xmap-content>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", content)

    document = parse_xmind(path)

    assert document.sheets[0].root_topic.children[0].title == "子主题"


def test_rejects_archive_path_traversal(tmp_path: Path):
    path = tmp_path / "unsafe.xmind"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../content.json", "[]")

    with pytest.raises(XMindParseError, match="xmind_archive_path_invalid"):
        parse_xmind(path)


def test_rejects_topic_tree_beyond_depth_limit(tmp_path: Path):
    path = tmp_path / "deep.xmind"
    root: dict = {"id": "0", "title": "root", "children": {"attached": []}}
    current = root
    for index in range(70):
        child = {"id": str(index + 1), "title": "child", "children": {"attached": []}}
        current["children"]["attached"].append(child)
        current = child
    _write_modern(path, [{"id": "s", "title": "deep", "rootTopic": root}])

    with pytest.raises(XMindParseError, match="xmind_topic_structure_invalid"):
        parse_xmind(path)
