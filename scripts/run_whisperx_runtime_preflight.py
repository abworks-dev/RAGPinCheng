"""Read-only Windows runner preflight for WhisperX qualification."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


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
    from asr_service.model_cache import (
        WHISPERX_ALIGN_RELATIVE_PATH,
        WHISPERX_RELATIVE_PATH,
        validate_whisperx_align_cache,
        validate_whisperx_cache,
    )
    from scripts.asr_qualification_manifest import resolve_manifest_from_environment
    from src.transcription.profile_catalog import (
        WHISPERX_PROFILE_ID,
        build_phase3_profile_catalog,
    )

    try:
        corpus = resolve_manifest_from_environment("whisperx", os.environ).manifest
    except (OSError, RuntimeError) as exc:
        raise ValueError("shared-corpus-unavailable") from exc
    model_root = _directory_from_environment("PRODUCTION_WHISPERX_MODEL_ROOT")
    nltk_root = _directory_from_environment("PRODUCTION_WHISPERX_NLTK_ROOT")
    qualification_root = _directory_from_environment(
        "PRODUCTION_WHISPERX_QUALIFICATION_ROOT"
    )
    wheel_cache_root = _directory_from_environment(
        "PRODUCTION_WHISPERX_WHEEL_CACHE_ROOT"
    )
    report_root = _directory_from_environment("PRODUCTION_WHISPERX_REPORT_ROOT")
    asr_cache = validate_whisperx_cache(
        model_root, model_root / WHISPERX_RELATIVE_PATH / "model-manifest.json"
    )
    align_cache = validate_whisperx_align_cache(
        model_root,
        model_root / WHISPERX_ALIGN_RELATIVE_PATH / "model-manifest.json",
    )
    if not asr_cache.available:
        raise ValueError(asr_cache.reason_code)
    if not align_cache.available:
        raise ValueError(align_cache.reason_code)
    profile = next(
        item.profile
        for item in build_phase3_profile_catalog()
        if item.profile.profile_id == WHISPERX_PROFILE_ID
    )
    if profile.admission.value != "disabled":
        raise ValueError("profile_admission_not_disabled")
    return {
        "schema_version": "whisperx-runtime-preflight/1",
        "status": "pass",
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "gpu": _gpu_identity(),
        "manifest": corpus.identity(),
        "asr_model_revision": "53ecf83a5bedc5597eb8c8b34eac29e5345520ff",
        "align_model_revision": "51d27579a1040ee4e967979278d5f76b9c32c375",
        "nltk_root_available": nltk_root.is_dir(),
        "qualification_root_available": qualification_root.is_dir(),
        "wheel_cache_root_available": wheel_cache_root.is_dir(),
        "report_root_available": report_root.is_dir(),
        "profile_admission": profile.admission.value,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_preflight()
    except Exception as exc:
        result = {
            "schema_version": "whisperx-runtime-preflight/1",
            "status": "fail",
            "failure_code": _failure_code(exc),
            "production_services_modified": False,
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
