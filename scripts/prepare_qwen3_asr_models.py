"""Prepare the two immutable Qwen3-ASR snapshots as strict offline caches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from asr_service.model_cache import (
    MODEL_MANIFEST_VERSION,
    QWEN3_ALIGNER_MODEL_ID,
    QWEN3_ALIGNER_RELATIVE_PATH,
    QWEN3_ALIGNER_REVISION,
    QWEN3_ASR_MODEL_ID,
    QWEN3_ASR_RELATIVE_PATH,
    QWEN3_ASR_REVISION,
    validate_qwen3_aligner_cache,
    validate_qwen3_asr_cache,
)

MANIFEST_NAME = "model-manifest.json"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    label: str
    model_id: str
    revision: str
    relative_path: str
    validator: Callable[[Path | None, Path | None], object]


SPECS = (
    ModelSpec(
        "asr",
        QWEN3_ASR_MODEL_ID,
        QWEN3_ASR_REVISION,
        QWEN3_ASR_RELATIVE_PATH,
        validate_qwen3_asr_cache,
    ),
    ModelSpec(
        "aligner",
        QWEN3_ALIGNER_MODEL_ID,
        QWEN3_ALIGNER_REVISION,
        QWEN3_ALIGNER_RELATIVE_PATH,
        validate_qwen3_aligner_cache,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_root(path: Path, label: str, *, exists: bool) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be absolute")
    result = path.resolve(strict=exists)
    if result == Path(result.anchor):
        raise RuntimeError(f"{label} must not be a drive root")
    return result


def _files(root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    for item in root.rglob("*"):
        if item.is_symlink():
            raise RuntimeError("model tree contains a symbolic link")
        if item.is_dir():
            continue
        if not item.is_file():
            raise RuntimeError("model tree contains a non-regular file")
        if item.name != MANIFEST_NAME and ".cache" not in item.relative_to(root).parts:
            result.append(item)
    if not result:
        raise RuntimeError("model tree is empty")
    return tuple(sorted(result, key=lambda item: item.relative_to(root).as_posix()))


def _manifest(model_root: Path, spec: ModelSpec) -> Path:
    entries = []
    for item in _files(model_root):
        relative = item.relative_to(model_root).as_posix()
        pure = PurePosixPath(relative)
        if pure.is_absolute() or "\\" in relative or any(
            part in {"", ".", ".."} for part in pure.parts
        ):
            raise RuntimeError("unsafe model path")
        entries.append(
            {
                "path": relative,
                "size_bytes": item.stat().st_size,
                "sha256": _sha256(item),
            }
        )
    path = model_root / MANIFEST_NAME
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": MODEL_MANIFEST_VERSION,
                "model_id": spec.model_id,
                "model_revision": spec.revision,
                "model_path": spec.relative_path,
                "files": entries,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)
    return path


def _download(**kwargs: object) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(**kwargs)


def _prepare_one(
    cache: Path,
    staging: Path,
    spec: ModelSpec,
    downloader: Callable[..., str],
) -> dict[str, str]:
    target = cache / Path(*PurePosixPath(spec.relative_path).parts)
    target_manifest = target / MANIFEST_NAME
    if target.exists():
        status = spec.validator(cache, target_manifest)
        if not getattr(status, "available", False):
            raise RuntimeError(f"existing {spec.label} cache is invalid")
        return {
            "label": spec.label,
            "status": "reused",
            "model_id": spec.model_id,
            "revision": spec.revision,
            "manifest_sha256": _sha256(target_manifest),
        }

    download_root = staging / spec.label / "download"
    download_root.mkdir(parents=True)
    returned = Path(
        downloader(
            repo_id=spec.model_id,
            revision=spec.revision,
            local_dir=str(download_root),
            local_dir_use_symlinks=False,
        )
    ).resolve(strict=True)
    if returned != download_root.resolve(strict=True):
        raise RuntimeError("downloader escaped fixed staging")

    candidate_cache = staging / spec.label / "candidate-cache"
    candidate = candidate_cache / Path(*PurePosixPath(spec.relative_path).parts)
    candidate.mkdir(parents=True)
    for source in _files(download_root):
        destination = candidate / source.relative_to(download_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    manifest = _manifest(candidate, spec)
    if not getattr(spec.validator(candidate_cache, manifest), "available", False):
        raise RuntimeError(f"staged {spec.label} cache validation failed")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError("final cache appeared during preparation")
    os.replace(candidate, target)
    if not getattr(spec.validator(cache, target_manifest), "available", False):
        raise RuntimeError(f"promoted {spec.label} cache validation failed")
    return {
        "label": spec.label,
        "status": "prepared",
        "model_id": spec.model_id,
        "revision": spec.revision,
        "manifest_sha256": _sha256(target_manifest),
    }


def prepare_models(
    cache_root: Path,
    staging_root: Path,
    *,
    downloader: Callable[..., str] = _download,
) -> dict[str, object]:
    cache = _strict_root(cache_root, "cache_root", exists=False)
    staging = _strict_root(staging_root, "staging_root", exists=False)
    if staging.exists() and any(staging.iterdir()):
        raise RuntimeError("staging_root must be empty")
    staging.mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": "qwen3-asr-model-preparation/1",
        "models": [
            _prepare_one(cache, staging, spec, downloader) for spec in SPECS
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path)
    parser.add_argument("--report-path", required=True, type=Path)
    args = parser.parse_args()
    result = prepare_models(args.cache_root, args.staging_root)
    report_parent = _strict_root(args.report_path.parent, "report_parent", exists=True)
    report = (report_parent / args.report_path.name).resolve()
    if report.parent != report_parent:
        raise RuntimeError("report_path escapes report parent")
    report.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "pass", "model_count": 2}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
