from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cleanup-asr-candidate-dependency.ps1"


def _powershell() -> str | None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(executable) if executable.is_file() else shutil.which("powershell.exe")


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *arguments],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_candidate_cleanup_requires_locked_preview_and_supports_restore(tmp_path: Path):
    data = tmp_path / "data"
    program = tmp_path / "program"
    backup = tmp_path / "backup"
    candidate = data / "dependency-runs" / "candidate-123"
    for path in (candidate, program, backup):
        path.mkdir(parents=True)
    (candidate / "payload.bin").write_bytes(b"payload")
    manifest = tmp_path / "manifest.json"
    common = ["-CandidateId", "123", "-DataRoot", str(data), "-ProgramRoot", str(program), "-BackupRoot", str(backup), "-ManifestPath", str(manifest), "-OperationId", "900"]

    preview = _run("-Mode", "Preview", *common)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(manifest.read_text(encoding="utf-8"))["candidate_id"] == "123"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()

    quarantine = _run("-Mode", "Quarantine", *common, "-ExpectedManifestSha256", digest, "-Confirm:$false")
    assert quarantine.returncode == 0, quarantine.stderr
    assert not candidate.exists()
    assert (data / "dependency-runs" / ".cleanup-quarantine-candidate-123-900").is_dir()

    restore = _run("-Mode", "Restore", *common, "-Confirm:$false")
    assert restore.returncode == 0, restore.stderr
    assert candidate.is_dir()


def test_candidate_cleanup_rejects_active_candidate(tmp_path: Path):
    data = tmp_path / "data"
    program = tmp_path / "program"
    backup = tmp_path / "backup"
    candidate = data / "dependency-runs" / "candidate-123"
    for path in (candidate, program, backup, data / "release-state"):
        path.mkdir(parents=True, exist_ok=True)
    (data / "release-state" / "active.json").write_text('{"candidate_id":"123"}', encoding="utf-8")
    result = _run("-Mode", "Preview", "-CandidateId", "123", "-DataRoot", str(data), "-ProgramRoot", str(program), "-BackupRoot", str(backup), "-ManifestPath", str(tmp_path / "manifest.json"), "-OperationId", "900")
    assert result.returncode != 0
    assert candidate.is_dir()


def test_candidate_cleanup_workflow_is_single_candidate_and_hash_locked():
    workflow = (ROOT / ".github" / "workflows" / "cleanup-asr-candidate-dependency.yml").read_text(encoding="utf-8")
    assert "group: production-gpu-exclusive" in workflow
    assert "preview_run_id" in workflow
    assert "manifest_sha256" in workflow
    assert "-Mode Quarantine" in workflow
    assert "-Mode Restore" in workflow
    assert "-Mode Finalize" in workflow
