from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def read_workflow(path: str) -> dict:
    return yaml.safe_load(read_text(path))


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
    assert "actions/checkout@v4" in workflow
    assert "${{ github.workspace }}" in workflow
    assert "PRODUCTION_REPO_PATH" not in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_cleanup_workflow_uses_named_parameter_splatting():
    workflow = read_text(".github/workflows/cleanup-production.yml")

    assert "$arguments = @{" in workflow
    assert "Target                         = $target" in workflow
    assert "AsrDataRoot                    = $env:ASR_DATA_ROOT" in workflow
    assert "AsrProgramRoot                 = $env:ASR_PROGRAM_ROOT" in workflow
    assert "FasterWhisperQualificationRoot = $env:FASTER_WHISPER_QUALIFICATION_ROOT" in workflow
    assert "Qwen3AsrQualificationRoot       = $env:QWEN3_ASR_QUALIFICATION_ROOT" in workflow
    assert "WhisperXRoot                    = $env:WHISPERX_ROOT" in workflow
    assert "Confirm                         = $false" in workflow
    assert "$arguments.Apply = $true" in workflow
    assert "'-Target', $target" not in workflow
    assert "$arguments += '-Apply'" not in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "ExpectedAsrBatchManifestSha256" in workflow
    assert "asr_manifest_run_id" in workflow
    assert "asr_manifest_sha256:" in workflow
    assert "value: ${{ jobs.cleanup.outputs.asr_manifest_sha256 }}" in workflow
    assert "id: cleanup" in workflow
    assert "asr_manifest_sha256=$manifestSha256" in workflow
    assert "if: ${{ always() && inputs.apply == false }}" in workflow
    assert "if: ${{ always() && inputs.apply == true }}" in workflow
    assert "name: production-cleanup-${{ github.run_id }}-${{ github.run_attempt }}-apply" in workflow
    assert "asr_manifest_artifact_name:" in workflow
    assert "asr_manifest_artifact_name=$previewArtifactName" in workflow
    assert "github.run_attempt" in workflow


def test_cleanup_orchestrator_keeps_runtime_audit_inside_managed_root():
    script = read_text("scripts/cleanup-production.ps1")

    assert "$runtimeAuditRoot = Join-Path $RuntimeRoot 'cleanup-audit'" in script
    assert 'Join-Path $runtimeAuditRoot "orchestrated-$runId.json"' in script
    assert "$artifactAuditPath = Join-Path $auditRoot 'runtime.json'" in script
    assert "Copy-Item -LiteralPath $arguments.AuditPath" in script
    assert "Runtime cleanup did not produce its managed audit report" in script
    assert "$arguments.AuditPath = Join-Path $auditRoot 'runtime.json'" not in script


def test_cleanup_operations_owns_manual_and_scheduled_triggers():
    workflow = read_text(".github/workflows/cleanup-production-operations.yml")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "23 19 * * *" in workflow
    assert "7,37 * * * *" in workflow
    assert "30 19 * * *" not in workflow
    assert "*/30 * * * *" not in workflow
    assert "cleanup-production.yml" in workflow
    assert "PRODUCTION_AUTO_CLEANUP_ENABLED" in workflow
    assert "actions/checkout@v4" in workflow
    assert "${{ github.workspace }}" in workflow
    assert "PRODUCTION_REPO_PATH" not in workflow
    assert "backup-apply" in workflow
    assert "auto-clean-asr:" in workflow
    assert "needs: [disk-pressure, pressure-dryrun]" in workflow
    assert "asr_manifest_run_id: ${{ github.run_id }}" in workflow
    assert "asr_manifest_sha256: ${{ needs.pressure-dryrun.outputs.asr_manifest_sha256 }}" in workflow
    assert "target: asr" in workflow
    assert "apply: true" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions: read" in workflow


def test_disk_pressure_cleanup_jobs_are_sequenced_and_identity_locked():
    jobs = read_workflow(".github/workflows/cleanup-production-operations.yml")["jobs"]
    asr = jobs["auto-clean-asr"]
    backups = jobs["auto-clean-backups"]

    assert asr["needs"] == ["disk-pressure", "pressure-dryrun"]
    assert "needs.disk-pressure.outputs.tier == 'backup-apply'" in asr["if"]
    assert "vars.PRODUCTION_AUTO_CLEANUP_ENABLED == 'true'" in asr["if"]
    assert asr["with"]["asr_manifest_artifact_name"] == (
        "${{ needs.pressure-dryrun.outputs.asr_manifest_artifact_name }}"
    )
    assert backups["needs"] == ["disk-pressure", "pressure-dryrun", "auto-clean-asr"]
    assert "always()" in backups["if"]
    assert "needs.auto-clean-asr.result" in backups["if"]


def test_storage_inventory_is_aggregate_and_read_only():
    script = read_text("scripts/inventory-production-storage.ps1")
    workflow = read_text(".github/workflows/inventory-production-storage.yml")

    assert "production-storage-inventory/1" in script
    assert "directory metadata only; no nested file names or file contents" in script
    assert "Remove-Item" not in script
    assert "Move-Item" not in script
    assert "Set-Content" in script
    assert "workflow_dispatch:" in workflow
    assert "confirm_read_only_inventory" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "-DependencyRetentionDays 7" in workflow
    assert "-ReleaseKeepCount 2" in workflow
    assert "-QualificationKeepCount 3" in workflow
    assert "-ResolverRetentionDays 14" in workflow
    assert "asr_qualification_inventory" in script
    assert "asr_model_preparation_inventory" in script
    assert "gpu_model_cache_repair_inventory" in script
