from __future__ import annotations

import json

import pytest

from scripts import summarize_qwen3_asr_performance as performance


def _timing(elapsed_ms: int, *, outcome: str = "success") -> str:
    return performance.TIMING_PREFIX + json.dumps(
        {
            "schema_version": performance.ENGINE_SCHEMA_VERSION,
            "language_policy": performance.CANDIDATE_ID,
            "outcome": outcome,
            "elapsed_ms": elapsed_ms,
        },
        sort_keys=True,
    )


def _report() -> dict[str, object]:
    return {
        "schema_version": "qwen3-asr-qualification-report/2",
        "candidate_id": performance.CANDIDATE_ID,
        "sample_count": 8,
        "samples": [
            {
                "sample_id": f"sample-{index + 1}",
                "rtf": (index + 1) / 10,
                "hypothesis": "must not be emitted",
            }
            for index in range(8)
        ],
    }


def test_summarizes_fixed_numeric_diagnostics_without_copying_text(tmp_path):
    service_log = tmp_path / "service.log"
    service_log.write_text(
        "ignored private log line\n"
        + "\n".join(_timing(index * 10) for index in range(1, 18)),
        encoding="utf-8",
    )
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")

    result = performance.summarize(
        service_log=service_log, qualification_summary=report
    )
    assert result["engine_call_count"] == 17
    assert result["engine_success_count"] == 17
    assert result["engine_error_count"] == 0
    assert result["engine_elapsed_ms"] == {
        "min": 10.0,
        "max": 170.0,
        "mean": 90.0,
        "p95": 170.0,
    }
    assert result["end_to_end_rtf"] == {
        "min": 0.1,
        "max": 0.8,
        "mean": 0.45,
        "p95": 0.8,
    }
    serialized = json.dumps(result)
    assert "private log" not in serialized
    assert "hypothesis" not in serialized


def test_partial_failure_without_report_remains_diagnostic(tmp_path):
    service_log = tmp_path / "service.log"
    service_log.write_text(_timing(25, outcome="error"), encoding="utf-8")
    result = performance.summarize(
        service_log=service_log,
        qualification_summary=tmp_path / "missing.json",
    )
    assert result["engine_call_count"] == 1
    assert result["engine_error_count"] == 1
    assert result["qualification_summary_exists"] is False
    assert result["end_to_end_rtf"] is None


def test_failure_before_first_engine_call_remains_diagnostic(tmp_path):
    service_log = tmp_path / "service.log"
    service_log.write_text("service failed before inference\n", encoding="utf-8")
    result = performance.summarize(
        service_log=service_log,
        qualification_summary=tmp_path / "missing.json",
    )
    assert result["engine_call_count"] == 0
    assert result["engine_elapsed_ms"] is None
    assert result["qualification_summary_exists"] is False


def test_complete_report_requires_all_seventeen_engine_calls(tmp_path):
    service_log = tmp_path / "service.log"
    service_log.write_text(_timing(25), encoding="utf-8")
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    with pytest.raises(performance.PerformanceDiagnosticError, match="17 engine"):
        performance.summarize(
            service_log=service_log, qualification_summary=report
        )


def test_rejects_unapproved_language_policy_record(tmp_path):
    service_log = tmp_path / "service.log"
    service_log.write_text(
        _timing(25).replace(performance.CANDIDATE_ID, "unrestricted"),
        encoding="utf-8",
    )
    with pytest.raises(performance.PerformanceDiagnosticError, match="contract"):
        performance.summarize(service_log=service_log, qualification_summary=None)
