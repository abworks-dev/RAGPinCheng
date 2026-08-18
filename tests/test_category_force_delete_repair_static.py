from pathlib import Path


WORKFLOW = Path(".github/workflows/repair-category-force-delete-production.yml")


def test_force_delete_repair_is_narrowly_guarded_and_backed_up() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "REPAIR_BATCH_RESIDUALS",
        "run_ref",
        "expected_batch_count",
        "category-force-delete-repair-",
        "app.sqlite",
        "parents.sqlite",
        "PRAGMA foreign_keys=ON",
        "batch:IntegrityError",
        "content_audit_events SET batch_id=NULL",
        "PRAGMA foreign_key_check",
        "PRAGMA integrity_check",
    ):
        assert marker in source

    assert "pincheng_docs" not in source
    assert "qdrant" not in source.lower()
    assert "unlink(" not in source
    assert "shutil.rmtree" not in source
