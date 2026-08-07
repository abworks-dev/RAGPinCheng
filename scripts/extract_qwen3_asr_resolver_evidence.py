from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from scripts.extract_faster_whisper_resolver_evidence import (
    NO_MATCH_RE,
    OWNER_DEPENDENCY_RE,
    PACKAGE_RE,
    REQUESTED_CONSTRAINT_RE,
    REQUESTED_RE,
    REQUIRES_PYTHON_RE,
    EvidenceError,
    _parse_logs,
    _parse_requirement,
    _specifiers_prove_conflict,
    _strip_powershell_prefix,
)


SOURCE_RUN_ID = "30972780438"
SOURCE_COMMIT_SHA = "9f0cb2b0ba9ae2f226a289f4a4db68333fcef50e"
QUALIFICATION_ROOT = Path(r"D:\Services\RAGPinCheng-ASR\qualification\qwen3-asr")
SOURCE_RUN_ROOT = QUALIFICATION_ROOT / "dependency-diagnostics" / SOURCE_RUN_ID
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000
MAX_LINE_CHARS = 16_384
MAX_UNPARSED_RECORDS = 32

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


def _safe_requirement_parsed(value: str) -> bool:
    try:
        return _parse_requirement(value) is not None
    except EvidenceError:
        return False


def _character_counts(value: str) -> dict[str, int]:
    return {
        "ascii_letters": sum(char.isascii() and char.isalpha() for char in value),
        "digits": sum(char.isascii() and char.isdigit() for char in value),
        "whitespace": sum(char.isspace() for char in value),
        "punctuation": sum(
            char.isascii() and not char.isalnum() and not char.isspace()
            for char in value
        ),
        "non_ascii": sum(not char.isascii() for char in value),
    }


def _safe_unparsed_category(line: str) -> str:
    if re.search(
        r"(?i)(Could not fetch URL|connection (?:error|reset|refused)|"
        r"Max retries exceeded|timed? out|proxy error|certificate verify failed)",
        line,
    ):
        return "network_or_index"
    if re.search(r"(?i)requires Python", line):
        return "python_incompatible"
    if re.search(r"(?i)(matching distribution|distributions available)", line):
        return "binary_unavailable"
    if re.search(
        r"(?i)(conflict|depends on|requested|resolutionimpossible|cannot install)",
        line,
    ):
        return "resolver_context_only"
    return "still_unknown"


def _context_requirements(line: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    requested = re.search(
        r"The user requested(?: \(constraint\))?\s+(?P<requirement>.+)$",
        line,
        re.IGNORECASE,
    )
    if requested and _safe_requirement_parsed(requested.group("requirement")):
        parsed = _parse_requirement(requested.group("requirement"))
        if parsed:
            package, specifier = parsed
            rows.append({"kind": "requested_requirement", "package": package, "specifier": specifier, "owner": "", "owner_version": ""})
    owner = re.search(
        r"(?P<owner>[A-Za-z0-9_.-]+)\s+"
        r"(?P<owner_version>[A-Za-z0-9+.!_-]+)\s+depends on\s+"
        r"(?P<requirement>.+)$",
        line,
        re.IGNORECASE,
    )
    if owner and _safe_requirement_parsed(owner.group("requirement")):
        parsed = _parse_requirement(owner.group("requirement"))
        if parsed:
            package, specifier = parsed
            rows.append({"kind": "owner_dependency", "package": package, "specifier": specifier, "owner": owner.group("owner"), "owner_version": owner.group("owner_version")})
    return rows


def _unparsed_records(
    named_logs: dict[str, list[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source, lines in named_logs.items():
        expect_unavailable_names = False
        for line_number, raw in enumerate(lines, start=1):
            line = _strip_powershell_prefix(raw)
            lowered = line.lower()
            relevant = False
            parsed = False

            if "the conflict is caused by" in lowered:
                relevant = parsed = True
            if "resolutionimpossible" in lowered:
                relevant = parsed = True
            if "cannot install" in lowered and "conflicting dependencies" in lowered:
                relevant = parsed = True
            if re.search(
                r"(?i)(Could not open requirements file|Invalid requirement|Invalid constraint)",
                line,
            ):
                relevant = parsed = True
            if re.search(
                r"(?i)(Could not fetch URL|connection (?:error|reset|refused)|"
                r"Max retries exceeded|timed? out|proxy error|certificate verify failed)",
                line,
            ):
                relevant = parsed = True

            match = NO_MATCH_RE.search(line)
            if match:
                relevant = True
                parsed = _safe_requirement_parsed(match.group("requirement")) or parsed

            match = REQUESTED_CONSTRAINT_RE.match(line)
            if match:
                relevant = True
                parsed = _safe_requirement_parsed(match.group("requirement")) or parsed
            else:
                match = REQUESTED_RE.match(line)
                if match:
                    relevant = True
                    parsed = _safe_requirement_parsed(match.group("requirement")) or parsed

            match = OWNER_DEPENDENCY_RE.match(line)
            if match:
                relevant = True
                parsed = _safe_requirement_parsed(match.group("requirement")) or parsed

            match = REQUIRES_PYTHON_RE.search(line)
            if match:
                relevant = True
                parsed = _safe_requirement_parsed(
                    match.group("package") + match.group("specifier")
                ) or parsed

            if "no matching distributions available for your environment" in lowered:
                expect_unavailable_names = True
                relevant = parsed = True
                continue
            if expect_unavailable_names:
                stripped = line.strip()
                if PACKAGE_RE.fullmatch(stripped):
                    relevant = parsed = True
                elif stripped:
                    expect_unavailable_names = False

            if not relevant and re.search(
                r"(?i)(ERROR:|conflict|depends on|requested|matching distribution|requires python)",
                raw,
            ):
                relevant = True
            if relevant and not parsed:
                payload = raw.encode("utf-8")
                records.append(
                    {
                        "source": source,
                        "line_number": line_number,
                        "character_count": len(raw),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "character_types": _character_counts(raw),
                        "category": _safe_unparsed_category(line),
                        "requirements": _context_requirements(line),
                    }
                )
                if len(records) > MAX_UNPARSED_RECORDS:
                    raise EvidenceError("unparsed evidence exceeds the fixed limit")
    return records


def _classification(
    parsed: dict[str, Any], unparsed_records: list[dict[str, Any]]
) -> str:
    blocker_kinds = {item["diagnosis_kind"] for item in parsed["blockers"]}
    if "version_constraint_conflict" in blocker_kinds:
        return "proven_version_conflict"
    if "binary_distribution_unavailable" in blocker_kinds:
        return "binary_unavailable"
    context_rows = [row for item in unparsed_records for row in item["requirements"]]
    for owner in (row for row in context_rows if row["kind"] == "owner_dependency"):
        for requested in (row for row in context_rows if row["kind"] == "requested_requirement"):
            if owner["package"] == requested["package"] and _specifiers_prove_conflict(owner["specifier"], requested["specifier"]):
                return "proven_version_conflict"
    categories = {item["category"] for item in unparsed_records}
    if "network_or_index" in categories:
        return "network_or_index"
    if "python_incompatible" in categories:
        return "python_incompatible"
    if "binary_unavailable" in categories:
        return "binary_unavailable"
    if categories and categories <= {"resolver_context_only"}:
        return "resolver_context_only"
    return "still_unknown"


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
    unparsed_records = _unparsed_records(log_lines)
    if len(unparsed_records) != parsed["unparsed_relevant_line_count"]:
        raise EvidenceError("unparsed evidence accounting mismatch")
    classification = _classification(parsed, unparsed_records)
    evidence_complete = classification not in {
        "resolver_context_only",
        "still_unknown",
    }
    return {
        "schema_version": "qwen3-asr-r3-resolver-evidence/3",
        "status": "evidence_complete" if evidence_complete else "evidence_incomplete",
        "classification": classification,
        "source_run_id": SOURCE_RUN_ID,
        "source_commit_sha": SOURCE_COMMIT_SHA,
        "source_diagnostic_schema": diagnostic["schema_version"],
        "logs": log_metadata,
        "error_family_counts": parsed["error_family_counts"],
        "candidates": parsed["candidates"],
        "blockers": parsed["blockers"],
        "unparsed_relevant_line_count": parsed["unparsed_relevant_line_count"],
        "unparsed_records": unparsed_records,
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
