from __future__ import annotations

from pathlib import Path

from src import indexing_pipeline


def test_cached_pptx_markdown_still_generates_missing_preview(tmp_path: Path, monkeypatch):
    source = tmp_path / "sample.pptx"
    source.write_bytes(b"synthetic")
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    markdown_path = parsed_dir / "cached.md"
    markdown_path.write_text("# cached", encoding="utf-8")
    calls: list[Path] = []

    monkeypatch.setattr(indexing_pipeline, "_md_path_for_office", lambda *_args: markdown_path)

    def convert(path: Path) -> Path:
        calls.append(path)
        preview = path.with_suffix(".preview.pdf")
        preview.write_bytes(b"%PDF-1.7\nsynthetic")
        return preview

    monkeypatch.setattr(indexing_pipeline, "convert_pptx_to_pdf", convert)

    document = indexing_pipeline._build_pptx_doc(source, lambda _status: None, parsed_dir=parsed_dir)

    assert document.markdown_path == markdown_path
    assert calls == [source]


def test_cached_pptx_keeps_existing_valid_preview(tmp_path: Path, monkeypatch):
    source = tmp_path / "sample.pptx"
    source.write_bytes(b"synthetic")
    source.with_suffix(".preview.pdf").write_bytes(b"%PDF-1.7\nsynthetic")
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    markdown_path = parsed_dir / "cached.md"
    markdown_path.write_text("# cached", encoding="utf-8")
    monkeypatch.setattr(indexing_pipeline, "_md_path_for_office", lambda *_args: markdown_path)
    convert = lambda _path: (_ for _ in ()).throw(AssertionError("must not regenerate"))
    monkeypatch.setattr(indexing_pipeline, "convert_pptx_to_pdf", convert)

    indexing_pipeline._build_pptx_doc(source, lambda _status: None, parsed_dir=parsed_dir)
