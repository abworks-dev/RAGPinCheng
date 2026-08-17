from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inventory-production-storage.ps1"


def _powershell() -> str | None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ps51 = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(ps51) if ps51.is_file() else shutil.which("powershell.exe")


def test_inventory_reports_only_aggregate_metadata(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "RAGPinCheng-ASR"
    candidate = data_root / "dependency-runs" / "candidate-secret-name"
    candidate.mkdir(parents=True)
    secret_file_name = "customer-secret-document.txt"
    (candidate / secret_file_name).write_bytes(b"content-not-for-report")
    report_path = tmp_path / "inventory.json"

    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ReportPath",
            str(report_path),
            "-AsrDataRoot",
            str(data_root),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    raw = report_path.read_text(encoding="utf-8-sig")
    report = json.loads(raw)
    assert secret_file_name not in raw
    assert "content-not-for-report" not in raw
    assert report["roots"]["asr_data"]["bytes"] == len(b"content-not-for-report")
    assert report["dependency_runs"]["candidate"]["directories"] == 1
    assert report["dependency_runs"]["candidate"]["bytes"] == len(b"content-not-for-report")
    assert report["breakdowns"]["asr_data"]["categories"]["dependency_runs"]["bytes"] == len(
        b"content-not-for-report"
    )
    assert "candidate-secret-name" not in raw
    assert report["policy"]["advisory_only"] is True
    assert report["candidates"][0]["advisory_status"] == "protected"


def test_inventory_classifies_candidate_release_and_active_references(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "data"
    program_root = tmp_path / "program"
    backup_root = data_root / "backups"
    dependency_root = data_root / "dependency-runs"
    for candidate_id in ("101", "102", "broken", "103"):
        (dependency_root / f"candidate-{candidate_id}").mkdir(parents=True)
        (dependency_root / f"candidate-{candidate_id}" / "wheelhouse.bin").write_bytes(b"x")
    (dependency_root / "candidate-103" / "run.lock").write_text("active", encoding="ascii")
    (program_root / "releases" / "101").mkdir(parents=True)
    (data_root / "config" / "releases" / "101").mkdir(parents=True)
    (program_root / "releases" / "101" / "release-manifest.json").write_text(
        '{"candidate_id":"101"}', encoding="utf-8"
    )
    (data_root / "release-state").mkdir(parents=True)
    (data_root / "release-state" / "active.json").write_text(
        '{"candidate_id":"101"}', encoding="utf-8"
    )
    activation = backup_root / "900"
    activation.mkdir(parents=True)
    (activation / "candidate-activation-state.json").write_text(
        '{"candidate_id":"102","previous_candidate_id":"101"}', encoding="utf-8"
    )
    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-ReportPath", str(report_path),
            "-AsrDataRoot", str(data_root), "-AsrProgramRoot", str(program_root),
            "-BackupDirectory", str(backup_root),
        ], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    candidates = {item["candidate_id"]: item for item in report["candidates"]}
    assert candidates["101"]["status"] == "identity-conflict"
    assert "active-release-state" in candidates["101"]["reasons"]
    assert candidates["102"]["status"] == "rollback-referenced"
    assert candidates["103"]["status"] == "active-marker"
    assert candidates["broken"]["status"] == "unknown-name"
    activation_audit = report["activation_audit"]
    assert activation_audit["references"][0]["activation_id"] == "900"
    assert activation_audit["references"][0]["candidate_ids"] == ["102", "101"]


def test_inventory_reports_other_entries_and_gpu_advisory_details(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "asr-data"
    runtime_root = tmp_path / "runtime"
    (data_root / "unclassified-output").mkdir(parents=True)
    (data_root / "unclassified-output" / "payload.bin").write_bytes(b"abc")
    invalid_release = runtime_root / "releases" / "invalid-release"
    invalid_release.mkdir(parents=True)
    (invalid_release / "runtime-manifest.json").write_text(
        '{"release_id":"different","qualification_status":"pending"}', encoding="utf-8"
    )
    (runtime_root / "qualification" / "101").mkdir(parents=True)
    (runtime_root / "resolver" / "pip-cache").mkdir(parents=True)
    (runtime_root / "resolver" / "pip-cache" / "cached.whl").write_bytes(b"cache")
    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-ReportPath", str(report_path),
            "-AsrDataRoot", str(data_root), "-RuntimeRoot", str(runtime_root),
        ], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    other = report["breakdowns"]["asr_data"]["other_entries"]
    assert other[0]["name"] == "unclassified-output"
    assert other[0]["bytes"] == 3
    release = report["gpu_runtime_inventory"]["releases"][0]
    assert release["identity"] == "invalid-release-contract"
    assert "failed-release_id_matches" in release["advisory_reasons"]
    assert release["advisory_status"] == "protected"
    resolver_cache = report["gpu_runtime_inventory"]["caches"]["resolver-pip-cache"]
    assert resolver_cache["bytes"] == len(b"cache")
    assert resolver_cache["advisory_status"] == "protected-inventory-only"
    assert report["gpu_runtime_inventory"]["resolver"] == []
    assert report["gpu_runtime_inventory"]["reference_inventory_status"] in {
        "measured", "unavailable-protect-all"
    }


def test_inventory_workflow_uses_asr_activation_backup_root():
    workflow = (ROOT / ".github" / "workflows" / "inventory-production-storage.yml").read_text(encoding="utf-8")
    assert "PRODUCTION_ASR_BACKUP_ROOT" in workflow
    assert "-AsrActivationBackupRoot" in workflow
