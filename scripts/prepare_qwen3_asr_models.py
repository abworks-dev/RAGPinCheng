"""Prepare the two immutable Qwen3-ASR snapshots as strict offline caches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

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
from scripts.asr_model_download import (
    HF_ORIGIN_IP_ENV,
    assert_no_reparse_components,
    curl_snapshot_download,
    exclusive_staging_lock,
    hugging_face_origin_override,
)

MANIFEST_NAME = "model-manifest.json"
MODEL_DOWNLOAD_ATTEMPTS = 3
MODEL_DOWNLOAD_RETRY_SECONDS = 2


class _TLS12HTTPAdapter(requests.adapters.HTTPAdapter):
    """Keep Hugging Face HTTPS compatible with the production proxy."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._tls12_context = self._create_ssl_context()
        super().__init__(*args, **kwargs)

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        return context

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = requests.adapters.DEFAULT_POOLBLOCK,
        **pool_kwargs: object,
    ) -> None:
        pool_kwargs["ssl_context"] = self._tls12_context
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def proxy_manager_for(
        self, proxy: str, **proxy_kwargs: object
    ) -> requests.packages.urllib3.ProxyManager:
        proxy_kwargs["ssl_context"] = self._tls12_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)

    def build_connection_pool_key_attributes(
        self,
        request: requests.PreparedRequest,
        verify: object,
        cert: object = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if verify is not True:
            raise RuntimeError(
                "Hugging Face model download requires default certificate verification"
            )
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        pool_kwargs["ssl_context"] = self._tls12_context
        return host_params, pool_kwargs


def _hugging_face_backend() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _TLS12HTTPAdapter())
    return session


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

MODEL_FAILURE_KINDS = frozenset(
    {
        "existing_cache_invalid",
        "staging_validation_failed",
        "snapshot_download_failed",
        "filesystem_or_permission_failure",
        "disk_space_failure",
        "evidence_insufficient",
    }
)


def classify_model_preparation_failure(error: Exception) -> dict[str, object]:
    message = str(error).lower()
    model = "unknown"
    if "aligner" in message:
        model = "aligner"
    elif "asr" in message:
        model = "asr"

    kind = "evidence_insufficient"
    if "existing " in message and " cache is invalid" in message:
        kind = "existing_cache_invalid"
    elif any(
        marker in message
        for marker in (
            "staged ",
            "promoted ",
            "model tree is empty",
            "unsafe model path",
            "symbolic link",
            "downloader escaped fixed staging",
        )
    ):
        kind = "staging_validation_failed"
    elif isinstance(error, OSError) and getattr(error, "errno", None) == 28:
        kind = "disk_space_failure"
    elif isinstance(error, PermissionError) or any(
        marker in message for marker in ("permission denied", "access is denied")
    ):
        kind = "filesystem_or_permission_failure"
    elif any(
        marker in message
        for marker in (
            "snapshot_download",
            "huggingface",
            "http error",
            "client error",
            "connection",
            "proxy",
            "timeout",
            "timed out",
            "ssl",
        )
    ):
        kind = "snapshot_download_failed"

    return {
        "schema_version": "qwen3-asr-model-preparation-failure/1",
        "status": "fail",
        "stage": "model_preparation",
        "kind": kind,
        "model": model,
        "exception_type": type(error).__name__,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_root(path: Path, label: str, *, exists: bool) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be absolute")
    assert_no_reparse_components(path, label)
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
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if os.environ.get(HF_ORIGIN_IP_ENV, "").strip():
        return curl_snapshot_download(**kwargs)
    from huggingface_hub import configure_http_backend, snapshot_download

    configure_http_backend(backend_factory=_hugging_face_backend)
    kwargs.setdefault("max_workers", 1)
    with hugging_face_origin_override():
        for attempt in range(1, MODEL_DOWNLOAD_ATTEMPTS + 1):
            try:
                return snapshot_download(**kwargs)
            except requests.exceptions.SSLError:
                if attempt == MODEL_DOWNLOAD_ATTEMPTS:
                    raise
                time.sleep(MODEL_DOWNLOAD_RETRY_SECONDS * attempt)
    raise AssertionError("model download retry loop exhausted")


def _prepare_one(
    cache: Path,
    staging: Path,
    spec: ModelSpec,
    downloader: Callable[..., str],
) -> dict[str, str]:
    target = cache / Path(*PurePosixPath(spec.relative_path).parts)
    assert_no_reparse_components(target, f"{spec.label} model target")
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

    candidate_cache = staging / spec.label / "candidate-cache"
    candidate = candidate_cache / Path(*PurePosixPath(spec.relative_path).parts)
    candidate.mkdir(parents=True, exist_ok=True)
    returned = Path(
        downloader(
            repo_id=spec.model_id,
            revision=spec.revision,
            local_dir=str(candidate),
            local_dir_use_symlinks=False,
        )
    ).resolve(strict=True)
    if returned != candidate.resolve(strict=True):
        raise RuntimeError("downloader escaped fixed staging")
    metadata_cache = candidate / ".cache"
    if metadata_cache.exists():
        shutil.rmtree(metadata_cache)
    manifest = _manifest(candidate, spec)
    if not getattr(spec.validator(candidate_cache, manifest), "available", False):
        raise RuntimeError(f"staged {spec.label} cache validation failed")
    target.parent.mkdir(parents=True, exist_ok=True)
    assert_no_reparse_components(target.parent, f"{spec.label} model target")
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
    staging.mkdir(parents=True, exist_ok=True)
    allowed = {".prepare.lock", *(spec.label for spec in SPECS)}
    unexpected = sorted(item.name for item in staging.iterdir() if item.name not in allowed)
    if unexpected:
        raise RuntimeError("staging_root contains unexpected entries")
    with exclusive_staging_lock(staging):
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
    try:
        result = prepare_models(args.cache_root, args.staging_root)
    except Exception as error:
        print(
            json.dumps(
                classify_model_preparation_failure(error),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
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
