from __future__ import annotations

from pathlib import Path

from scripts import repair_pptx_previews


class FakeStorage:
    def __init__(self, root: Path):
        self.root = root

    def published_source_path(
        self,
        *,
        content_item_id: str,
        content_version_id: str,
        filename: str,
    ) -> Path:
        del content_item_id
        return self.root / content_version_id / filename


def test_repair_previews_dry_run_and_apply_only_touch_missing(tmp_path: Path, monkeypatch):
    rows = [
        {"version_id": "ready", "item_id": "item-1", "original_filename": "ready.pptx"},
        {"version_id": "missing", "item_id": "item-2", "original_filename": "missing.pptx"},
    ]
    monkeypatch.setattr(repair_pptx_previews, "_candidates", lambda _version_id=None: rows)
    storage = FakeStorage(tmp_path)
    for row in rows:
        source = storage.published_source_path(
            content_item_id=row["item_id"],
            content_version_id=row["version_id"],
            filename=row["original_filename"],
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"synthetic")
    ready_preview = tmp_path / "ready" / "ready.preview.pdf"
    ready_preview.write_bytes(b"%PDF-1.7\nexisting")
    conversions: list[Path] = []

    def convert(path: Path) -> Path:
        conversions.append(path)
        preview = path.with_suffix(".preview.pdf")
        preview.write_bytes(b"%PDF-1.7\ngenerated")
        return preview

    monkeypatch.setattr(repair_pptx_previews, "convert_pptx_to_pdf", convert)

    dry_run = repair_pptx_previews.repair_previews(apply=False, storage=storage)
    applied = repair_pptx_previews.repair_previews(apply=True, storage=storage)

    assert dry_run["ready"] == 1
    assert dry_run["missing"] == 1
    assert conversions == [tmp_path / "missing" / "missing.pptx"]
    assert applied["generated"] == 1
    assert applied["failed"] == 0
    assert ready_preview.read_bytes() == b"%PDF-1.7\nexisting"
