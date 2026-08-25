from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rebuild_managed_index.py"


def load_script():
    spec = importlib.util.spec_from_file_location("rebuild_managed_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_exposes_opt_in_shadow_destinations():
    source = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("RAG_DATA_DIR"' in source
    assert 'os.getenv("QDRANT_COLLECTION"' in source
    assert 'os.getenv("RAG_PARSED_DIR"' in source


def test_managed_index_entry_accepts_rebuild_options():
    from src.indexing_pipeline import index_managed_content

    parameters = inspect.signature(index_managed_content).parameters
    assert parameters["force_parse"].default is False
    assert parameters["write_preview"].default is True


def _snapshot_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE content_item_heads(item_id TEXT, current_version_id TEXT, publication_id TEXT);
        CREATE TABLE content_items(id TEXT, title TEXT, category_id TEXT);
        CREATE TABLE content_versions(
          id TEXT, item_id TEXT, object_sha256 TEXT, original_filename TEXT,
          doc_type TEXT, title TEXT
        );
        CREATE TABLE category_nodes(id TEXT, category_key TEXT, display_name TEXT);
        CREATE TABLE content_objects(sha256 TEXT, storage_rel_path TEXT);
        CREATE TABLE content_index_jobs(
          id TEXT, publication_id TEXT, version_id TEXT, target_index_id TEXT,
          status TEXT, finished_at INTEGER
        );
        CREATE TABLE media_transcript_heads(media_id TEXT, current_version_id TEXT);
        CREATE TABLE media_assets(media_id TEXT, title TEXT);
        CREATE TABLE transcript_versions(
          id TEXT, media_id TEXT, markdown_storage_kind TEXT, markdown_rel_path TEXT,
          markdown_sha256 TEXT, markdown_size_bytes INTEGER
        );
        CREATE TABLE transcript_publication_index_jobs(
          id TEXT, transcript_version_id TEXT, target_index_id TEXT,
          status TEXT, finished_at INTEGER
        );
        """
    )
    return conn


def test_load_snapshot_enumerates_only_current_published_heads(tmp_path: Path):
    module = load_script()
    conn = _snapshot_database(tmp_path / "app.sqlite")
    conn.executescript(
        """
        INSERT INTO category_nodes VALUES ('cat-1','standards','标准');
        INSERT INTO content_items VALUES ('item-1','规范','cat-1');
        INSERT INTO content_versions VALUES ('ver-current','item-1','abc','规范.pdf','pdf',NULL);
        INSERT INTO content_versions VALUES ('ver-old','item-1','def','旧版.pdf','pdf',NULL);
        INSERT INTO content_objects VALUES ('abc','objects/ab/abc');
        INSERT INTO content_item_heads VALUES ('item-1','ver-current','pub-1');
        INSERT INTO content_index_jobs VALUES ('job-old','pub-1','ver-current','target-old','failed',10);
        INSERT INTO content_index_jobs VALUES ('job-current','pub-1','ver-current','target-current','done',20);
        INSERT INTO media_assets VALUES ('media-1','培训');
        INSERT INTO transcript_versions VALUES ('tx-current','media-1','managed_artifact','markdown/aa/a.md','aaa',3);
        INSERT INTO media_transcript_heads VALUES ('media-1','tx-current');
        INSERT INTO transcript_publication_index_jobs VALUES ('tx-job','tx-current','tx-target','done',30);
        """
    )
    conn.commit()

    snapshot = module.load_rebuild_snapshot(conn)

    assert [item.version_id for item in snapshot.managed] == ["ver-current"]
    assert snapshot.managed[0].publication_target_id == "target-current"
    assert [item.version_id for item in snapshot.transcripts] == ["tx-current"]
    assert snapshot.transcripts[0].publication_target_id == "tx-target"


def test_verify_file_sha256_rejects_changed_content(tmp_path: Path):
    module = load_script()
    source = tmp_path / "object"
    source.write_bytes(b"approved")
    expected = hashlib.sha256(b"approved").hexdigest()
    assert module.verify_file_sha256(source, expected) == 8

    with pytest.raises(ValueError, match="source_hash_mismatch"):
        module.verify_file_sha256(source, hashlib.sha256(b"other").hexdigest())


def test_shadow_destination_guard_accepts_workflow_run_directory(tmp_path: Path):
    module = load_script()
    parents = tmp_path / "full-reindex-32897731751-1-shadow" / "parents.sqlite"
    module.validate_shadow_destinations(
        "pincheng_docs_rebuild_32897731751_1",
        parents,
    )

    with pytest.raises(ValueError, match="shadow_parent_database_required"):
        module.validate_shadow_destinations(
            "pincheng_docs_rebuild_32897731751_1",
            tmp_path / "production" / "parents.sqlite",
        )

    with pytest.raises(ValueError, match="shadow_destination_mismatch"):
        module.validate_shadow_destinations(
            "pincheng_docs_rebuild_32897731751_1",
            tmp_path / "full-reindex-32897731751-2-shadow" / "parents.sqlite",
        )


def test_validate_report_requires_exact_head_coverage_and_green_collection():
    module = load_script()
    report = {
        "expected": {"managed_heads": 2, "transcript_heads": 1},
        "indexed": {
            "managed_heads": 2,
            "transcript_heads": 1,
            "parents": 10,
            "children": 20,
        },
        "parents_integrity": "ok",
        "qdrant": {"status": "green", "points_count": 20},
        "location_head_coverage": {"expected": 2, "located": 2},
    }
    module.validate_report(report)

    report["indexed"]["managed_heads"] = 1
    with pytest.raises(ValueError, match="managed_head_coverage_mismatch"):
        module.validate_report(report)

    report["indexed"]["managed_heads"] = 2
    report["location_head_coverage"]["located"] = 1
    with pytest.raises(ValueError, match="location_head_coverage_mismatch"):
        module.validate_report(report)
