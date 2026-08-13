from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-managed-content-legacy-index.yml").read_text(
    encoding="utf-8"
)


def test_t11_workflow_is_manual_fixed_master_compat_and_exact_count():
    assert "workflow_dispatch:" in WORKFLOW
    assert 'CONFIRM_PRODUCTION}" = "RETIRE_LEGACY_INDEX_T11"' in WORKFLOW
    assert 'GITHUB_REF}" = "refs/heads/master"' in WORKFLOW
    assert 'GITHUB_SHA}" = "${EXPECTED_COMMIT}"' in WORKFLOW
    assert 'EXPECTED_HEAD_ENFORCEMENT}" = "compat"' in WORKFLOW
    assert "EXPECTED_IMPORT_RECORD_COUNT=117" in WORKFLOW
    assert "EXPECTED_HEAD_COUNT=116" in WORKFLOW
    assert "EXPECTED_EXCLUDED_PREVIEW_COUNT=1" in WORKFLOW
    assert "EXPECTED_HEAD_COUNT + EXPECTED_EXCLUDED_PREVIEW_COUNT" in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW


def test_t11_workflow_has_locks_backups_exact_apply_and_rollback():
    for required in (
        ".managed-content-t9.lock",
        ".production-app-deploy.lock",
        "PRAGMA integrity_check",
        "qdrant-snapshot.json",
        "qdrant.snapshot",
        "plan_legacy_index_retirement.py",
        "apply_legacy_index_retirement.py",
        "--expected-plan-sha256",
        "--confirm RETIRE_LEGACY_INDEX_T11",
        "T11_ROLLBACK status=complete",
        "T11_RETIRE status=success",
    ):
        assert required in WORKFLOW
    for forbidden in ("delete_collection", "rm -rf", "docker compose down", "source/media", "CONTENT_HEAD_ENFORCEMENT=strict"):
        assert forbidden not in WORKFLOW


def test_t11_public_artifact_is_redacted_summary_only():
    assert "path: ${{ runner.temp }}/t11-${{ github.run_id }}-${{ github.run_attempt }}-summary" in WORKFLOW
    assert "retirement-plan.json" not in WORKFLOW.split("- name: Upload redacted T11 summary", 1)[1]
