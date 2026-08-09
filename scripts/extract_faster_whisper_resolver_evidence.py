from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SOURCE_RUN_ID = "30968517582"
SOURCE_COMMIT_SHA = "cf57327452dbcd7e72140e5d271a3f0c2f3b5238"
QUALIFICATION_ROOT = Path(os.environ["PRODUCTION_FASTER_WHISPER_QUALIFICATION_ROOT"])
SOURCE_RUN_ROOT = QUALIFICATION_ROOT / "runs" / SOURCE_RUN_ID
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINES = 10_000
MAX_LINE_CHARS = 16_384
MAX_CANDIDATES = 32
MAX_BLOCKERS = 16

PACKAGE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$")
SPECIFIER_RE = re.compile(
    r"^(?:(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9*+.!_-]+)"
    r"(?:,(?:==|!=|<=|>=|~=|<|>)[A-Za-z0-9*+.!_-]+)*$"
)
OWNER_DEPENDENCY_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)\s+"
    r"(?P<owner_version>[A-Za-z0-9+.!_-]+)\s+depends on\s+(?P<requirement>.+)$",
    re.IGNORECASE,
)
REQUESTED_CONSTRAINT_RE = re.compile(
    r"^The user requested \(constraint\)\s+(?P<requirement>.+)$", re.IGNORECASE
)
REQUESTED_RE = re.compile(
    r"^The user requested\s+(?P<requirement>.+)$", re.IGNORECASE
)
NO_MATCH_RE = re.compile(
    r"(?:No matching distribution found for|"
    r"Could not find a version that satisfies the requirement)\s+"
    r"(?P<requirement>.+)$",
    re.IGNORECASE,
)
REQUIRES_PYTHON_RE = re.compile(
    r"(?P<package>[A-Za-z0-9_.-]+).*requires Python\s+(?P<specifier>[^;\s]+)",
    re.IGNORECASE,
)

FAMILY_KEYS = (
    "cannot_install",
    "conflict_header",
    "invalid_input",
    "network_or_index",
    "no_matching_distribution",
    "requires_python",
    "resolution_impossible",
    "unknown_error",
)

DIAGNOSTIC_FIELDS = {
    "schema_version",
    "status",
    "failure_code",
    "commit_sha",
    "run_id",
    "dependency_stage",
    "dependency_operation",
    "failure_origin",
    "native_exit_code",
    "captured_line_count",
    "diagnosis_kind",
    "affected_requirement",
    "fallback_probe_executed",
    "fallback_probe_exit_code",
    "profile_admission",
    "production_services_modified",
}


class EvidenceError(RuntimeError):
    pass


def _normalise_package(value: str) -> str:
    package = re.sub(r"[-_.]+", "-", value.strip()).lower()
    if not PACKAGE_RE.fullmatch(package):
        raise EvidenceError("unsafe package identity")
    return package


def _normalise_specifier(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"\s+and\s+(?=[<>=!~])", ",", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        return ""
    if len(cleaned) > 128 or not SPECIFIER_RE.fullmatch(cleaned):
        return ""
    return cleaned


def _parse_requirement(value: str) -> tuple[str, str] | None:
    cleaned = value.strip()
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
    cleaned = cleaned.split(";", 1)[0].strip()
    match = re.match(
        r"^(?P<package>[A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?"
        r"(?P<specifier>.*)$",
        cleaned,
    )
    if not match:
        return None
    package = _normalise_package(match.group("package"))
    raw_specifier = match.group("specifier").strip()
    specifier = _normalise_specifier(raw_specifier)
    if raw_specifier and not specifier:
        return None
    return package, specifier


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _specifiers_prove_conflict(owner_specifier: str, constraint_specifier: str) -> bool:
    """Prove only that one exact numeric constraint violates every owner clause."""
    if not owner_specifier or not constraint_specifier:
        return False
    constraint_match = re.fullmatch(r"==(?P<version>\d+(?:\.\d+)*)", constraint_specifier)
    if constraint_match is None:
        return False
    pinned = _numeric_version(constraint_match.group("version"))
    if pinned is None:
        return False
    for clause in owner_specifier.split(","):
        match = re.fullmatch(r"(?P<operator>==|!=|<=|>=|<|>)(?P<version>\d+(?:\.\d+)*)", clause)
        if match is None:
            return False
        target = _numeric_version(match.group("version"))
        if target is None:
            return False
        comparison = _compare_versions(pinned, target)
        satisfied = {
            "==": comparison == 0,
            "!=": comparison != 0,
            "<=": comparison <= 0,
            ">=": comparison >= 0,
            "<": comparison < 0,
            ">": comparison > 0,
        }[match.group("operator")]
        if not satisfied:
            return True
    return False


def _strip_powershell_prefix(line: str) -> str:
    cleaned = line.strip()
    error_match = re.search(r"(?i)ERROR:\s*(?P<message>.+)$", cleaned)
    if error_match:
        return error_match.group("message").strip()
    return cleaned


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _read_fixed_file(path: Path, *, root: Path) -> tuple[list[str], dict[str, Any]]:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved.parent not in {
        (resolved_root / "logs").resolve(strict=True),
        (resolved_root / "reports").resolve(strict=True),
    }:
        raise EvidenceError("source file escaped fixed run root")
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
        "schema_version": "faster-whisper-r3-dependency-failure/2",
        "status": "fail",
        "failure_code": "dependency_preparation_failed",
        "commit_sha": SOURCE_COMMIT_SHA,
        "run_id": SOURCE_RUN_ID,
        "dependency_stage": "pip_download",
        "dependency_operation": "pip_download_command",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "diagnosis_kind": "resolver_replay_insufficient",
        "affected_requirement": "",
        "fallback_probe_executed": True,
        "fallback_probe_exit_code": 1,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    for key, value in expected.items():
        if diagnostic.get(key) != value:
            raise EvidenceError(f"source diagnostic mismatch: {key}")
    captured = diagnostic.get("captured_line_count")
    if isinstance(captured, bool) or not isinstance(captured, int) or captured <= 0:
        raise EvidenceError("source diagnostic captured line count is invalid")
    return diagnostic


def _add_candidate(
    candidates: Counter[tuple[str, str, str, str, str]],
    *,
    kind: str,
    requirement: str,
    owner: str = "",
    owner_version: str = "",
) -> bool:
    try:
        parsed = _parse_requirement(requirement)
        safe_owner = _normalise_package(owner) if owner else ""
    except EvidenceError:
        return False
    if parsed is None:
        return False
    package, specifier = parsed
    safe_owner_version = owner_version if re.fullmatch(r"[A-Za-z0-9+.!_-]{1,64}", owner_version) else ""
    candidates[(kind, package, specifier, safe_owner, safe_owner_version)] += 1
    if len(candidates) > MAX_CANDIDATES:
        raise EvidenceError("candidate evidence exceeds the fixed limit")
    return True


def _parse_logs(log_sets: Iterable[list[str]]) -> dict[str, Any]:
    families: Counter[str] = Counter()
    candidates: Counter[tuple[str, str, str, str, str]] = Counter()
    unparsed_relevant = 0

    for lines in log_sets:
        expect_unavailable_names = False
        for raw in lines:
            line = _strip_powershell_prefix(raw)
            lowered = line.lower()
            relevant = False
            parsed = False

            if "the conflict is caused by" in lowered:
                families["conflict_header"] += 1
                relevant = parsed = True
            if "resolutionimpossible" in lowered:
                families["resolution_impossible"] += 1
                relevant = parsed = True
            if "cannot install" in lowered and "conflicting dependencies" in lowered:
                families["cannot_install"] += 1
                relevant = parsed = True
            if re.search(
                r"(?i)(Could not open requirements file|Invalid requirement|Invalid constraint)",
                line,
            ):
                families["invalid_input"] += 1
                relevant = parsed = True
            if re.search(
                r"(?i)(Could not fetch URL|connection (?:error|reset|refused)|"
                r"Max retries exceeded|timed? out|proxy error|certificate verify failed)",
                line,
            ):
                families["network_or_index"] += 1
                relevant = parsed = True

            match = NO_MATCH_RE.search(line)
            if match:
                families["no_matching_distribution"] += 1
                relevant = True
                parsed = _add_candidate(
                    candidates,
                    kind="no_matching_distribution",
                    requirement=match.group("requirement"),
                ) or parsed

            match = REQUESTED_CONSTRAINT_RE.match(line)
            if match:
                relevant = True
                parsed = _add_candidate(
                    candidates,
                    kind="constraint_requirement",
                    requirement=match.group("requirement"),
                ) or parsed
            else:
                match = REQUESTED_RE.match(line)
                if match:
                    relevant = True
                    parsed = _add_candidate(
                        candidates,
                        kind="requested_requirement",
                        requirement=match.group("requirement"),
                    ) or parsed

            match = OWNER_DEPENDENCY_RE.match(line)
            if match:
                relevant = True
                parsed = _add_candidate(
                    candidates,
                    kind="owner_dependency",
                    requirement=match.group("requirement"),
                    owner=match.group("owner"),
                    owner_version=match.group("owner_version"),
                ) or parsed

            match = REQUIRES_PYTHON_RE.search(line)
            if match:
                families["requires_python"] += 1
                relevant = True
                parsed = _add_candidate(
                    candidates,
                    kind="requires_python",
                    requirement=match.group("package") + match.group("specifier"),
                ) or parsed

            if "no matching distributions available for your environment" in lowered:
                expect_unavailable_names = True
                families["no_matching_distribution"] += 1
                relevant = parsed = True
                continue
            if expect_unavailable_names:
                stripped = line.strip()
                if PACKAGE_RE.fullmatch(stripped):
                    relevant = True
                    parsed = _add_candidate(
                        candidates,
                        kind="no_matching_distribution",
                        requirement=stripped,
                    ) or parsed
                elif stripped:
                    expect_unavailable_names = False

            if not relevant and re.search(
                r"(?i)(ERROR:|conflict|depends on|requested|matching distribution|requires python)",
                raw,
            ):
                relevant = True
            if relevant and not parsed:
                unparsed_relevant += 1

    candidate_rows = [
        {
            "kind": kind,
            "package": package,
            "specifier": specifier,
            "owner": owner,
            "owner_version": owner_version,
            "occurrence_count": count,
        }
        for (kind, package, specifier, owner, owner_version), count in sorted(candidates.items())
    ]

    by_package: dict[str, list[dict[str, Any]]] = {}
    for row in candidate_rows:
        by_package.setdefault(row["package"], []).append(row)
    blockers: list[dict[str, Any]] = []
    for package, rows in sorted(by_package.items()):
        kinds = {row["kind"] for row in rows}
        diagnosis = ""
        if "no_matching_distribution" in kinds:
            diagnosis = "binary_distribution_unavailable"
        elif "owner_dependency" in kinds and kinds.intersection(
            {"requested_requirement", "constraint_requirement"}
        ):
            owner_specifiers = {
                row["specifier"]
                for row in rows
                if row["kind"] == "owner_dependency" and row["specifier"]
            }
            constraint_specifiers = {
                row["specifier"]
                for row in rows
                if row["kind"] in {"requested_requirement", "constraint_requirement"}
                and row["specifier"]
            }
            if any(
                _specifiers_prove_conflict(owner_specifier, constraint_specifier)
                for owner_specifier in owner_specifiers
                for constraint_specifier in constraint_specifiers
            ):
                diagnosis = "version_constraint_conflict"
        if not diagnosis:
            continue
        specifiers = sorted({row["specifier"] for row in rows if row["specifier"]})
        owners = sorted({row["owner"] for row in rows if row["owner"]})
        blockers.append(
            {
                "package": package,
                "diagnosis_kind": diagnosis,
                "specifiers": specifiers[:8],
                "owners": owners[:8],
                "evidence_count": sum(row["occurrence_count"] for row in rows),
            }
        )
        if len(blockers) > MAX_BLOCKERS:
            raise EvidenceError("blocker evidence exceeds the fixed limit")

    family_counts = {key: int(families.get(key, 0)) for key in FAMILY_KEYS}
    family_counts["unknown_error"] = unparsed_relevant
    return {
        "error_family_counts": family_counts,
        "candidates": candidate_rows,
        "blockers": blockers,
        "unparsed_relevant_line_count": unparsed_relevant,
    }


def extract_evidence(*, source_root: Path = SOURCE_RUN_ROOT) -> dict[str, Any]:
    diagnostic = _load_source_diagnostic(
        source_root / "reports" / "dependency-diagnostic.json", root=source_root
    )
    log_lines: dict[str, list[str]] = {}
    log_metadata: dict[str, dict[str, Any]] = {}
    for name, relative in {
        "primary": Path("logs/pip-download.log"),
        "fallback": Path("logs/pip-resolver-fallback.log"),
    }.items():
        lines, metadata = _read_fixed_file(source_root / relative, root=source_root)
        log_lines[name] = lines
        log_metadata[name] = metadata

    parsed = _parse_logs((log_lines["primary"], log_lines["fallback"]))
    evidence_complete = len(parsed["blockers"]) > 0
    return {
        "schema_version": "faster-whisper-r3-resolver-evidence/1",
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
    result = extract_evidence()
    _write_output(args.output, result)
    print("sanitized-resolver-evidence-written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
