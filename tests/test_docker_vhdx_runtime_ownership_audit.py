from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "audit-docker-vhdx-runtime-ownership.ps1").read_text(encoding="utf-8")
WORKFLOW = (
    ROOT / ".github" / "workflows" / "audit-production-docker-vhdx-runtime-ownership.yml"
).read_text(encoding="utf-8")


def test_runtime_ownership_audit_is_read_only_and_private():
    for expected in (
        "destructive_operations_executed=$false",
        "processes_stopped=$false",
        "services_changed=$false",
        "docker_started=$false",
        "wsl_distribution_started=$false",
        "wsl_shutdown_requested=$false",
        "disk_images_mounted=$false",
        "no PIDs, paths, command lines, distribution names",
    ):
        assert expected in SCRIPT
    for forbidden in (
        "Stop-Process",
        "Stop-Service",
        "Start-Service",
        "--shutdown",
        "--terminate",
        "--mount",
        "Mount-DiskImage",
        "Dismount-DiskImage",
        "CommandLine",
        "ExecutablePath",
    ):
        assert forbidden not in SCRIPT


def test_runtime_ownership_audit_classifies_without_raw_names():
    for expected in (
        "--list --running --quiet",
        "docker_desktop",
        "docker_desktop_data",
        "non_docker_count",
        "process_categories",
        "query_stdout_bytes",
        "audit-docker-vhdx-references.ps1",
        "group: production-gpu-exclusive",
        "environment: production-asr",
    ):
        assert expected in SCRIPT or expected in WORKFLOW
