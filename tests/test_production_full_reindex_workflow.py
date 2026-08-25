from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rebuild-production-index-manual.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_valid_yaml_and_manual_production_only():
    parsed = yaml.safe_load(workflow_text())
    assert parsed["name"] == "Rebuild Production Index Manual"
    text = workflow_text()
    assert "workflow_dispatch:" in text
    assert "REBUILD_PRODUCTION_INDEX" in text
    assert "environment: production-asr" in text
    assert "runs-on: [self-hosted, linux, ubuntu, production, app]" in text
    assert '${GITHUB_REF}" = "refs/heads/master' in text
    assert '${GITHUB_SHA}" = "${EXPECTED_COMMIT}' in text


def test_workflow_has_locks_drift_gates_and_independent_backups():
    text = workflow_text()
    assert ".managed-content-t9.lock" in text
    assert ".production-app-deploy.lock" in text
    assert "active_job_preflight initial" in text
    assert "active_job_preflight cutover" in text
    assert "HEAD_SNAPSHOT_SHA256" in text
    assert "HEAD_SNAPSHOT_CHANGED" in text
    assert "qdrant-before.snapshot" in text
    assert 'for name in ("app.sqlite", "parents.sqlite")' in text
    assert "findmnt -n -o MAJ:MIN" in text


def test_workflow_builds_shadow_then_cuts_over_with_rollback():
    text = workflow_text()
    assert "pincheng_docs_rebuild_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}" in text
    assert "scripts/rebuild_managed_index.py" in text
    assert "REBUILD_SHADOW status=verified" in text
    assert '"${COMPOSE[@]}" stop backend' in text
    assert "REBUILD_CUTOVER status=success" in text
    assert "REBUILD_ROLLBACK status=complete" in text
    assert "priority=snapshot" in text
    assert "REBUILD_BACKUP status=complete" in text
    assert "http://localhost:8000/api/health" in text


def test_workflow_never_deletes_volumes_or_the_live_collection():
    text = workflow_text()
    assert "down -v" not in text
    assert "docker volume rm" not in text
    assert "DELETE http://qdrant:6333/collections/pincheng_docs" not in text
    assert "rm -rf" not in text
    assert 'DST="${DATA_PATH}/app.sqlite"' not in text
