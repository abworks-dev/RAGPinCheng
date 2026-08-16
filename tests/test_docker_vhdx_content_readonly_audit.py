from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "inspect-docker-vhdx-content-readonly.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "inspect-production-docker-vhdx-content.yml").read_text(
    encoding="utf-8"
)


def test_vhdx_content_audit_is_exact_read_only_and_fail_closed():
    assert "-ExpectedLogicalBytes 92712992768" in WORKFLOW
    assert "2026-08-08T16:50:03.9178356Z" in WORKFLOW
    assert "group: production-gpu-exclusive" in WORKFLOW
    assert "environment: production-asr" in WORKFLOW
    assert "'--options','ro,noload'" in SCRIPT
    assert "mount_read_only" in SCRIPT
    assert "storage-cim-and-exclusive-read" not in SCRIPT
    assert "classification='protected'" in SCRIPT
    assert "post-audit-state-not-restored" in SCRIPT
    assert "Get-FileHash" in SCRIPT
    assert "state_restored" in SCRIPT
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
