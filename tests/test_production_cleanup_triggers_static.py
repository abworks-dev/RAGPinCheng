from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_disk_pressure_checker_is_read_only():
    script = read_text("scripts/check-production-disk.ps1")

    assert "Win32_LogicalDisk" in script
    assert "Remove-Item" not in script
    assert "Move-Item" not in script
    assert "auto_backup_eligible" in script


def test_cleanup_workflow_has_safe_trigger_gates():
    workflow = read_text(".github/workflows/cleanup-production.yml")

    assert "workflow_call:" in workflow
    assert '"on":' not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "schedule:" not in workflow
    assert "nightly-dryrun:" not in workflow
    assert "disk-pressure:" not in workflow
    assert "confirm_production_cleanup" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_cleanup_operations_owns_manual_and_scheduled_triggers():
    workflow = read_text(".github/workflows/cleanup-production-operations.yml")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "30 19 * * *" in workflow
    assert "*/30 * * * *" in workflow
    assert "cleanup-production.yml" in workflow
    assert "PRODUCTION_AUTO_CLEANUP_ENABLED" in workflow
    assert "backup-apply" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_deployment_cleanup_waits_for_both_deployment_jobs():
    workflow = read_text(".github/workflows/deploy-production.yml")

    assert "cleanup-after-deploy:" in workflow
    assert "needs: [deploy-gpu, deploy-app]" in workflow
    assert "target: backups" in workflow
    assert "apply: true" in workflow
    assert "confirm_production_cleanup: true" in workflow
