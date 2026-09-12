"""The WhisperX qualification must measure how much speech it actually transcribed."""
from __future__ import annotations

from scripts import run_whisperx_qualification as qualification


def _report() -> dict[str, object]:
    return {
        "status": "pass",
        "gates": {"bim_term_recall": {"observed": 1.0, "threshold": 0.9, "pass": True}},
        "thresholds": {"bim_term_recall_min": 0.9},
        "samples": [],
    }


def _observe(sample_id: str, reference_chars: int, canonical_chars: int, runs: int = 2) -> None:
    qualification._COVERAGE_OBSERVATIONS[sample_id] = [
        {
            "reference_chars": reference_chars,
            "raw_chars": canonical_chars,
            "canonical_chars": canonical_chars,
            "duration_ms": 120_000,
            "negative_control": False,
        }
        for _ in range(runs)
    ]


def _clear() -> None:
    qualification._COVERAGE_OBSERVATIONS.clear()


def test_collapsed_decoding_fails_the_content_coverage_gate() -> None:
    _clear()
    _observe("clear-zh-01", reference_chars=400, canonical_chars=60)
    report = _report()

    qualification._attach_content_coverage(report)

    gate = report["gates"]["content_coverage"]
    assert gate["pass"] is False
    assert gate["observed"] == 0.15
    assert report["status"] == "fail"
    assert report["content_coverage"]["min_ratio"] == 0.15
    _clear()


def test_complete_decoding_keeps_the_verdict_and_reports_the_ratio() -> None:
    _clear()
    _observe("clear-zh-01", reference_chars=400, canonical_chars=360)
    _observe("standard-codes-01", reference_chars=200, canonical_chars=150)
    report = _report()

    qualification._attach_content_coverage(report)

    gate = report["gates"]["content_coverage"]
    assert gate["pass"] is True
    assert gate["observed"] == 0.75
    assert report["status"] == "pass"
    assert report["thresholds"]["content_coverage_min"] == qualification.CONTENT_COVERAGE_MIN_RATIO
    assert [row["sample_id"] for row in report["content_coverage"]["samples"]] == [
        "clear-zh-01",
        "standard-codes-01",
    ]
    _clear()


def test_coverage_gate_only_downgrades_an_existing_verdict() -> None:
    _clear()
    _observe("clear-zh-01", reference_chars=400, canonical_chars=360)
    report = _report()
    report["status"] = "fail"

    qualification._attach_content_coverage(report)

    assert report["gates"]["content_coverage"]["pass"] is True
    assert report["status"] == "fail"
    _clear()


def test_worst_repetition_decides_the_sample_ratio() -> None:
    _clear()
    qualification._COVERAGE_OBSERVATIONS["clear-zh-01"] = [
        {
            "reference_chars": 400,
            "raw_chars": 380,
            "canonical_chars": 380,
            "duration_ms": 120_000,
            "negative_control": False,
        },
        {
            "reference_chars": 400,
            "raw_chars": 40,
            "canonical_chars": 40,
            "duration_ms": 120_000,
            "negative_control": False,
        },
    ]
    report = _report()

    qualification._attach_content_coverage(report)

    assert report["content_coverage"]["min_ratio"] == 0.1
    assert report["gates"]["content_coverage"]["pass"] is False
    _clear()


def test_negative_controls_are_not_measured_for_coverage() -> None:
    _clear()
    qualification._COVERAGE_OBSERVATIONS["negative-01"] = [
        {
            "reference_chars": 120,
            "raw_chars": 0,
            "canonical_chars": 0,
            "duration_ms": 60_000,
            "negative_control": True,
        }
    ]
    report = _report()

    qualification._attach_content_coverage(report)

    assert report["content_coverage"]["samples"] == []
    assert report["content_coverage"]["min_ratio"] is None
    assert report["gates"]["content_coverage"]["pass"] is False
    _clear()
