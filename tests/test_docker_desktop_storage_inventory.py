from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "inventory-docker-desktop-storage.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "inventory-production-docker-desktop-storage.yml").read_text(encoding="utf-8")


def test_inventory_is_aggregate_read_only_and_skips_reparse_points():
    assert "destructive_operations_executed=$false" in SCRIPT
    assert "docker_daemon_started=$false" in SCRIPT
    assert "wsl_distribution_started=$false" in SCRIPT
    assert "ReparsePoint" in SCRIPT
    assert "no absolute paths, distro names, or file names" in SCRIPT
    assert "--list','--quiet" in SCRIPT
    for forbidden in ("--shutdown", "--terminate", "--mount", "Optimize-VHD", "Remove-Item", "docker system prune"):
        assert forbidden not in SCRIPT
    assert "group: production-gpu-exclusive" in WORKFLOW
