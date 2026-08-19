import json
import zipfile
from pathlib import Path

import pytest

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
