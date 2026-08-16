from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "audit-docker-vhdx-references.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "audit-production-docker-vhdx.yml").read_text(encoding="utf-8")


def test_vhdx_audit_is_read_only_private_and_fail_closed():
    assert "destructive_operations_executed=$false" in SCRIPT
    assert "docker_daemon_started=$false" in SCRIPT
    assert "disk_images_mounted=$false" in SCRIPT
    assert "attachment-state-unknown" in SCRIPT
    assert "exclusive-read-unavailable" in SCRIPT
    assert "installed-default-data-root" in SCRIPT
    assert "anonymous VHDX identifiers" in SCRIPT
    for forbidden in ("Mount-DiskImage", "Dismount-DiskImage", "Optimize-VHD", "Remove-Item", "Start-Service", "Start-Process"):
        assert forbidden not in SCRIPT
    assert "group: production-gpu-exclusive" in WORKFLOW
