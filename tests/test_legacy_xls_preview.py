from pathlib import Path

from src import indexing_pipeline


def test_legacy_xls_uses_single_conversion_as_preview(tmp_path, monkeypatch):
    source = tmp_path / "legacy.xls"
    source.write_bytes(b"legacy")
    converted = tmp_path / "legacy.converted.xlsx"
    converted.write_bytes(b"PK\x03\x04converted")
    markdown = tmp_path / "legacy.md"
    markdown.write_text("table", encoding="utf-8")
    monkeypatch.setattr(indexing_pipeline, "_legacy_conversion", lambda *_args: converted)
    monkeypatch.setattr(
        indexing_pipeline,
        "_build_xlsx_doc",
        lambda *_args, **_kwargs: indexing_pipeline.ParsedDoc(
            source_path=converted,
            category="", doc_title="legacy", markdown_path=markdown, doc_type="xlsx",
        ),
    )

    indexing_pipeline._build_legacy_doc(
        source, "xls", lambda _status: None, parsed_dir=tmp_path, write_preview=True,
    )

    assert source.with_suffix(".preview.xlsx").read_bytes() == b"PK\x03\x04converted"
