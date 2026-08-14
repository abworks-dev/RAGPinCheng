"""Prepare the pinned SenseVoiceSmall model cache and strict manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.asr_service.model_cache import (
    MODEL_MANIFEST_VERSION,
    SENSEVOICE_MODEL_ID,
    SENSEVOICE_RELATIVE_PATH,
    SENSEVOICE_REVISION,
    validate_sensevoice_cache,
)

MANIFEST_NAME = "model-manifest.json"
Downloader = Callable[[Path], Path]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _assert_regular_tree(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("downloaded model root must be a regular directory")
    files: list[Path] = []
    for target in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if target.is_symlink():
            raise RuntimeError(f"model tree contains a symbolic link: {target}")
        if target.is_file():
            if target.name == MANIFEST_NAME:
                raise RuntimeError("downloaded model unexpectedly contains model-manifest.json")
            files.append(target)
        elif not target.is_dir():
            raise RuntimeError(f"model tree contains an unsupported entry: {target}")
    if not files:
        raise RuntimeError("downloaded model tree contains no files")
    return files


def _build_manifest(model_path: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for target in _assert_regular_tree(model_path):
        entries.append(
            {
                "path": target.relative_to(model_path).as_posix(),
                "size_bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    return {
        "schema_version": MODEL_MANIFEST_VERSION,
        "model_id": SENSEVOICE_MODEL_ID,
        "model_revision": SENSEVOICE_REVISION,
        "model_path": SENSEVOICE_RELATIVE_PATH,
        "files": entries,
    }


def _write_manifest(model_path: Path) -> Path:
    manifest_path = model_path / MANIFEST_NAME
    content = json.dumps(
        _build_manifest(model_path),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ) + "\n"
    manifest_path.write_text(content, encoding="utf-8", newline="\n")
    return manifest_path


def _archive(path: Path, backup_root: Path, reason: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    suffix = f"{_timestamp()}-{SENSEVOICE_REVISION[:12]}"
    destination = backup_root / f"{reason}-{suffix}"
    counter = 1
    while destination.exists():
        destination = backup_root / f"{reason}-{suffix}-{counter}"
        counter += 1
    shutil.move(str(path), str(destination))
    return destination


def _default_downloader(download_cache: Path) -> Path:
    from modelscope import snapshot_download

    downloaded = snapshot_download(
        SENSEVOICE_MODEL_ID,
        revision=SENSEVOICE_REVISION,
        cache_dir=str(download_cache),
    )
    return Path(downloaded)


def _summary(status: str, manifest_path: Path, model_path: Path) -> dict[str, object]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = payload["files"]
    return {
        "status": status,
        "schema_version": payload["schema_version"],
        "model_id": payload["model_id"],
        "model_revision": payload["model_revision"],
        "model_path": str(model_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "file_count": len(files),
        "total_bytes": sum(entry["size_bytes"] for entry in files),
        "all_files_match": True,
        "symlink_count": 0,
    }


def prepare_model(
    cache_root: Path,
    staging_root: Path,
    backup_root: Path,
    downloader: Downloader = _default_downloader,
) -> dict[str, object]:
    cache_root = cache_root.resolve()
    staging_root = staging_root.resolve()
    backup_root = backup_root.resolve()
    final_model = cache_root / Path(SENSEVOICE_RELATIVE_PATH)
    final_manifest = final_model / MANIFEST_NAME

    current = validate_sensevoice_cache(cache_root, final_manifest)
    if current.available:
        return _summary("already-available", final_manifest, final_model)

    cache_root.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    run_root = staging_root / f"model-{_timestamp()}-{os.getpid()}"
    download_cache = run_root / "download-cache"
    staged_cache = run_root / "validated-cache"
    staged_model = staged_cache / Path(SENSEVOICE_RELATIVE_PATH)
    run_root.mkdir(parents=True, exist_ok=False)

    try:
        download_cache.mkdir(parents=True, exist_ok=False)
        downloaded = downloader(download_cache).resolve(strict=True)
        resolved_download_cache = download_cache.resolve(strict=True)
        if downloaded != resolved_download_cache and resolved_download_cache not in downloaded.parents:
            raise RuntimeError("model downloader returned a path outside the staging cache")
        _assert_regular_tree(downloaded)
        staged_model.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(downloaded), str(staged_model))
        manifest_path = _write_manifest(staged_model)
        staged_status = validate_sensevoice_cache(staged_cache, manifest_path)
        if not staged_status.available:
            raise RuntimeError(f"staged model validation failed: {staged_status.reason_code}")

        final_model.parent.mkdir(parents=True, exist_ok=True)
        previous_backup: Path | None = None
        if final_model.exists():
            previous_backup = _archive(final_model, backup_root, "invalid-model")
        try:
            shutil.move(str(staged_model), str(final_model))
        except Exception:
            if previous_backup is not None and not final_model.exists():
                shutil.move(str(previous_backup), str(final_model))
            raise

        final_status = validate_sensevoice_cache(cache_root, final_manifest)
        if not final_status.available:
            failed_final = _archive(final_model, backup_root, "failed-promoted-model")
            if previous_backup is not None and previous_backup.exists():
                shutil.move(str(previous_backup), str(final_model))
            raise RuntimeError(
                f"promoted model validation failed: {final_status.reason_code}; archived to {failed_final}"
            )

        if run_root.exists():
            _archive(run_root, backup_root, "successful-model-staging")
        return _summary("prepared", final_manifest, final_model)
    except Exception:
        if run_root.exists():
            _archive(run_root, backup_root, "failed-model-staging")
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = prepare_model(args.cache_root, args.staging_root, args.backup_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
