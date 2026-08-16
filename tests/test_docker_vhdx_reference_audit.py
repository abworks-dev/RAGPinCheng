from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "audit-docker-vhdx-references.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "audit-production-docker-vhdx.yml").read_text(encoding="utf-8")


def test_vhdx_audit_is_read_only_private_and_fail_closed():
    assert "destructive_operations_executed=$false" in SCRIPT
    assert "docker_daemon_started=$false" in SCRIPT
    assert "disk_images_mounted=$false" in SCRIPT
    assert "wsl_distribution_started=$false" in SCRIPT
    assert "attachment-state-unknown" in SCRIPT
    assert "attachment-state-conflict" in SCRIPT
    assert "exclusive-read-unavailable" in SCRIPT
    assert "installed-default-data-root" in SCRIPT
    assert "NativeVirtualDiskState" in SCRIPT
    assert "GET_VIRTUAL_DISK_INFO_IS_LOADED" in SCRIPT
    assert "MSFT_DiskImage" in SCRIPT
    assert "wsl-registered-root-reference" in SCRIPT
    assert "schema_version='docker-vhdx-reference-audit/2'" in SCRIPT
    assert "anonymous VHDX identifiers" in SCRIPT
    for forbidden in (
        "Mount-DiskImage",
        "Dismount-DiskImage",
        "Optimize-VHD",
        "Remove-Item",
        "Start-Service",
        "Start-Process",
        "wsl.exe",
        "--mount",
        "AttachVirtualDisk",
        "DetachVirtualDisk",
    ):
        assert forbidden not in SCRIPT
    assert "group: production-gpu-exclusive" in WORKFLOW
    assert "timeout-minutes: 10" in WORKFLOW


def test_orphan_classification_requires_confirmed_detachment_and_no_runtime_references():
    assert "$directAttachmentKnown=($nativeState.status -eq 'known'" in SCRIPT
    assert "$corroboratedDetached=(-not $attached" in SCRIPT
    assert "$cimState.attached -eq $false" in SCRIPT
    assert "$exclusive -eq 'available'" in SCRIPT
    assert "attachment_state_basis=$attachmentStateBasis" in SCRIPT
    assert "$attachmentConflict" in SCRIPT
    assert "$wslRegisteredReference" in SCRIPT
    assert "$wslRegistryStatus -ne 'known' -and $wslRuntimeActive" in SCRIPT
    assert "elseif (-not $installed -and -not $configuredReference)" in SCRIPT
