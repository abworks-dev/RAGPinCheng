from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from scripts.extract_faster_whisper_resolver_evidence import EvidenceError, _parse_logs


SOURCE_RUN_ID = "30972780438"
SOURCE_COMMIT_SHA = "9f0cb2b0ba9ae2f226a289f4a4db68333fcef50e"
QUALIFICATION_ROOT = Path(r"${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR\qualification\qwen3-asr")
SOURCE_RUN_ROOT = QUALIFICATION_ROOT / "dependency-diagnostics" / SOURCE_RUN_ID
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000
MAX_LINE_CHARS = 16_384

DIAGNOSTIC_FIELDS = {
    "schema_version",
    "status",
    "failure_code",
    "commit_sha",
    "diagnostic_run_id",
    "source_run_id",
    "source_commit_sha",
    "production_freeze_sha256",
    "combined_requirements_sha256",
    "windows_requirements_sha256",
    "qwen3_asr_requirements_sha256",
    "internal_wheel_manifest_sha256",
    "resolver_replayed",
    "resolver_exit_code",
    "dependency_operation",
    "failure_origin",
    "native_exit_code",
    "captured_line_count",
    "diagnosis_kind",
    "affected_requirement",
    "focused_probe_executed",
    "focused_probe_exit_code",
    "cleanup_complete",
    "profile_admission",
    "production_services_modified",
}


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _read_fixed_file(path: Path, *, root: Path) -> tuple[list[str], dict[str, Any]]:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    allowed_parents = {
        (resolved_root / "logs").resolve(strict=True),
        (resolved_root / "state").resolve(strict=True),
    }
    if resolved.parent not in allowed_parents:
        raise EvidenceError("source file escaped fixed diagnostic root")
    if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
        raise EvidenceError("source file must be a regular non-reparse file")
    size_bytes = path.stat().st_size
    if size_bytes <= 0 or size_bytes > MAX_FILE_BYTES:
        raise EvidenceError("source file size is outside the fixed boundary")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("source file must be strict UTF-8") from exc
    lines = text.splitlines()
    if not lines or len(lines) > MAX_LINES:
        raise EvidenceError("source line count is outside the fixed boundary")
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        raise EvidenceError("source line exceeds the fixed boundary")
    return lines, {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": size_bytes,
        "line_count": len(lines),
    }


def _load_source_diagnostic(path: Path, *, root: Path) -> dict[str, Any]:
    lines, _ = _read_fixed_file(path, root=root)
    try:
        diagnostic = json.loads("\n".join(lines))
    except json.JSONDecodeError as exc:
        raise EvidenceError("source diagnostic is invalid JSON") from exc
    if not isinstance(diagnostic, dict) or set(diagnostic) != DIAGNOSTIC_FIELDS:
        raise EvidenceError("source diagnostic field set mismatch")
    expected = {
        "schema_version": "qwen3-asr-r3-dependency-diagnostic/2",
        "status": "diagnostic_failed",
        "failure_code": "focused_probe_failed",
        "commit_sha": SOURCE_COMMIT_SHA,
        "diagnostic_run_id": SOURCE_RUN_ID,
        "source_run_id": "30970277613",
        "source_commit_sha": "86b69db6831e3cd201436a26bbe836229bf419bd",
        "resolver_replayed": True,
        "resolver_exit_code": 1,
        "dependency_operation": "focused_probe_command",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "captured_line_count": 16,
        "diagnosis_kind": "unknown",
        "affected_requirement": "oss2",
        "focused_probe_executed": True,
        "focused_probe_exit_code": 1,
        "cleanup_complete": True,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    for key, value in expected.items():
        if diagnostic.get(key) != value:
            raise EvidenceError(f"source diagnostic mismatch: {key}")
    for key in (
        "production_freeze_sha256",
        "combined_requirements_sha256",
        "windows_requirements_sha256",
        "qwen3_asr_requirements_sha256",
        "internal_wheel_manifest_sha256",
    ):
        value = diagnostic.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise EvidenceError(f"source diagnostic mismatch: {key}")
    return diagnostic


def extract_evidence(*, source_root: Path = SOURCE_RUN_ROOT) -> dict[str, Any]:
    diagnostic = _load_source_diagnostic(
        source_root / "state" / "dependency-diagnostic.json", root=source_root
    )
    log_lines: dict[str, list[str]] = {}
    log_metadata: dict[str, dict[str, Any]] = {}
    for name, relative in {
        "resolver_replay": Path("logs/resolver-replay.log"),
        "focused_binary_probe": Path("logs/focused-binary-probe.log"),
    }.items():
        lines, metadata = _read_fixed_file(source_root / relative, root=source_root)
        log_lines[name] = lines
        log_metadata[name] = metadata

    parsed = _parse_logs(
        (log_lines["resolver_replay"], log_lines["focused_binary_probe"])
    )
    evidence_complete = len(parsed["blockers"]) > 0
    return {
        "schema_version": "qwen3-asr-r3-resolver-evidence/1",
        "status": "evidence_complete" if evidence_complete else "evidence_incomplete",
        "source_run_id": SOURCE_RUN_ID,
        "source_commit_sha": SOURCE_COMMIT_SHA,
        "source_diagnostic_schema": diagnostic["schema_version"],
        "logs": log_metadata,
        "error_family_counts": parsed["error_family_counts"],
        "candidates": parsed["candidates"],
        "blockers": parsed["blockers"],
        "unparsed_relevant_line_count": parsed["unparsed_relevant_line_count"],
        "profile_admission": "disabled",
        "production_services_modified": False,
    }


def _write_output(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    _write_output(args.output, extract_evidence())
    print("sanitized-qwen3-asr-resolver-evidence-written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
