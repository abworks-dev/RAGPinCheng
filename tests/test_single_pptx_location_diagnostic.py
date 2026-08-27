import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

from scripts import diagnose_single_pptx_location as diagnostic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_single_pptx_location.py"
WORKFLOW = ROOT / ".github" / "workflows" / "diagnose-single-pptx-location.yml"
ITEM_ID = "item-8e22ad7c-e1ea-4475-b8ab-7084c9d870f3"
VERSION_ID = "version-2f810c73-7d5f-4b04-8ff1-9b7416a28403"


def test_single_pptx_location_diagnostic_script_exists():
    assert SCRIPT.is_file()


def test_build_location_report_exposes_structure_without_source_text():
    markdown = "# Secret heading\n\nAlpha project overview\n"
    slides = [
        {"slide_number": 1, "text": "Alpha project overview"},
        {"slide_number": 3, "text": "Confidential bracket layout"},
    ]
    parents = [
        SimpleNamespace(
            text="Alpha project overview",
            page_number=1,
            paragraph_anchor=None,
            sheet_name=None,
            topic_id=None,
        ),
        SimpleNamespace(
            text="Unrelated chunk",
            page_number=None,
            paragraph_anchor=None,
            sheet_name=None,
            topic_id=None,
        ),
    ]
    children = [
        SimpleNamespace(content_type="prose", page_number=1),
        SimpleNamespace(content_type="table", page_number=None),
    ]

    report = diagnostic.build_location_report(markdown, slides, parents, children)

    assert report["schema_version"] == 1
    assert report["markdown"] == {
        "characters": 41,
        "nonempty_lines": 2,
        "heading_lines": 1,
    }
    assert report["slides"] == [
        {"slide_number": 1, "normalized_characters": 20},
        {"slide_number": 3, "normalized_characters": 25},
    ]
    assert report["chunks"] == {
        "parents": 2,
        "children": 2,
        "located_parents": 1,
        "located_children": 1,
        "unmatched_parent_ordinals": [2],
        "child_content_types": {"prose": 1, "table": 1},
    }
    assert report["parent_matches"][0] == {
        "parent_ordinal": 1,
        "assigned_page_number": 1,
        "best_slide_number": 1,
        "best_similarity": 1.0,
        "probe_match_slide_numbers": [1],
    }
    serialized = json.dumps(report, sort_keys=True)
    for source_text in (
        "Secret heading",
        "Alpha project overview",
        "Confidential bracket layout",
        "Unrelated chunk",
    ):
        assert source_text not in serialized


def test_build_location_report_does_not_count_empty_location_fields():
    parent = SimpleNamespace(
        text="Unmatched",
        page_number=None,
        paragraph_anchor="",
        sheet_name="",
        topic_id="",
    )

    report = diagnostic.build_location_report("Unmatched", [], [parent], [])

    assert report["chunks"]["located_parents"] == 0
    assert report["chunks"]["unmatched_parent_ordinals"] == [1]


def test_load_target_selects_exact_pptx_and_verifies_object(tmp_path):
    content_root = tmp_path / "content"
    source = content_root / "objects" / "source.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic-pptx")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = tmp_path / "app.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE content_item_heads (
              item_id TEXT PRIMARY KEY,
              current_version_id TEXT NOT NULL
            );
            CREATE TABLE content_versions (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              object_sha256 TEXT NOT NULL,
              original_filename TEXT NOT NULL,
              doc_type TEXT NOT NULL
            );
            CREATE TABLE content_objects (
              sha256 TEXT PRIMARY KEY,
              storage_rel_path TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO content_item_heads VALUES (?, ?)",
            (ITEM_ID, VERSION_ID),
        )
        connection.execute(
            "INSERT INTO content_versions VALUES (?, ?, ?, ?, ?)",
            (VERSION_ID, ITEM_ID, digest, "diagnostic.pptx", "pptx"),
        )
        connection.execute(
            "INSERT INTO content_objects VALUES (?, ?)",
            (digest, "objects/source.pptx"),
        )

    target = diagnostic.load_target(database, content_root, ITEM_ID, VERSION_ID)

    assert target.source_path == source.resolve()
    assert target.original_filename == "diagnostic.pptx"
    assert target.object_sha256 == digest


def test_single_pptx_location_diagnostic_cli_requires_bounded_inputs():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--app-database" in result.stdout
    assert "--content-root" in result.stdout
    assert "--item-id" in result.stdout
    assert "--version-id" in result.stdout
    assert "--work-dir" in result.stdout


def test_production_workflow_is_exact_read_only_and_isolated():
    assert WORKFLOW.is_file()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "permissions:\n  contents: read",
        "group: production-app-manual-v1",
        "runs-on: [self-hosted, linux, ubuntu, production, app]",
        ITEM_ID,
        VERSION_ID,
        "--read-only",
        "--network none",
        ":/diagnostic/app.sqlite:ro",
        ":/diagnostic/content:ro",
        ":/diagnostic/repo:ro",
        "--tmpfs /diagnostic-work",
    ):
        assert required in workflow
    for forbidden in (
        "docker compose exec",
        "docker compose run",
        "UPDATE ",
        "DELETE ",
        "INSERT ",
        "qdrant",
        "systemctl",
        "docker restart",
    ):
        assert forbidden not in workflow
