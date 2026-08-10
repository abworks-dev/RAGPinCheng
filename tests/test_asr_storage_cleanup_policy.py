from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPACT_SCRIPT = ROOT / "scripts" / "compact-asr-run.ps1"
CLEANUP_SCRIPT = ROOT / "scripts" / "cleanup-asr-storage.ps1"


def _powershell() -> str | None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ps51 = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if ps51.is_file():
        return str(ps51)
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _run(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_compaction_apply(managed_root: Path, audit_path: Path) -> subprocess.CompletedProcess[str]:
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        f"& '{COMPACT_SCRIPT}' -TargetKind qualification -Engine faster-whisper "
        f"-ManagedRoot '{managed_root}' -Identity 123456 -AuditPath '{audit_path}' "
        "-Apply -Confirm:$false"
    )
    return subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_tree(root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "payload.bin").write_bytes(name.encode("ascii"))


def test_exact_run_compaction_is_dry_run_by_default_and_preserves_evidence(tmp_path: Path):
    managed_root = tmp_path / "faster-whisper"
    run_root = managed_root / "runs" / "123456"
    heavy = ("venv", "wheelhouse", "shared-wheel-seed", "model-staging", "spool", "temp")
    preserved = ("reports", "evidence", "logs", "state", "config", "models")
    _write_tree(run_root, heavy + preserved)
    dry_run_audit = tmp_path / "dry-run.json"

    dry_run = _run(
        COMPACT_SCRIPT,
        "-TargetKind",
        "qualification",
        "-Engine",
        "faster-whisper",
        "-ManagedRoot",
        str(managed_root),
        "-Identity",
        "123456",
        "-AuditPath",
        str(dry_run_audit),
    )

    assert dry_run.returncode == 0, dry_run.stderr
    assert all((run_root / name).is_dir() for name in heavy + preserved)
    dry_run_report = _read_json(dry_run_audit)
    assert dry_run_report["mode"] == "dry-run"
    assert {Path(item["Path"]).name for item in dry_run_report["candidates"]} == set(heavy)

    apply_audit = tmp_path / "apply.json"
    applied = _run_compaction_apply(managed_root, apply_audit)

    assert applied.returncode == 0, applied.stderr
    assert all(not (run_root / name).exists() for name in heavy)
    assert all((run_root / name).is_dir() for name in preserved)
    apply_report = _read_json(apply_audit)
    assert apply_report["mode"] == "apply"
    assert all(item["Deleted"] is True for item in apply_report["candidates"])


def test_periodic_cleanup_discovers_real_run_layouts_without_targeting_evidence(tmp_path: Path):
    data_root = tmp_path / "data" / "RAGPinCheng-ASR"
    program_root = tmp_path / "program" / "RAGPinCheng-ASR"
    qwen_root = tmp_path / "qualification" / "qwen3-asr"
    data_root.mkdir(parents=True)
    program_root.mkdir(parents=True)

    old_timestamp = 1_600_000_000
    for index, run_id in enumerate(("101", "102", "103", "104")):
        run_root = qwen_root / "runs" / run_id
        _write_tree(run_root, ("venv", "evidence", "reports"))
        os.utime(run_root, (old_timestamp + index, old_timestamp + index))

    dependency = data_root / "dependency-runs" / ("funasr-" + "a" * 40)
    _write_tree(dependency, ("wheelhouse",))
    os.utime(dependency, (old_timestamp, old_timestamp))

    for index in range(3):
        staging = data_root / "backups" / f"failed-staging-20200101-00000000{index}-{'b' * 12}"
        _write_tree(staging, ("venv",))
        os.utime(staging, (old_timestamp + index, old_timestamp + index))

    audit_path = tmp_path / "cleanup.json"
    result = _run(
        CLEANUP_SCRIPT,
        "-DataRoot",
        str(data_root),
        "-ProgramRoot",
        str(program_root),
        "-Qwen3AsrQualificationRoot",
        str(qwen_root),
        "-AuditPath",
        str(audit_path),
    )

    assert result.returncode == 0, result.stderr
    report = _read_json(audit_path)
    assert report["mode"] == "dry-run"
    candidates = report["candidates"]
    assert any(item["Kind"] == "qualification-run" for item in candidates)
    assert any(item["Kind"] == "qualification-venv" for item in candidates)
    assert any(item["Kind"] == "dependency-run" for item in candidates)
    assert any(item["Kind"] == "failed-staging" for item in candidates)
    assert not any(Path(item["Path"]).name in {"evidence", "reports"} for item in candidates)
    assert all(path.exists() for path in (data_root, program_root, qwen_root))


def test_periodic_cleanup_reports_zero_candidates_on_powershell_51(tmp_path: Path):
    data_root = tmp_path / "data" / "RAGPinCheng-ASR"
    program_root = tmp_path / "program" / "RAGPinCheng-ASR"
    data_root.mkdir(parents=True)
    program_root.mkdir(parents=True)
    audit_path = tmp_path / "empty-cleanup.json"

    result = _run(
        CLEANUP_SCRIPT,
        "-DataRoot",
        str(data_root),
        "-ProgramRoot",
        str(program_root),
        "-AuditPath",
        str(audit_path),
    )

    assert result.returncode == 0, result.stderr
    report = _read_json(audit_path)
    assert report["mode"] == "dry-run"
    assert report["candidate_count"] == 0
    assert report["candidate_bytes"] == 0


def test_cleanup_sources_use_explicit_roots_and_exclude_protected_storage():
    compact = COMPACT_SCRIPT.read_text(encoding="utf-8")
    cleanup = CLEANUP_SCRIPT.read_text(encoding="utf-8")
    entry = (ROOT / "scripts" / "cleanup-production.ps1").read_text(encoding="utf-8")
    workflows = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            ".github/workflows/cleanup-production.yml",
            ".github/workflows/cleanup-production-operations.yml",
        )
    )

    assert "$qualificationChildren = @(" in compact
    for child in ("venv", "wheelhouse", "shared-wheel-seed", "model-staging", "spool", "temp"):
        assert f"'{child}'" in compact
    assert "cleanup-evidence-backup" in cleanup
    assert "PRODUCTION_ASR_ROOT" not in compact + cleanup + entry + workflows
    for protected in ("models", "wheel-cache", "app", "venv-backup"):
        assert f"qualification-{protected}" not in compact


def test_storage_mutating_workflows_share_concurrency_and_default_to_dry_run():
    paths = (
        ".github/workflows/qualify-faster-whisper-production.yml",
        ".github/workflows/qualify-qwen3-asr-production.yml",
        ".github/workflows/qualify-whisperx-production.yml",
        ".github/workflows/deploy-asr-production.yml",
        ".github/workflows/cleanup-production.yml",
    )
    for path in paths:
        workflow = (ROOT / path).read_text(encoding="utf-8")
        assert "group: production-gpu-exclusive" in workflow

    for path in paths[:3]:
        workflow = (ROOT / path).read_text(encoding="utf-8")
        assert "PRODUCTION_ASR_RUN_COMPACTION_ENABLED" in workflow
        assert "QUALIFICATION_JOB_STATUS" in workflow
        assert "Compact qualification run after evidence upload" in workflow
        assert workflow.index("Upload sanitized") < workflow.index(
            "Compact qualification run after evidence upload"
        )
        assert "if ($env:COMPACTION_ENABLED -eq 'true'" in workflow

    deploy = (ROOT / paths[3]).read_text(encoding="utf-8")
    assert "TargetKind = 'deployment-dependency'" in deploy
    assert "Identity = '${{ inputs.commit_sha }}'" in deploy
    assert "compact-dependency-run:" in deploy
    assert "needs: [deploy, verify-ubuntu]" in deploy
    assert "needs.deploy.result == 'success'" in deploy
    assert "needs.verify-ubuntu.result == 'success'" in deploy
    assert "COMPACTION_AUDIT: ${{ runner.temp }}" not in deploy
    assert (
        '$auditPath = Join-Path $env:RUNNER_TEMP '
        '"asr-deployment-compaction-${{ github.run_id }}.json"'
    ) in deploy
    assert "AuditPath = $auditPath" in deploy
