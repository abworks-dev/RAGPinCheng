from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


TIMING_PREFIX = "QWEN3_ENGINE_TIMING "
SCHEMA_VERSION = "qwen3-asr-performance-diagnostic/1"
ENGINE_SCHEMA_VERSION = "qwen3-asr-engine-timing/1"
CANDIDATE_ID = "auto-zh-en"
EXPECTED_ENGINE_CALLS = 17
EXPECTED_SAMPLE_COUNT = 8
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LOG_LINES = 10_000


class PerformanceDiagnosticError(RuntimeError):
    pass


def _metric(value: object, label: str) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise PerformanceDiagnosticError(f"{label} must be numeric")
    metric = float(value)
    if not math.isfinite(metric) or metric < 0:
        raise PerformanceDiagnosticError(f"{label} must be finite and nonnegative")
    return metric


def _summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise PerformanceDiagnosticError("timing observations are empty")
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
        "mean": round(sum(ordered) / len(ordered), 6),
        "p95": round(p95, 6),
    }


def _load_log(path: Path) -> list[dict[str, object]]:
    if not path.is_file() or path.is_symlink():
        raise PerformanceDiagnosticError("service log must be a regular file")
    if path.stat().st_size > MAX_LOG_BYTES:
        raise PerformanceDiagnosticError("service log exceeds size boundary")
    try:
        lines = path.read_bytes().decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise PerformanceDiagnosticError("service log must be UTF-8") from exc
    if len(lines) > MAX_LOG_LINES:
        raise PerformanceDiagnosticError("service log exceeds line boundary")
    records: list[dict[str, object]] = []
    for line in lines:
        if not line.startswith(TIMING_PREFIX):
            continue
        try:
            value = json.loads(line[len(TIMING_PREFIX) :])
        except json.JSONDecodeError as exc:
            raise PerformanceDiagnosticError("engine timing record is invalid") from exc
        if (
            type(value) is not dict
            or set(value)
            != {"schema_version", "language_policy", "outcome", "elapsed_ms"}
            or value["schema_version"] != ENGINE_SCHEMA_VERSION
            or value["language_policy"] != CANDIDATE_ID
            or value["outcome"] not in {"success", "error"}
        ):
            raise PerformanceDiagnosticError("engine timing contract mismatch")
        _metric(value["elapsed_ms"], "elapsed_ms")
        records.append(value)
    return records


def _load_qualification(path: Path | None) -> tuple[bool, list[float]]:
    if path is None or not path.exists():
        return False, []
    if not path.is_file() or path.is_symlink():
        raise PerformanceDiagnosticError("qualification summary must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PerformanceDiagnosticError("qualification summary is invalid") from exc
    if (
        type(value) is not dict
        or value.get("schema_version") != "qwen3-asr-qualification-report/2"
        or value.get("candidate_id") != CANDIDATE_ID
        or value.get("sample_count") != EXPECTED_SAMPLE_COUNT
        or type(value.get("samples")) is not list
        or len(value["samples"]) != EXPECTED_SAMPLE_COUNT
    ):
        raise PerformanceDiagnosticError("qualification summary contract mismatch")
    rtfs: list[float] = []
    for sample in value["samples"]:
        if type(sample) is not dict:
            raise PerformanceDiagnosticError("qualification sample must be an object")
        rtfs.append(_metric(sample.get("rtf"), "sample rtf"))
    return True, rtfs


def summarize(
    *, service_log: Path, qualification_summary: Path | None
) -> dict[str, object]:
    records = _load_log(service_log)
    report_exists, rtfs = _load_qualification(qualification_summary)
    if report_exists and len(records) != EXPECTED_ENGINE_CALLS:
        raise PerformanceDiagnosticError("complete report requires exactly 17 engine calls")
    elapsed = [_metric(item["elapsed_ms"], "elapsed_ms") for item in records]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "language_policy": CANDIDATE_ID,
        "engine_call_count": len(records),
        "engine_success_count": sum(item["outcome"] == "success" for item in records),
        "engine_error_count": sum(item["outcome"] == "error" for item in records),
        "engine_elapsed_ms": _summary(elapsed) if elapsed else None,
        "qualification_summary_exists": report_exists,
        "sample_count": len(rtfs),
        "end_to_end_rtf": _summary(rtfs) if rtfs else None,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-log", required=True, type=Path)
    parser.add_argument("--qualification-summary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = summarize(
        service_log=args.service_log,
        qualification_summary=args.qualification_summary,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "complete"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
