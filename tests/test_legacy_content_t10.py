from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from api.content_storage import ContentStorage
from api.db import init_db
from scripts.legacy_content_migration import (
    LegacyMigrationError,
    apply_entries,
    load_import_entries,
    stage_entries,
    summary,
    verify_manifest,
    write_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def _database(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES ('t10-engineer','T10 Engineer','x','admin',1,1)"""
    )
    conn.commit()
    return conn


def _plan(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _entry(relative_path: str, payload: bytes, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "docs",
        "relative_path": relative_path,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "document_type": "pdf",
        "category_key": "company_standards",
        "disposition": "import_document",
    }
    row.update(changes)
    return row


def test_preflight_verifies_sources_and_returns_aggregate_summary(tmp_path: Path):
    conn = _database(tmp_path)
    docs = tmp_path / "docs"
    source = docs / "公司标准" / "guide.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf-content")
    plan = _plan(tmp_path / "plan.json", [_entry("公司标准/guide.pdf", b"pdf-content")])

    entries = load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)

    assert summary(entries) == {
        "file_count": 1,
        "total_bytes": 11,
        "by_category_key": {"company_standards": 1},
        "by_document_type": {"pdf": 1},
    }
    conn.close()


def test_preflight_cli_keeps_database_unchanged_and_redacts_paths(tmp_path: Path):
    conn = _database(tmp_path)
    database = tmp_path / "app.sqlite"
    conn.close()
    docs = tmp_path / "docs"
    source = docs / "公司标准" / "secret-name.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf-content")
    plan = _plan(
        tmp_path / "plan.json",
        [_entry("公司标准/secret-name.pdf", b"pdf-content")],
    )
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    output = tmp_path / "summary.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/preflight_legacy_content_t10.py"),
            "--docs-root",
            str(docs),
            "--plan",
            str(plan),
            "--database",
            str(database),
            "--expected-plan-sha256",
            plan_sha,
            "--expected-count",
            "1",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert "secret-name.pdf" not in result.stdout
    assert "secret-name.pdf" not in output.read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ready"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"relative_path": "../escape.pdf"}, "invalid_relative_path"),
        ({"sha256": "0" * 64}, "source_sha256_changed"),
        ({"category_key": "unknown"}, "invalid_import_entry"),
        ({"kind": "media"}, "invalid_import_entry"),
        ({"document_type": "markdown"}, "document_type_mismatch"),
    ],
)
def test_preflight_fails_closed_for_invalid_plan_entries(tmp_path: Path, changes, error):
    conn = _database(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.pdf").write_bytes(b"pdf")
    row = _entry("guide.pdf", b"pdf")
    row.update(changes)
    plan = _plan(tmp_path / "plan.json", [row])

    with pytest.raises(LegacyMigrationError, match=error):
        load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)
    conn.close()


@pytest.mark.parametrize("filename", ["report.preview.pdf", "sheet.PREVIEW.XLSX"])
def test_preflight_rejects_generated_preview_from_old_or_manual_plan(
    tmp_path: Path, filename: str
):
    conn = _database(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    payload = b"derived"
    (docs / filename).write_bytes(payload)
    document_type = "xlsx" if filename.lower().endswith("xlsx") else "pdf"
    plan = _plan(
        tmp_path / "plan.json",
        [_entry(filename, payload, document_type=document_type)],
    )

    with pytest.raises(LegacyMigrationError, match="generated_preview_rejected"):
        load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)
    conn.close()


def test_stage_preserves_relative_path_and_verifies_copy(tmp_path: Path):
    conn = _database(tmp_path)
    docs = tmp_path / "docs"
    source = docs / "公司标准" / "guide.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    plan = _plan(tmp_path / "plan.json", [_entry("公司标准/guide.pdf", b"pdf")])
    entries = load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)

    destination = tmp_path / "staged"
    stage_entries(entries, docs_root=docs, destination=destination)

    assert (destination / "公司标准" / "guide.pdf").read_bytes() == b"pdf"
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, entries, source_plan_sha256="a" * 64)
    verify_manifest(manifest, entries, source_plan_sha256="a" * 64)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LegacyMigrationError, match="manifest_entries_mismatch"):
        verify_manifest(manifest, entries, source_plan_sha256="a" * 64)
    with pytest.raises(LegacyMigrationError, match="destination_not_empty"):
        stage_entries(entries, docs_root=docs, destination=destination)
    conn.close()


def test_preflight_rejects_symlinked_path_component(tmp_path: Path):
    conn = _database(tmp_path)
    docs = tmp_path / "docs"
    real = docs / "real"
    real.mkdir(parents=True)
    (real / "guide.pdf").write_bytes(b"pdf")
    link = docs / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available")
    plan = _plan(tmp_path / "plan.json", [_entry("linked/guide.pdf", b"pdf")])

    with pytest.raises(LegacyMigrationError, match="source_symlink_rejected"):
        load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)
    conn.close()


def test_apply_registers_legacy_items_for_review_and_blocks_plan_reuse(tmp_path: Path):
    conn = _database(tmp_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='t10-engineer'").fetchone()[0]
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.pdf").write_bytes(b"pdf")
    plan = _plan(tmp_path / "plan.json", [_entry("guide.pdf", b"pdf")])
    entries = load_import_entries(conn, docs_root=docs, plan_path=plan, expected_count=1)
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()

    batch_id, imported = apply_entries(
        conn,
        ContentStorage(tmp_path / "content"),
        entries,
        docs_root=docs,
        actor_user_id=actor,
        batch_storage_rel_path="inbox/server/t10-test",
        source_plan_sha256=plan_sha,
    )

    assert imported == 1
    assert conn.execute("SELECT status FROM upload_batches WHERE id=?", (batch_id,)).fetchone()[0] == "ready_for_review"
    row = conn.execute("SELECT source_origin,lifecycle_status FROM content_versions").fetchone()
    assert tuple(row) == ("legacy", "awaiting_review")
    assert conn.execute("SELECT count(*) FROM content_item_heads").fetchone()[0] == 0
    with pytest.raises(LegacyMigrationError, match="legacy_plan_already_imported"):
        apply_entries(
            conn,
            ContentStorage(tmp_path / "content"),
            entries,
            docs_root=docs,
            actor_user_id=actor,
            batch_storage_rel_path="inbox/server/t10-test-retry",
            source_plan_sha256=plan_sha,
        )
    conn.close()
