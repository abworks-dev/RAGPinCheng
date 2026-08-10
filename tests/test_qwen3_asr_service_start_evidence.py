from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_qwen3_asr_service_start_evidence as evidence


def _source(tmp_path: Path, *, stdout: str = "", stderr: str = "") -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    verdict = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "temporary_service_failed",
        "commit_sha": evidence.SOURCE_COMMIT_SHA,
        "run_id": evidence.SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    (tmp_path / "reports" / "qualification-verdict.json").write_text(
        json.dumps(verdict), encoding="utf-8"
    )
    if stdout:
        (tmp_path / "logs" / "qualification-service.stdout.log").write_text(
            stdout, encoding="utf-8"
        )
    if stderr:
        (tmp_path / "logs" / "qualification-service.stderr.log").write_text(
            stderr, encoding="utf-8"
        )
    return tmp_path


def test_extracts_safe_service_failure_categories(tmp_path):
    secret = "https://user:token@example.invalid/private"
    root = _source(
        tmp_path,
        stderr=(
            "Traceback (most recent call last):\n"
            f"private path {secret}\n"
            "ModuleNotFoundError: No module named private_module"
        ),
    )
    result = evidence.extract_evidence(source_root=root)
    serialized = json.dumps(result, sort_keys=True)
    assert result["status"] == "evidence_complete"
    assert result["signals"] == ["module_import_failure", "traceback"]
    assert result["exception_types"] == ["ModuleNotFoundError"]
    assert secret not in serialized
    assert "private_module" not in serialized


def test_missing_logs_are_reported_without_failure(tmp_path):
    result = evidence.extract_evidence(source_root=_source(tmp_path))
    assert result["status"] == "evidence_incomplete"
    assert result["stdout_line_count"] == 0
    assert result["stderr_line_count"] == 0


def test_rejects_wrong_source_commit(tmp_path):
    root = _source(tmp_path, stderr="ValueError: private")
    path = root / "reports" / "qualification-verdict.json"
    verdict = json.loads(path.read_text(encoding="utf-8"))
    verdict["commit_sha"] = "0" * 40
    path.write_text(json.dumps(verdict), encoding="utf-8")
    with pytest.raises(evidence.EvidenceError, match="commit_sha"):
        evidence.extract_evidence(source_root=root)
