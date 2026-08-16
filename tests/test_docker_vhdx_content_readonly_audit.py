from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "inspect-docker-vhdx-content-readonly.ps1").read_text(encoding="utf-8")
ORCHESTRATOR = (ROOT / "scripts" / "inspect-docker-vhdx-content-quiesced.ps1").read_text(
    encoding="utf-8"
)
WORKFLOW = (ROOT / ".github" / "workflows" / "inspect-production-docker-vhdx-content.yml").read_text(
    encoding="utf-8"
)


def test_vhdx_content_audit_is_exact_read_only_and_fail_closed():
    assert "-ExpectedLogicalBytes 92712992768" in WORKFLOW
    assert "2026-06-04T07:58:58.2353948Z" in WORKFLOW
    assert "inspect-docker-vhdx-content-quiesced.ps1" in WORKFLOW
    assert "group: production-gpu-exclusive" in WORKFLOW
    assert "environment: production-asr" in WORKFLOW
    assert "'--options','ro,noload'" in SCRIPT
    assert "mount_read_only" in SCRIPT
    assert "storage-cim-and-exclusive-read" not in SCRIPT
    assert "classification='protected'" in SCRIPT
    assert "post-audit-state-not-restored" in SCRIPT
    assert "Get-FileHash" in SCRIPT
    assert "state_restored" in SCRIPT
    assert "offline-sevenzip" in SCRIPT
    assert "Read-SevenZipAggregate" in SCRIPT
    assert "No approved read-only VHDX inspection capability is available" in SCRIPT
    assert "$postHash=if ($preHash)" in SCRIPT
    assert "Get-MountCapableWslPath" in SCRIPT
    assert "-tVHDX" in SCRIPT
    assert "if (-not $values)" in SCRIPT
    assert "Get-WslHelp $candidate" in SCRIPT
    assert "ConvertFrom-WslHelpBytes" in SCRIPT
    assert ".Replace(\"$([char]0)\",'')" in SCRIPT
    assert "set -eu" not in SCRIPT
    assert "shellBase64" in SCRIPT
    assert "base64 -d | sh" in SCRIPT
    assert "ignored_output_lines" in SCRIPT
    assert "contains duplicate fields" in SCRIPT
    for forbidden in (
        "Remove-Item",
        "Optimize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "Start-Service",
        "Stop-Service",
        "docker system prune",
        "wsl.exe --shutdown",
        "wsl.exe --terminate",
    ):
        assert forbidden not in SCRIPT


def test_vhdx_content_audit_reports_only_aggregate_inventory():
    assert "no paths, names, content, settings values, or command output" in SCRIPT
    assert "volume_count" in SCRIPT
    assert "volume_bytes" in SCRIPT
    assert "sensitive_markers" in SCRIPT
    assert "persistent-or-sensitive-content-present" in SCRIPT
    assert "docker-storage-found-without-persistent-volume-data" in SCRIPT
    assert "docker-storage-layout-inconclusive" in SCRIPT


def test_quiesced_orchestrator_gates_stop_and_restores_runtime():
    for expected in (
        "docker.exe' @('ps','-q')",
        "Running Docker containers are present",
        "runner identity; restoration is not guaranteed",
        "Docker\\Docker\\Docker Desktop.exe",
        "restart executable identity is invalid",
        "Stop-Process",
        "@('--terminate','docker-desktop')",
        "global_wsl_shutdown_requested=$false",
        "ExpectedCreatedUtc",
        "Test-ExclusiveRead",
        "inspect-docker-vhdx-content-readonly.ps1",
        "Start-Process -FilePath $desktopExecutable",
        "restore_status",
        "no_local_backup_accepted=$true",
        "stdout_base64",
        "FromBase64String",
        "baseline_mode='inactive-runtime'",
        "activeRuntime",
        "runtime state is inconsistent",
        "Inactive Docker runtime state was not restored",
    ):
        assert expected in ORCHESTRATOR
    for forbidden in (
        "--shutdown",
        "docker system prune",
        "Remove-Item",
        "Optimize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
    ):
        assert forbidden not in ORCHESTRATOR
