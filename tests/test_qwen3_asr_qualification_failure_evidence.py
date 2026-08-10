from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_qwen3_asr_qualification_failure_evidence as evidence


ROOT = Path(__file__).resolve().parents[1]


def _source(
    tmp_path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    service_stderr: str = "",
    reports: bool = False,
) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    verdict = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "qualification_failed",
        "commit_sha": evidence.SOURCE_COMMIT_SHA,
        "run_id": evidence.SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    (tmp_path / "reports" / "qualification-verdict.json").write_text(
        json.dumps(verdict), encoding="utf-8"
    )
    files = {
        "qualification-runner.stdout.log": stdout,
        "qualification-runner.stderr.log": stderr,
        "qualification-service.stderr.log": service_stderr,
    }
    for name, value in files.items():
        if value:
            (tmp_path / "logs" / name).write_text(value, encoding="utf-8")
    if reports:
        (tmp_path / "reports" / "qualification-summary.json").write_text(
            "{}", encoding="utf-8"
        )
        (tmp_path / "reports" / "sample-results.json").write_text(
            "{}", encoding="utf-8"
        )
    return tmp_path


def test_extracts_progress_and_sanitized_final_error(tmp_path):
    secret_url = "https://user:token@example.invalid/private"
    stdout = "\n".join(
        [
            json.dumps({"event": "warmup-start", "sample_id": "clear-zh-01"}),
            "model output that must not be copied",
        ]
    )
    stderr = (
        "Traceback (most recent call last):\n"
        "  private transcript line\n"
        f"httpx.ReadTimeout: request to {secret_url} at 192.168.1.20:18300 "
        "token=do-not-emit\n"
    )
    result = evidence.extract_evidence(
        source_root=_source(tmp_path, stdout=stdout, stderr=stderr)
    )
    assert result["status"] == "evidence_complete"
    assert result["last_stage"] == "warmup-start"
    assert result["progress_events"] == [
        {"event": "warmup-start", "sample_id": "clear-zh-01"}
    ]
    assert result["signals"] == ["request_failure", "traceback"]
    assert result["error_summaries"] == [
        {
            "source": "qualification-runner.stderr",
            "exception_type": "httpx.ReadTimeout",
            "summary": "request to <url> at <address> token=<redacted>",
        }
    ]
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "model output",
        "private transcript",
        "user:token",
        "192.168.1.20",
        "do-not-emit",
    ):
        assert forbidden not in serialized


def test_reports_completed_sample_without_copying_extra_fields(tmp_path):
    stdout = json.dumps(
        {
            "event": "sample-complete",
            "sample_id": "bim-terms-01",
            "sample_index": 2,
            "passed": False,
            "hypothesis": "must not be emitted",
        }
    )
    result = evidence.extract_evidence(
        source_root=_source(tmp_path, stdout=stdout, reports=True)
    )
    assert result["last_stage"] == "report-written"
    assert result["progress_events"] == [
        {
            "event": "sample-complete",
            "sample_id": "bim-terms-01",
            "sample_index": 2,
            "passed": False,
        }
    ]
    assert result["qualification_summary_exists"] is True
    assert result["sample_results_exists"] is True
    assert "hypothesis" not in json.dumps(result)


def test_missing_runner_details_are_incomplete_but_bounded(tmp_path):
    result = evidence.extract_evidence(source_root=_source(tmp_path))
    assert result["status"] == "evidence_incomplete"
    assert result["last_stage"] == "runner-started"
    assert result["progress_events"] == []
    assert result["error_summaries"] == []


def test_rejects_wrong_source_run(tmp_path):
    root = _source(tmp_path)
    path = root / "reports" / "qualification-verdict.json"
    verdict = json.loads(path.read_text(encoding="utf-8"))
    verdict["run_id"] = "0"
    path.write_text(json.dumps(verdict), encoding="utf-8")
    with pytest.raises(evidence.EvidenceError, match="run_id"):
        evidence.extract_evidence(source_root=root)


def test_workflow_is_manual_immutable_and_uploads_only_sanitized_json():
    workflow = (
        ROOT
        / ".github"
        / "workflows"
        / "diagnose-qwen3-asr-qualification-failure-production.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "commit_sha must equal the workflow dispatch revision" in workflow
    assert '"refs/heads/master"' in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "qualification-failure-evidence.json" in workflow
    assert "qualification-runner.stdout.log" not in workflow
    assert "qualification-runner.stderr.log" not in workflow
