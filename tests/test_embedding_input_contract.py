from __future__ import annotations

from pathlib import Path

import pytest

from api import content_publication
from src import indexing_pipeline, table_summary
from src.chunk import Child, chunk_document
from src.config import EMBED_MAX_TEXT_CHARS
from src.index import EmbeddingInputTooLong, validate_embedding_inputs
from src.ingest import ParsedDoc
from src.indexing_pipeline import ManagedIndexMetadata
from src.providers import GpuServiceInputTooLong


def _document(tmp_path: Path, markdown: str, *, title: str = "测试资料") -> ParsedDoc:
    source = tmp_path / "source.md"
    source.write_text(markdown, encoding="utf-8")
    return ParsedDoc(
        source_path=source,
        category="客户标准与要求",
        doc_title=title,
        markdown_path=source,
        doc_type="pdf",
        content_item_id="item-1",
        content_version_id="version-1",
        publication_target_id="target-1",
        category_key="customer_requirements",
        source_ref="content://item-1/version-1",
    )


def test_oversized_html_table_splits_children_and_preserves_parent_evidence(tmp_path):
    header = "<tr><th>条目</th><th>要求</th></tr>"
    rows = "".join(
        f"<tr><td>{index}</td><td>{'管综要求' * 300}</td></tr>"
        for index in range(18)
    )
    markdown = f"# 管综管理\n\n<table>{header}{rows}</table>"

    parents, children = chunk_document(_document(tmp_path, markdown))

    table_children = [child for child in children if child.content_type == "table"]
    assert len(table_children) > 1
    assert all(len(child.embed_text) <= EMBED_MAX_TEXT_CHARS for child in children)
    assert "管综要求" in "".join(parent.text for parent in parents)
    assert all("<th>条目</th>" in child.text for child in table_children)
    validate_embedding_inputs(children)


def test_single_oversized_html_cell_splits_only_child_not_parent(tmp_path):
    header = "<tr><th>条目</th><th>要求</th></tr>"
    row = f"<tr><td>1</td><td>{'单行要求' * 2500}</td></tr>"
    markdown = f"# 管综管理\n\n<table>{header}{row}</table>"

    parents, children = chunk_document(_document(tmp_path, markdown))
    table_parents = [parent for parent in parents if "<table>" in parent.text]
    table_children = [child for child in children if child.content_type == "table"]

    assert len(table_parents) == 1
    assert row in table_parents[0].text
    assert len(table_children) > 1
    assert all("<th>条目</th>" in child.text for child in table_children)
    assert all(len(child.embed_text) <= EMBED_MAX_TEXT_CHARS for child in children)


def test_single_row_html_table_is_split_without_changing_parent(tmp_path):
    row = f"<tr><td>{'单行内容' * 2500}</td></tr>"
    markdown = f"# 单行表格\n\n<table>{row}</table>"

    parents, children = chunk_document(_document(tmp_path, markdown))
    table_parents = [parent for parent in parents if "<table>" in parent.text]
    table_children = [child for child in children if child.content_type == "table"]

    assert len(table_parents) == 1
    assert row in table_parents[0].text
    assert len(table_children) > 1
    assert all(child.text.startswith("<table><tr><td>") for child in table_children)
    assert all(len(child.embed_text) <= EMBED_MAX_TEXT_CHARS for child in children)


def test_oversized_markdown_table_splits_rows_and_repeats_header(tmp_path):
    header = "| 条目 | 要求 |\n| --- | --- |"
    rows = "\n".join(
        f"| {index} | {'协调原则' * 350} |" for index in range(18)
    )

    parents, children = chunk_document(
        _document(tmp_path, f"# 管综管理\n\n{header}\n{rows}")
    )

    table_children = [child for child in children if child.content_type == "table"]
    assert len(table_children) > 1
    assert all(child.text.startswith(header) for child in table_children)
    assert all(len(child.embed_text) <= EMBED_MAX_TEXT_CHARS for child in table_children)
    assert "协调原则" in "".join(parent.text for parent in parents)


def test_table_summary_respects_final_embedding_limit(tmp_path, monkeypatch):
    child = Child(
        child_id="child-1",
        parent_id="parent-1",
        text="表" * 7000,
        embed_text="资料 > 章节\n\n" + "表" * 7000,
        doc_title="资料",
        category="客户标准与要求",
        section_path="章节",
        source_path="content://item/version",
        content_type="table",
    )
    monkeypatch.setattr(table_summary, "PARENTS_DB", tmp_path / "parents.sqlite")
    monkeypatch.setattr(table_summary, "_call_llm", lambda *_args: "摘要" * 2000)
    monkeypatch.setattr(table_summary, "_client", lambda: object())

    table_summary.summarize_table_children([child])

    assert child.text.startswith(table_summary.SUMMARY_MARKER)
    assert len(child.embed_text) <= EMBED_MAX_TEXT_CHARS
    validate_embedding_inputs([child])


def test_oversized_formula_fails_before_parent_store(tmp_path, monkeypatch):
    source = tmp_path / "formula.md"
    source.write_text("# 公式\n\n$$" + ("x" * 9000) + "$$", encoding="utf-8")
    metadata = ManagedIndexMetadata(
        content_item_id="item-1",
        content_version_id="version-1",
        publication_target_id="target-1",
        category_key="customer_requirements",
        category_display_name="客户标准与要求",
        doc_title="超长公式",
        source_ref="content://item-1/version-1",
    )
    called = {"store": False, "index": False}
    monkeypatch.setattr(
        indexing_pipeline,
        "store_parents",
        lambda _parents: called.__setitem__("store", True),
    )
    monkeypatch.setattr(
        indexing_pipeline,
        "index_children",
        lambda _children: called.__setitem__("index", True),
    )

    with pytest.raises(EmbeddingInputTooLong):
        indexing_pipeline.index_managed_content(source, "markdown", metadata)

    assert called == {"store": False, "index": False}


def test_oversized_embedding_failure_has_specific_safe_reason():
    error = EmbeddingInputTooLong(
        child_index=1,
        content_type="table",
        length=9000,
    )

    assert (
        content_publication._classify_failure(error, "embedding")
        == "embedding_input_too_long"
    )
    assert content_publication.failure_detail("embedding_input_too_long") == {
        "code": "embedding_input_too_long",
        "message": "文档中存在超过向量化限制的内容块。",
        "retryable": True,
        "recommended_action": "请在系统更新后重试，无需重新上传文件。",
    }

    assert (
        content_publication._classify_failure(
            GpuServiceInputTooLong("provider limit"), "embedding"
        )
        == "embedding_input_too_long"
    )


def test_oversized_formula_failure_has_specific_non_retryable_reason():
    error = EmbeddingInputTooLong(
        child_index=0,
        content_type="formula",
        length=9000,
    )

    assert (
        content_publication._classify_failure(error, "embedding")
        == "embedding_formula_too_long"
    )
    assert content_publication.failure_detail("embedding_formula_too_long") == {
        "code": "embedding_formula_too_long",
        "message": "文档中存在超过向量化限制的公式。",
        "retryable": False,
        "recommended_action": "请拆分超长公式后重新上传，或联系系统管理员处理。",
    }
