from pathlib import Path


WORKFLOW = Path(".github/workflows/diagnose-category-force-delete-production.yml")


def test_force_delete_diagnostic_is_explicitly_read_only_and_sanitized() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "DIAGNOSE_READ_ONLY" in source
    assert "refs/heads/master" in source
    assert "mode=ro" in source
    assert "PRAGMA query_only=ON" in source
    assert "contents: read" in source
    assert "error_type_counts" in source
    assert "residual_path_classes" in source
    assert "hashlib.sha256" in source

    forbidden_mutations = (
        "DELETE FROM",
        "UPDATE ",
        "INSERT INTO",
        ".unlink(",
        "shutil.rmtree",
        "os.remove(",
    )
    for mutation in forbidden_mutations:
        assert mutation not in source
