from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SOURCE_RUN_ID = "31350405787"
SOURCE_COMMIT_SHA = "5d79bb0388614eae85f5eadb6669bdde5234f7c1"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000

SIGNALS = {
    "module_import_failure": re.compile(
        r"modulenotfounderror|importerror|cannot import name", re.IGNORECASE
    ),
    "dependency_api_mismatch": re.compile(
        r"unexpected keyword argument|has no attribute|attributeerror", re.IGNORECASE
    ),
    "native_library_failure": re.compile(
        r"dll load failed|could not find module|winerror 126|winerror 193", re.IGNORECASE
    ),
    "model_cache_failure": re.compile(
        r"model.*(?:cache|manifest).*(?:invalid|missing|failed)|offline.*model",
        re.IGNORECASE,
    ),
    "cuda_out_of_memory": re.compile(r"cuda out of memory|outofmemoryerror", re.IGNORECASE),
    "address_in_use": re.compile(r"address already in use|winerror 10048", re.IGNORECASE),
    "traceback": re.compile(r"traceback \(most recent call last\)", re.IGNORECASE),
}

EXCEPTION_TYPE_RE = re.compile(
    r"(?:^|\s)([A-Za-z][A-Za-z0-9_]{0,127}(?:Error|Exception))(?=\s*:)",
    re.MULTILINE,
)
EXCEPTION_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]{0,127}(?:Error|Exception))\s*:\s*(.+?)\s*$",
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
MAX_ERROR_SUMMARY_CHARS = 500
SUMMARY_EXCEPTION_TYPES = frozenset({"RuntimeError"})


class EvidenceError(RuntimeError):
    pass


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


def _extract_error_summaries(text: str) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in EXCEPTION_LINE_RE.finditer(text):
        exception_type = match.group(1)
        if exception_type not in SUMMARY_EXCEPTION_TYPES:
            continue
        summary = _sanitize_error_summary(match.group(2))
        if not summary:
            continue
        identity = (exception_type, summary)
        if identity in seen:
            continue
        seen.add(identity)
        summaries.append(
            {"exception_type": exception_type, "summary": summary}
        )
    return summaries


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read_optional_log(path: Path, *, root: Path) -> tuple[str, int]:
    resolved_root = root.resolve(strict=True)
    logs_root = (resolved_root / "logs").resolve(strict=True)
    if path.resolve(strict=False).parent != logs_root:
        raise EvidenceError("source log escaped fixed run root")
    if not path.exists():
        return "", 0
    resolved = path.resolve(strict=True)
    if resolved.parent != logs_root:
        raise EvidenceError("source log escaped fixed run root")
    if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
        raise EvidenceError("source log must be a regular non-reparse file")
    size_bytes = path.stat().st_size
    if size_bytes > MAX_FILE_BYTES:
        raise EvidenceError("source log exceeds the fixed size boundary")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvidenceError("source log must be UTF-8") from exc
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise EvidenceError("source log exceeds the fixed line boundary")
    return text, len(lines)


def _load_verdict(path: Path, *, root: Path) -> dict[str, Any]:
    resolved_root = root.resolve(strict=True)
    reports_root = (resolved_root / "reports").resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved.parent != reports_root or path.is_symlink() or _is_reparse_point(path):
        raise EvidenceError("source verdict escaped fixed run root")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("source verdict is invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceError("source verdict must be an object")
    expected = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "temporary_service_failed",
        "commit_sha": SOURCE_COMMIT_SHA,
        "run_id": SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise EvidenceError(f"source verdict mismatch: {key}")
    return value


def extract_evidence(*, source_root: Path) -> dict[str, object]:
    _load_verdict(
        source_root / "reports" / "qualification-verdict.json", root=source_root
    )
    stdout, stdout_lines = _read_optional_log(
        source_root / "logs" / "qualification-service.stdout.log", root=source_root
    )
    stderr, stderr_lines = _read_optional_log(
        source_root / "logs" / "qualification-service.stderr.log", root=source_root
    )
    combined = f"{stdout}\n{stderr}"
    signals = sorted(name for name, pattern in SIGNALS.items() if pattern.search(combined))
    exception_types = sorted(set(EXCEPTION_TYPE_RE.findall(combined)))
    error_summaries = _extract_error_summaries(combined)
    return {
        "schema_version": "qwen3-asr-service-start-evidence/2",
        "status": "evidence_complete" if signals or exception_types else "evidence_incomplete",
        "source_run_id": SOURCE_RUN_ID,
        "source_commit_sha": SOURCE_COMMIT_SHA,
        "failure_code": "temporary_service_failed",
        "signals": signals,
        "exception_types": exception_types,
        "error_summaries": error_summaries,
        "stdout_line_count": stdout_lines,
        "stderr_line_count": stderr_lines,
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
