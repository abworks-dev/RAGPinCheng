"""Read-only Windows runner preflight for WhisperX qualification."""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_CATALOG_PATH = REPOSITORY_ROOT / "src" / "transcription" / "profile_catalog.py"
WHISPERX_ROOT_ENV = "PRODUCTION_WHISPERX_ROOT"
WHISPERX_ROOT_CHILDREN = (
    "models",
    "nltk",
    "qualification",
    "wheel-cache",
    "reports",
)
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


class PreflightStageError(Exception):
    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(stage)


def _run_stage(stage: str, operation):
    try:
        return operation()
    except Exception as exc:
        raise PreflightStageError(stage, exc) from exc


def _directory_from_environment(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name.lower()}_missing")
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{name.lower()}_invalid") from exc
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{name.lower()}_invalid")
    return path


def _whisperx_directories() -> tuple[Path, Path, Path, Path, Path]:
    root = _directory_from_environment(WHISPERX_ROOT_ENV)
    directories: list[Path] = []
    for child in WHISPERX_ROOT_CHILDREN:
        candidate = root / child
        try:
            path = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"production_whisperx_{child.replace('-', '_')}_missing") from exc
        if path.parent != root or not path.is_dir() or path.is_symlink():
            raise ValueError(f"production_whisperx_{child.replace('-', '_')}_invalid")
        directories.append(path)
    return tuple(directories)  # type: ignore[return-value]


def _profile_admission() -> str:
    """Verify the checked-out static catalog without importing venv dependencies."""
    try:
        source = PROFILE_CATALOG_PATH.read_text(encoding="utf-8")
        catalog = ast.parse(source, filename=str(PROFILE_CATALOG_PATH))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ValueError("profile-catalog-unavailable") from exc

    profile_id_matches = [
        node.value.value
        for node in catalog.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "WHISPERX_BALANCED_PROFILE_ID"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if profile_id_matches != ["whisperx-large-v3-zh-balanced-v2"]:
        raise ValueError("profile-catalog-invalid")

    admissions: list[str] = []
    for node in ast.walk(catalog):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "TranscriptionProfileDefinition"
            and node.func.attr == "create"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        profile_id = keywords.get("profile_id")
        admission = keywords.get("admission")
        if not (
            isinstance(profile_id, ast.Name)
            and profile_id.id == "WHISPERX_BALANCED_PROFILE_ID"
            and isinstance(admission, ast.Attribute)
            and isinstance(admission.value, ast.Name)
            and admission.value.id == "ProfileAdmission"
        ):
            continue
        admissions.append(admission.attr)
    if admissions != ["disabled"]:
        raise ValueError("profile_admission_not_disabled")
    return "disabled"


def _gpu_identity() -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("gpu_identity_unavailable") from exc
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError("unexpected_gpu_count")
    parts = [item.strip() for item in rows[0].split(",")]
    if len(parts) != 3 or not all(parts):
        raise ValueError("gpu_identity_invalid")
    try:
        memory_mib = int(parts[2])
    except ValueError as exc:
        raise ValueError("gpu_identity_invalid") from exc
    if memory_mib <= 0:
        raise ValueError("gpu_identity_invalid")
    return {
        "name": parts[0],
        "driver_version": parts[1],
        "memory_total_mib": memory_mib,
    }


def run_preflight() -> dict[str, object]:
    def import_contracts():
        from services.asr_service.model_cache import (
            WHISPERX_ALIGN_RELATIVE_PATH,
            WHISPERX_RELATIVE_PATH,
            validate_whisperx_align_cache,
            validate_whisperx_cache,
        )
        from scripts.asr_qualification_manifest import resolve_manifest_from_environment
        return (
            WHISPERX_ALIGN_RELATIVE_PATH,
            WHISPERX_RELATIVE_PATH,
            resolve_manifest_from_environment,
            validate_whisperx_align_cache,
            validate_whisperx_cache,
        )

    (
        whisperx_align_relative_path,
        whisperx_relative_path,
        resolve_manifest_from_environment,
        validate_whisperx_align_cache,
        validate_whisperx_cache,
    ) = _run_stage("imports", import_contracts)

    corpus = _run_stage(
        "shared-corpus",
        lambda: resolve_manifest_from_environment("whisperx", os.environ).manifest,
    )
    (
        model_root,
        nltk_root,
        qualification_root,
        wheel_cache_root,
        report_root,
    ) = _run_stage(
        "directories",
        _whisperx_directories,
    )
    asr_cache, align_cache = _run_stage(
        "model-cache",
        lambda: (
            validate_whisperx_cache(
                model_root,
                model_root / whisperx_relative_path / "model-manifest.json",
            ),
            validate_whisperx_align_cache(
                model_root,
                model_root / whisperx_align_relative_path / "model-manifest.json",
            ),
        ),
    )
    if not asr_cache.available:
        raise ValueError(asr_cache.reason_code)
    if not align_cache.available:
        raise ValueError(align_cache.reason_code)
    profile_admission = _run_stage("profile", _profile_admission)
    return {
        "schema_version": "whisperx-runtime-preflight/1",
        "status": "pass",
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "gpu": _run_stage("gpu", _gpu_identity),
        "manifest": corpus.identity(),
        "asr_model_revision": "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
        "align_model_revision": "51d27579a1040ee4e967979278d5f76b9c32c375",
        "nltk_root_available": nltk_root.is_dir(),
        "qualification_root_available": qualification_root.is_dir(),
        "wheel_cache_root_available": wheel_cache_root.is_dir(),
        "report_root_available": report_root.is_dir(),
        "profile_admission": profile_admission,
        "production_services_modified": False,
    }


def _failure_code(exc: Exception) -> str:
    if not isinstance(exc, ValueError):
        return "runtime-preflight-failed"
    value = str(exc)
    return {
        "neutral ASR qualification manifest is not configured": (
            "shared-corpus-unconfigured"
        ),
        "neutral qualification root and manifest path must be configured together": (
            "shared-corpus-configuration-incomplete"
        ),
    }.get(value, value if value.isascii() and " " not in value else "runtime-preflight-failed")


def _failure_result(exc: Exception) -> dict[str, object]:
    if isinstance(exc, PreflightStageError):
        if exc.stage == "shared-corpus" and isinstance(
            exc.cause, (OSError, RuntimeError)
        ):
            failure_code = "shared-corpus-unavailable"
        else:
            failure_code = _failure_code(exc.cause)
        failure_stage = exc.stage
    else:
        failure_code = _failure_code(exc)
        failure_stage = "unclassified"
    return {
        "schema_version": "whisperx-runtime-preflight/1",
        "status": "fail",
        "failure_code": failure_code,
        "failure_stage": failure_stage,
        "production_services_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_preflight()
    except Exception as exc:
        result = _failure_result(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
