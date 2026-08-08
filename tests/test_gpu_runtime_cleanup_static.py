from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / "scripts" / "cleanup-gpu-runtime.ps1").read_text(encoding="utf-8")
INSTALL = (ROOT / "scripts" / "install-gpu-runtime-cleanup-task.ps1").read_text(
    encoding="utf-8"
)


def test_cleanup_is_dry_run_by_default_and_requires_apply():
    assert "[switch]$Apply" in CLEANUP
    assert "if (-not $Apply)" in CLEANUP
    assert "Preview only" in CLEANUP
    assert "ShouldProcess" in CLEANUP


def test_cleanup_is_locked_to_the_exact_production_runtime_root():
    assert "D:\\RAGPinCheng\\runtime" in CLEANUP
    assert "Refusing to operate outside the exact production runtime root" in CLEANUP
    assert "Test-PathUnderRoot" in CLEANUP
    assert "ReparsePoint" in CLEANUP


def test_cleanup_protects_current_release_and_seed_cache():
    assert "current-release.json" in CLEANUP
    assert "current or retained rollback release" in CLEANUP
    assert "wheel-seed" in CLEANUP
    assert "manual Torch wheel seed is permanently protected" in CLEANUP
    assert "pip-cache" in CLEANUP
    assert "releases" in CLEANUP
    assert "qualification" in CLEANUP
    assert "resolver" in CLEANUP


def test_cleanup_has_retention_and_delete_caps():
    assert "$ReleaseRetentionDays = 30" in CLEANUP
    assert "$ReleaseKeepCount = 2" in CLEANUP
    assert "$QualificationKeepCount = 3" in CLEANUP
    assert "$PipCacheMaxGB = 8" in CLEANUP
    assert "$MaxDeleteGB = 20" in CLEANUP
    assert "Candidate deletion exceeds the safety cap" in CLEANUP


def test_task_installer_defaults_to_dry_run_and_exact_paths():
    assert "[switch]$EnableApply" in INSTALL
    assert "D:\\RAGPinCheng" in INSTALL
    assert "D:\\RAGPinCheng\\runtime" in INSTALL
    assert "Register-ScheduledTask" in INSTALL
    assert "Mode: $(if ($EnableApply) { 'APPLY' } else { 'DRY RUN' })" in INSTALL
