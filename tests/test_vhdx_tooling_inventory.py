from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "inventory-vhdx-tooling.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "inventory-production-vhdx-tooling.yml").read_text(
    encoding="utf-8"
)


def test_tooling_inventory_is_read_only_private_and_serialized():
    assert "group: production-gpu-exclusive" in WORKFLOW
    assert "environment: production-asr" in WORKFLOW
    assert "destructive_operations_executed=$false" in SCRIPT
    assert "tools_downloaded=$false" in SCRIPT
    assert "tools_installed=$false" in SCRIPT
    assert "windows_features_changed=$false" in SCRIPT
    assert "wsl_distribution_started=$false" in SCRIPT
    assert "docker_started=$false" in SCRIPT
    assert "vhdx_mounted=$false" in SCRIPT
    assert "vhdx_hashed=$false" in SCRIPT
    assert "no paths, names, settings values, or command output" in SCRIPT
    for forbidden in (
        "Mount-DiskImage",
        "Mount-VHD",
        "wsl.exe --mount",
        "Start-Service",
        "Start-Process",
        "Invoke-WebRequest",
        "Install-Module",
        "Add-WindowsCapability",
        "Enable-WindowsOptionalFeature",
        "Get-FileHash",
    ):
        assert forbidden not in SCRIPT


def test_tooling_inventory_covers_required_capabilities():
    for expected in (
        "Win32_OperatingSystem",
        "Get-WindowsOptionalFeature",
        "supports_mount",
        "supports_vhdx",
        "supports_ext",
        "guestfish.exe",
        "qemu-img.exe",
        "Hyper-V",
        "MSFT_DiskImage",
        "system_drive_free_bytes",
    ):
        assert expected in SCRIPT


def test_wsl_capability_capture_includes_stderr_without_logging_raw_output():
    assert "--version 2>&1 | Out-String" in SCRIPT
    assert "--help 2>&1 | Out-String" in SCRIPT
