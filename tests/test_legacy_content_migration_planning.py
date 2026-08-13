from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.inventory_legacy_content import inventory
from scripts.plan_legacy_content_migration import PlanningError, build_plan, render_csv, render_json


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "plan_legacy_content_migration.py"


def _entry(kind: str, path: str, content: bytes = b"content") -> dict[str, object]:
    return {
        "kind": kind,
        "relative_path": path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "status": "inventoried",
    }


def _mapping(*rows: dict[str, str]) -> dict[str, object]:
    return {"schema_version": 1, "mappings": list(rows)}


def _rule(prefix: str, category: str = "company_standards", handling: str = "document") -> dict[str, str]:
    return {"kind": "docs", "legacy_prefix": prefix, "category_key": category, "handling": handling}


def test_inventory_synthetic_docs_media_tree_and_symlink(tmp_path):
    docs = tmp_path / "docs"
    media = tmp_path / "media"
    (docs / "公司标准").mkdir(parents=True)
    (media / "video-1").mkdir(parents=True)
    (docs / "公司标准" / "guide.pdf").write_bytes(b"pdf")
    (media / "video-1" / "original.mp4").write_bytes(b"video")
    rows = inventory(docs, "docs") + inventory(media, "media")
    assert [(row["kind"], row["relative_path"]) for row in rows] == [
        ("docs", "公司标准/guide.pdf"), ("media", "video-1/original.mp4")
    ]
    link = docs / "company-link.pdf"
    try:
        link.symlink_to(docs / "公司标准" / "guide.pdf")
    except OSError:
        pytest.skip("symbolic links are not available")
    assert inventory(docs, "docs")[0]["status"] == "symlink_rejected"


def test_mapping_dispositions_depth_limit_and_transcript_review():
    inventory_payload = {"schema_version": 1, "entries": [
        _entry("docs", "公司标准/guide.pdf"),
        _entry("docs", "公司标准/report.preview.pdf"),
        _entry("docs", "公司标准/sheet.PREVIEW.XLSX"),
        _entry("docs", "未知/readme.md"),
        _entry("docs", "教学视频/lesson.md"),
        _entry("docs", "教学视频/video.mp4"),
        _entry("docs", "a/b/c/d/file.docx"),
        _entry("docs", "a/b/c/d/e/file.docx"),
        _entry("media", "video-1/original.mp4"),
        {"kind": "docs", "relative_path": "公司标准/link.pdf", "status": "symlink_rejected"},
    ]}
    mapping = _mapping(
        _rule("公司标准"),
        _rule("教学视频", "training_materials", "transcript"),
        _rule("a", "project_materials"),
    )
    plan = build_plan(inventory_payload, mapping, max_bytes=1024)
    assert {entry["relative_path"]: entry["disposition"] for entry in plan["entries"]} == {
        "a/b/c/d/e/file.docx": "unsupported",
        "a/b/c/d/file.docx": "import_document",
        "公司标准/guide.pdf": "import_document",
        "公司标准/report.preview.pdf": "derived_artifact",
        "公司标准/sheet.PREVIEW.XLSX": "derived_artifact",
        "公司标准/link.pdf": "symlink_rejected",
        "教学视频/lesson.md": "review_transcript_link",
        "教学视频/video.mp4": "unsupported",
        "未知/readme.md": "pending_mapping",
        "video-1/original.mp4": "preserve_legacy_media",
    }
    assert plan["summary"]["unmapped_count"] == 1
    assert plan["summary"]["exception_count"] == 3
    previews = [entry for entry in plan["entries"] if entry["disposition"] == "derived_artifact"]
    assert {entry["reason"] for entry in previews} == {"generated_preview"}


def test_longest_prefix_unknown_category_unsupported_and_size_limit():
    entries = [
        _entry("docs", "项目/A/specific.pdf", b"1"),
        _entry("docs", "项目/general.pdf", b"1"),
        _entry("docs", "项目/archive.zip", b"1"),
        _entry("docs", "项目/large.pdf", b"123"),
    ]
    plan = build_plan(
        {"schema_version": 1, "entries": entries},
        _mapping(_rule("项目", "project_materials"), _rule("项目/A", "project_experience")),
        max_bytes=2,
    )
    by_path = {entry["relative_path"]: entry for entry in plan["entries"]}
    assert by_path["项目/A/specific.pdf"]["category_key"] == "project_experience"
    assert by_path["项目/general.pdf"]["category_key"] == "project_materials"
    assert by_path["项目/archive.zip"]["reason"] == "unsupported_type"
    assert by_path["项目/large.pdf"]["reason"] == "content_too_large"
    with pytest.raises(PlanningError, match="invalid_category_key"):
        build_plan({"schema_version": 1, "entries": []}, _mapping(_rule("项目", "guessed")), max_bytes=2)


def test_duplicate_sha_within_scope_and_cross_scope_is_only_related():
    same = b"same"
    plan = build_plan(
        {"schema_version": 1, "entries": [
            _entry("docs", "公司标准/a.pdf", same),
            _entry("docs", "公司标准/b.pdf", same),
            _entry("docs", "客户标准/c.pdf", same),
            _entry("media", "asset/original.mp4", same),
        ]},
        _mapping(_rule("公司标准"), _rule("客户标准", "client_requirements")),
        max_bytes=1024,
    )
    by_path = {entry["relative_path"]: entry for entry in plan["entries"]}
    assert by_path["公司标准/a.pdf"]["duplicate_group"] == "duplicate-0001"
    assert by_path["公司标准/b.pdf"]["duplicate_group"] == "duplicate-0001"
    assert by_path["客户标准/c.pdf"]["duplicate_group"] is None
    assert len(by_path["客户标准/c.pdf"]["related_sha256_paths"]) == 3
    assert plan["summary"]["same_scope_duplicate_groups"] == 1
    assert plan["summary"]["cross_scope_sha256_links"] == 1


def test_unmapped_and_media_duplicates_use_their_legacy_directory_scope():
    same = b"same"
    plan = build_plan(
        {"schema_version": 1, "entries": [
            _entry("docs", "unknown-a/a.pdf", same),
            _entry("docs", "unknown-b/b.pdf", same),
            _entry("media", "asset-a/original.mp4", same),
            _entry("media", "asset-b/original.mp4", same),
        ]},
        _mapping(),
        max_bytes=1024,
    )
    assert all(entry["duplicate_group"] is None for entry in plan["entries"])
    assert plan["summary"]["same_scope_duplicate_groups"] == 0
    assert plan["summary"]["cross_scope_sha256_links"] == 1


def test_json_csv_are_stable_and_contain_no_source_roots():
    plan = build_plan(
        {"schema_version": 1, "entries": [_entry("docs", "公司标准/a,b.pdf")]},
        _mapping(_rule("公司标准")),
        max_bytes=1024,
    )
    assert render_json(plan) == render_json(plan)
    assert render_csv(plan) == render_csv(plan)
    assert "a,b.pdf" in render_csv(plan)
    assert "E:\\" not in render_json(plan) and "/data/business" not in render_json(plan)
    assert "content" not in render_json(plan)


@pytest.mark.parametrize("bad_path", ["/absolute/file.pdf", "../escape.pdf", "a\\b.pdf"])
def test_rejects_unsafe_inventory_paths(bad_path):
    payload = {"schema_version": 1, "entries": [_entry("docs", bad_path)]}
    with pytest.raises(PlanningError, match="invalid_inventory_path"):
        build_plan(payload, _mapping(), max_bytes=1024)


def test_cli_help_errors_overwrite_and_does_not_touch_inputs(tmp_path):
    inventory_path = tmp_path / "inventory.json"
    mapping_path = tmp_path / "mapping.json"
    output_path = tmp_path / "out" / "plan.json"
    csv_path = tmp_path / "out" / "plan.csv"
    inventory_path.write_text(json.dumps({"schema_version": 1, "entries": [_entry("docs", "公司标准/a.pdf")]}), encoding="utf-8")
    mapping_path.write_text(json.dumps(_mapping(_rule("公司标准"))), encoding="utf-8")
    before = (inventory_path.read_bytes(), mapping_path.read_bytes())
    help_run = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert help_run.returncode == 0 and "--overwrite" in help_run.stdout
    command = [sys.executable, str(SCRIPT), "--inventory", str(inventory_path), "--mapping", str(mapping_path),
               "--output-json", str(output_path), "--output-csv", str(csv_path)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0 and "output_already_exists" in rejected.stderr
    assert subprocess.run([*command, "--overwrite"], capture_output=True, text=True).returncode == 0
    assert before == (inventory_path.read_bytes(), mapping_path.read_bytes())
    assert not list(tmp_path.rglob("*.sqlite"))
    collision = subprocess.run([sys.executable, str(SCRIPT), "--inventory", str(inventory_path), "--mapping", str(mapping_path),
                                "--output-json", str(inventory_path), "--overwrite"], capture_output=True, text=True)
    assert collision.returncode != 0 and "output_must_not_replace_input" in collision.stderr


def test_cli_preflights_all_outputs_before_writing(tmp_path):
    inventory_path = tmp_path / "inventory.json"
    mapping_path = tmp_path / "mapping.json"
    json_path = tmp_path / "new.json"
    csv_path = tmp_path / "existing.csv"
    inventory_path.write_text(json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8")
    mapping_path.write_text(json.dumps(_mapping()), encoding="utf-8")
    csv_path.write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory_path), "--mapping", str(mapping_path),
         "--output-json", str(json_path), "--output-csv", str(csv_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and "output_already_exists" in result.stderr
    assert not json_path.exists()
    assert csv_path.read_text(encoding="utf-8") == "keep"
