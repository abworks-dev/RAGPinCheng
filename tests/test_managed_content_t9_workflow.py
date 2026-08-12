from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/prepare-managed-content-t9.yml").read_text(
    encoding="utf-8"
)
DIAGNOSTIC = (
    ROOT / ".github/workflows/diagnose-managed-content-t9-storage.yml"
).read_text(encoding="utf-8")


def test_t9_workflow_is_manual_fixed_master_and_fail_closed():
    assert "workflow_dispatch:" in WORKFLOW
    assert "PREPARE_T9" in WORKFLOW
    assert '[ "${GITHUB_REF}" = "refs/heads/master" ]' in WORKFLOW
    assert '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]' in WORKFLOW
    assert 'rev-parse origin/master)" = "${EXPECTED_COMMIT}"' in WORKFLOW
    assert '[ "${EXPECTED_HEAD_ENFORCEMENT}" = "compat" ]' in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW


def test_t9_workflow_requires_independent_backup_and_capacity():
    assert "findmnt -T /data/business" in WORKFLOW
    assert 'findmnt -T "${DATA_PATH}"' in WORKFLOW
    assert "findmnt -T /data/backup" in WORKFLOW
    assert '[ "${BUSINESS_DEVICE}" != "${BACKUP_DEVICE}" ]' in WORKFLOW
    assert '[ "${DATA_DEVICE}" != "${BACKUP_DEVICE}" ]' in WORKFLOW
    assert "REQUIRED_BYTES=$((SOURCE_BYTES + SOURCE_BYTES / 5 + 1073741824))" in WORKFLOW
    assert '[ "${FREE_BYTES}" -ge "${REQUIRED_BYTES}" ]' in WORKFLOW
    assert ".production-app-deploy.lock" in WORKFLOW
    assert ".managed-content-t9.lock" in WORKFLOW


def test_t9_workflow_creates_all_recovery_points_before_inventory():
    database_backup = WORKFLOW.index('for name in ("app.sqlite", "parents.sqlite")')
    qdrant_snapshot = WORKFLOW.index("collections/pincheng_docs/snapshots")
    content_backup = WORKFLOW.index('"${CONTENT_ROOT}/" "${RUN_ROOT}/content/"')
    docs_backup = WORKFLOW.index('"${DOCS_ROOT}/" "${RUN_ROOT}/legacy-docs/"')
    media_backup = WORKFLOW.index('"${MEDIA_ROOT}/" "${RUN_ROOT}/legacy-media/"')
    inventory = WORKFLOW.index("inventory_legacy_content.py\" \\")
    assert database_backup < inventory
    assert qdrant_snapshot < inventory
    assert '"${RUN_ROOT}/qdrant.snapshot"' in WORKFLOW
    assert "independent Qdrant snapshot download is empty" in WORKFLOW
    assert content_backup < inventory
    assert docs_backup < inventory
    assert media_backup < inventory


def test_t9_workflow_is_read_only_for_legacy_content_and_redacts_artifact():
    assert 'LEGACY_ROOT="/data/business/ragpincheng/source"' in WORKFLOW
    assert '--docs-root "${RUN_ROOT}/legacy-docs" --media-root "${RUN_ROOT}/legacy-media"' in WORKFLOW
    assert "--checksum --delete --dry-run" in WORKFLOW
    assert "plan_legacy_content_migration.py" in WORKFLOW
    assert "--apply" not in WORKFLOW
    assert "docker compose down" not in WORKFLOW
    assert "rm -rf" not in WORKFLOW
    assert "Full paths, names, hashes and file contents remain only" in WORKFLOW
    assert "path: ${{ runner.temp }}/t9-" in WORKFLOW


def test_t9_workflow_preserves_approved_mapping_boundary():
    for category in (
        "industry_standards",
        "client_requirements",
        "company_standards",
        "training_materials",
    ):
        assert category in WORKFLOW
    assert '"legacy_prefix":"教学视频"' in WORKFLOW
    assert '"legacy_prefix":"培训视频"' in WORKFLOW
    assert '"handling":"transcript"' in WORKFLOW


def test_t9_storage_diagnostic_is_fixed_master_and_read_only():
    assert "workflow_dispatch:" in DIAGNOSTIC
    assert '[ "${GITHUB_REF}" = "refs/heads/master" ]' in DIAGNOSTIC
    assert '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]' in DIAGNOSTIC
    assert "findmnt -rn -o TARGET,MAJ:MIN,FSTYPE,AVAIL" in DIAGNOSTIC
    assert "findmnt -T \"${path}\" -n -o MAJ:MIN" in DIAGNOSTIC
    for forbidden in ("sudo ", "rm ", "cp ", "rsync ", "sqlite", "docker "):
        assert forbidden not in DIAGNOSTIC
