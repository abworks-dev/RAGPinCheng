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
