"""Strict, local-only SenseVoice model manifest validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MODEL_MANIFEST_VERSION = "asr-model-manifest/1"
SENSEVOICE_MODEL_ID = "iic/SenseVoiceSmall"
SENSEVOICE_REVISION = "7bf452403abd7353a300cd760f7adae7701c92c1"
SENSEVOICE_RELATIVE_PATH = f"SenseVoiceSmall/{SENSEVOICE_REVISION}"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ModelCacheStatus:
    available: bool
    reason_code: str
    model_path: Path | None = None


def _unavailable(reason: str) -> ModelCacheStatus:
    return ModelCacheStatus(False, reason)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_sensevoice_cache(
    cache_root: Path | None,
    manifest_path: Path | None,
) -> ModelCacheStatus:
    """Validate an offline model tree without importing engines or using network."""
    if cache_root is None or manifest_path is None:
        return _unavailable("model-cache-unconfigured")
    try:
        root = cache_root.resolve(strict=True)
        manifest = manifest_path.resolve(strict=True)
        if not root.is_dir() or root not in manifest.parents or not manifest.is_file():
            return _unavailable("model-manifest-outside-cache")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if type(data) is not dict or set(data) != {
            "schema_version",
            "model_id",
            "model_revision",
            "model_path",
            "files",
        }:
            return _unavailable("model-manifest-invalid")
        if data["schema_version"] != MODEL_MANIFEST_VERSION:
            return _unavailable("model-manifest-version-mismatch")
        if (
            data["model_id"] != SENSEVOICE_MODEL_ID
            or data["model_revision"] != SENSEVOICE_REVISION
        ):
            return _unavailable("model-identity-mismatch")
        if data["model_path"] != SENSEVOICE_RELATIVE_PATH:
            return _unavailable("model-path-invalid")
        model_path = (root / Path(*PurePosixPath(data["model_path"]).parts)).resolve(
            strict=True
        )
        if (
            root not in model_path.parents
            or not model_path.is_dir()
            or manifest.parent != model_path
        ):
            return _unavailable("model-path-invalid")
        files = data["files"]
        if type(files) is not list or not files:
            return _unavailable("model-manifest-invalid")
        seen: set[str] = set()
        for entry in files:
            if type(entry) is not dict or set(entry) != {
                "path",
                "size_bytes",
                "sha256",
            }:
                return _unavailable("model-manifest-invalid")
            rel = entry["path"]
            size = entry["size_bytes"]
            expected_digest = entry["sha256"]
            if (
                type(rel) is not str
                or not rel
                or rel in seen
                or type(size) is not int
                or isinstance(size, bool)
                or size < 0
                or type(expected_digest) is not str
                or _SHA256_RE.fullmatch(expected_digest) is None
            ):
                return _unavailable("model-manifest-invalid")
            pure_rel = PurePosixPath(rel)
            if (
                "\\" in rel
                or pure_rel.is_absolute()
                or any(part in {".", ".."} for part in pure_rel.parts)
                or str(pure_rel) != rel
            ):
                return _unavailable("model-manifest-invalid")
            seen.add(rel)
            target = (model_path / Path(*pure_rel.parts)).resolve(strict=True)
            if (
                model_path not in target.parents
                or target.is_symlink()
                or not target.is_file()
                or target.stat().st_size != size
                or _sha256(target) != expected_digest
            ):
                return _unavailable("model-file-mismatch")
        actual_files: set[str] = set()
        for target in model_path.rglob("*"):
            if target.is_symlink():
                return _unavailable("model-file-mismatch")
            if target.is_file() and target != manifest:
                actual_files.add(target.relative_to(model_path).as_posix())
        if actual_files != seen:
            return _unavailable("model-file-mismatch")
        return ModelCacheStatus(True, "available", model_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return _unavailable("model-cache-unavailable")
