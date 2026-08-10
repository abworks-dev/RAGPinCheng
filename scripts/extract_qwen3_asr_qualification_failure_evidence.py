from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SOURCE_RUN_ID = "31356827072"
SOURCE_COMMIT_SHA = "1d8154e620acb31e1428dd9abe69a5c97e53a16a"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000
MAX_PROGRESS_EVENTS = 32
MAX_ERROR_SUMMARIES = 5
MAX_ERROR_SUMMARY_CHARS = 500

PROGRESS_EVENTS = frozenset({"warmup-start", "warmup-complete", "sample-complete"})
SIGNALS = {
    "module_import_failure": re.compile(
        r"modulenotfounderror|importerror|cannot import name", re.IGNORECASE
    ),
    "dependency_api_mismatch": re.compile(
        r"unexpected keyword argument|has no attribute|attributeerror", re.IGNORECASE
    ),
    "native_library_failure": re.compile(
        r"dll load failed|could not find module|winerror 126|winerror 193",
        re.IGNORECASE,
    ),
    "cuda_out_of_memory": re.compile(r"cuda out of memory|outofmemoryerror", re.IGNORECASE),
    "request_failure": re.compile(
        r"connecterror|connecttimeout|readtimeout|httpstatuserror|provider failure",
        re.IGNORECASE,
    ),
    "traceback": re.compile(r"traceback \(most recent call last\)", re.IGNORECASE),
}
EXCEPTION_LINE_RE = re.compile(
    r"^\s*((?:[A-Za-z_][A-Za-z0-9_]*\.)*"
    r"[A-Za-z][A-Za-z0-9_]{0,127}(?:Error|Exception|Timeout))\s*:\s*(.*?)\s*$",
    re.MULTILINE,
)
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
QUOTED_WINDOWS_PATH_RE = re.compile(
    r"(['\"])(?:[A-Za-z]:[\\/]|\\\\).*?\1", re.IGNORECASE
)
WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s,;]+", re.IGNORECASE)
IP_ADDRESS_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(token|secret|password|authorization|proxy)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
SAMPLE_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
GATE_NAMES = (
    "processing_failure_rate",
    "bim_term_recall",
    "standard_code_recall",
    "timestamp_p95_ms",
    "negative_false_positives",
)
SAMPLE_FIELDS = (
    "sample_id",
    "scenario",
    "negative_control",
    "rtf",
    "rtf_pass",
    "deterministic",
    "pass",
    "cer",
    "cer_limit",
    "cer_pass",
    "term_hits",
    "term_total",
    "code_hits",
    "code_total",
    "timestamp_drift_max_ms",
    "forbidden_term_hits",
    "forbidden_code_hits",
)


class EvidenceError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read_optional_file(
    path: Path, *, root: Path, expected_parent: str
) -> tuple[str, int, bool]:
    resolved_root = root.resolve(strict=True)
    parent = (resolved_root / expected_parent).resolve(strict=True)
    if path.resolve(strict=False).parent != parent:
        raise EvidenceError("source file escaped fixed run root")
    if not path.exists():
        return "", 0, False
    resolved = path.resolve(strict=True)
    if resolved.parent != parent:
        raise EvidenceError("source file escaped fixed run root")
    if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
        raise EvidenceError("source file must be a regular non-reparse file")
    size_bytes = path.stat().st_size
    if size_bytes > MAX_FILE_BYTES:
        raise EvidenceError("source file exceeds the fixed size boundary")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvidenceError("source file must be UTF-8") from exc
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise EvidenceError("source file exceeds the fixed line boundary")
    return text, len(lines), True


def _load_verdict(path: Path, *, root: Path) -> dict[str, Any]:
    text, _, exists = _read_optional_file(
        path, root=root, expected_parent="reports"
    )
    if not exists:
        raise EvidenceError("source verdict is missing")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError("source verdict is invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceError("source verdict must be an object")
    expected = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "qualification_failed",
        "commit_sha": SOURCE_COMMIT_SHA,
        "run_id": SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise EvidenceError(f"source verdict mismatch: {key}")
    return value


def _sanitize_error_summary(message: str) -> str:
    value = ANSI_ESCAPE_RE.sub("", message)
    value = URL_RE.sub("<url>", value)
    value = QUOTED_WINDOWS_PATH_RE.sub("<path>", value)
    value = WINDOWS_PATH_RE.sub("<path>", value)
    value = IP_ADDRESS_RE.sub("<address>", value)
    value = SENSITIVE_ASSIGNMENT_RE.sub(r"\1=<redacted>", value)
    value = BEARER_RE.sub("Bearer <redacted>", value)
    value = " ".join(value.split())
    if len(value) > MAX_ERROR_SUMMARY_CHARS:
        value = value[:MAX_ERROR_SUMMARY_CHARS].rstrip() + "..."
    return value


def _extract_errors(sources: list[tuple[str, str]]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, text in sources:
        for match in EXCEPTION_LINE_RE.finditer(text):
            exception_type = match.group(1)
            summary = _sanitize_error_summary(match.group(2))
            if not summary:
                continue
            identity = (source, exception_type, summary)
            if identity in seen:
                continue
            seen.add(identity)
            summaries.append(
                {
                    "source": source,
                    "exception_type": exception_type,
                    "summary": summary,
                }
            )
    return summaries[-MAX_ERROR_SUMMARIES:]


def _extract_progress(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if type(value) is not dict or value.get("event") not in PROGRESS_EVENTS:
            continue
        event = value["event"]
        sample_id = value.get("sample_id")
        if type(sample_id) is not str or SAMPLE_ID_RE.fullmatch(sample_id) is None:
            continue
        item: dict[str, object] = {"event": event, "sample_id": sample_id}
        if event == "sample-complete":
            sample_index = value.get("sample_index")
            passed = value.get("passed")
            if (
                type(sample_index) is not int
                or isinstance(sample_index, bool)
                or not 1 <= sample_index <= 8
                or type(passed) is not bool
            ):
                continue
            item.update({"sample_index": sample_index, "passed": passed})
        events.append(item)
    return events[-MAX_PROGRESS_EVENTS:]


def _is_metric(value: object) -> bool:
    return type(value) in {int, float} and not isinstance(value, bool)


def _extract_quality_summary(text: str, *, exists: bool) -> dict[str, object] | None:
    if not exists:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError("qualification summary is invalid") from exc
    if type(value) is not dict:
        raise EvidenceError("qualification summary must be an object")
    if (
        value.get("schema_version") != "qwen3-asr-qualification-report/1"
        or value.get("status") not in {"pass", "fail"}
        or value.get("sample_count") != 8
        or type(value.get("gates")) is not dict
        or type(value.get("samples")) is not list
        or len(value["samples"]) != 8
    ):
        raise EvidenceError("qualification summary contract mismatch")

    gates: list[dict[str, object]] = []
    for name in GATE_NAMES:
        item = value["gates"].get(name)
        if (
            type(item) is not dict
            or not _is_metric(item.get("observed"))
            or not _is_metric(item.get("threshold"))
            or type(item.get("pass")) is not bool
        ):
            raise EvidenceError(f"qualification gate is invalid: {name}")
        gates.append(
            {
                "name": name,
                "observed": item["observed"],
                "threshold": item["threshold"],
                "pass": item["pass"],
            }
        )

    samples: list[dict[str, object]] = []
    for item in value["samples"]:
        if type(item) is not dict:
            raise EvidenceError("qualification sample result must be an object")
        sample_id = item.get("sample_id")
        scenario = item.get("scenario")
        if (
            type(sample_id) is not str
            or SAMPLE_ID_RE.fullmatch(sample_id) is None
            or type(scenario) is not str
            or SAMPLE_ID_RE.fullmatch(scenario) is None
        ):
            raise EvidenceError("qualification sample identity is invalid")
        filtered: dict[str, object] = {}
        for field in SAMPLE_FIELDS:
            if field not in item:
                continue
            field_value = item[field]
            if field in {"sample_id", "scenario"}:
                filtered[field] = field_value
            elif field in {
                "negative_control",
                "rtf_pass",
                "deterministic",
                "pass",
                "cer_pass",
            }:
                if type(field_value) is not bool:
                    raise EvidenceError(f"qualification sample field is invalid: {field}")
                filtered[field] = field_value
            elif _is_metric(field_value):
                filtered[field] = field_value
            else:
                raise EvidenceError(f"qualification sample field is invalid: {field}")
        for required in (
            "sample_id",
            "scenario",
            "negative_control",
            "rtf",
            "rtf_pass",
            "deterministic",
            "pass",
        ):
            if required not in filtered:
                raise EvidenceError(f"qualification sample field is missing: {required}")
        samples.append(filtered)
    return {"status": value["status"], "gates": gates, "samples": samples}


def extract_evidence(*, source_root: Path) -> dict[str, object]:
    _load_verdict(
        source_root / "reports" / "qualification-verdict.json", root=source_root
    )
    runner_stdout, runner_stdout_lines, _ = _read_optional_file(
        source_root / "logs" / "qualification-runner.stdout.log",
        root=source_root,
        expected_parent="logs",
    )
    runner_stderr, runner_stderr_lines, _ = _read_optional_file(
        source_root / "logs" / "qualification-runner.stderr.log",
        root=source_root,
        expected_parent="logs",
    )
    service_stderr, service_stderr_lines, _ = _read_optional_file(
        source_root / "logs" / "qualification-service.stderr.log",
        root=source_root,
        expected_parent="logs",
    )
    summary_text, _, summary_exists = _read_optional_file(
        source_root / "reports" / "qualification-summary.json",
        root=source_root,
        expected_parent="reports",
    )
    _, _, sample_results_exists = _read_optional_file(
        source_root / "reports" / "sample-results.json",
        root=source_root,
        expected_parent="reports",
    )
    progress_events = _extract_progress(runner_stdout)
    errors = _extract_errors(
        [
            ("qualification-runner.stderr", runner_stderr),
            ("qualification-service.stderr", service_stderr),
        ]
    )
    diagnostic_text = f"{runner_stderr}\n{service_stderr}"
    signals = sorted(
        name for name, pattern in SIGNALS.items() if pattern.search(diagnostic_text)
    )
    quality_summary = _extract_quality_summary(summary_text, exists=summary_exists)
    if summary_exists or sample_results_exists:
        last_stage = "report-written"
    elif progress_events:
        last_stage = str(progress_events[-1]["event"])
    else:
        last_stage = "runner-started"
    return {
        "schema_version": "qwen3-asr-qualification-failure-evidence/1",
        "status": (
            "evidence_complete"
            if errors or signals or progress_events or quality_summary
            else "evidence_incomplete"
        ),
        "source_run_id": SOURCE_RUN_ID,
        "source_commit_sha": SOURCE_COMMIT_SHA,
        "failure_code": "qualification_failed",
        "last_stage": last_stage,
        "progress_events": progress_events,
        "signals": signals,
        "error_summaries": errors,
        "quality_summary": quality_summary,
        "qualification_summary_exists": summary_exists,
        "sample_results_exists": sample_results_exists,
        "runner_stdout_line_count": runner_stdout_lines,
        "runner_stderr_line_count": runner_stderr_lines,
        "service_stderr_line_count": service_stderr_lines,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    qualification_root = os.environ.get("PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT")
    if not qualification_root:
        raise EvidenceError("qualification root is required")
    root = Path(qualification_root)
    if not root.is_absolute():
        raise EvidenceError("qualification root must be absolute")
    result = extract_evidence(source_root=root / "runs" / SOURCE_RUN_ID)
    parent = args.output.parent.resolve(strict=True)
    output = (parent / args.output.name).resolve()
    if output.parent != parent:
        raise EvidenceError("output escaped fixed parent")
    output.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
