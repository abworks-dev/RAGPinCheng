"""Prepare the pinned faster-whisper model as a strict offline cache.

The production entry point is the PowerShell qualification orchestrator.  This
module deliberately has no configurable model identity: callers may choose only
the staging and cache roots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from asr_service.model_cache import (
    FASTER_WHISPER_MODEL_ID,
    FASTER_WHISPER_RELATIVE_PATH,
    FASTER_WHISPER_REVISION,
    MODEL_MANIFEST_VERSION,
    validate_faster_whisper_cache,
)

MODEL_BIN_SIZE_BYTES = 1_617_884_929
MODEL_BIN_SHA256 = (
    "e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31"
)
MANIFEST_NAME = "model-manifest.json"
FIXED_MODEL_FILES = (
    ".gitattributes",
    "README.md",
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
MAX_REDIRECTS = 5


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


def _assert_download_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not (
            host == "huggingface.co"
            or host.endswith(".huggingface.co")
            or host == "hf.co"
            or host.endswith(".hf.co")
        )
    ):
        raise RuntimeError("model download escaped approved Hugging Face HTTPS hosts")


def _request_with_redirects(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
) -> requests.Response:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _assert_download_url(current)
        response = session.get(
            current,
            headers=headers,
            stream=True,
            allow_redirects=False,
            timeout=(10, 120),
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("model download redirect omitted Location")
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("model download exceeded redirect limit")


def _partial_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.partial")


def _download_fixed_file(
    session: requests.Session,
    *,
    url: str,
    destination: Path,
) -> None:
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise RuntimeError("download staging contains an invalid completed file")
        return

    partial = _partial_path(destination)
    if partial.exists() and (partial.is_symlink() or not partial.is_file()):
        raise RuntimeError("download staging contains an invalid partial file")
    offset = partial.stat().st_size if partial.exists() else 0

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            response = _request_with_redirects(session, url, headers=headers)
            try:
                append = offset > 0 and response.status_code == 206
                if offset > 0 and response.status_code not in {200, 206}:
                    raise RuntimeError("model download returned an invalid resume status")
                if append:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError(
                            "model download returned an invalid Content-Range"
                        )
                else:
                    offset = 0
                mode = "ab" if append else "wb"
                with partial.open(mode) as handle:
                    for chunk in response.iter_content(
                        chunk_size=DOWNLOAD_CHUNK_BYTES
                    ):
                        if chunk:
                            handle.write(chunk)
                expected_length = response.headers.get("Content-Length")
                if expected_length is not None:
                    received = partial.stat().st_size - offset
                    if received != int(expected_length):
                        raise RuntimeError("model download response length mismatch")
                os.replace(partial, destination)
                return
            finally:
                response.close()
        except (OSError, requests.RequestException, RuntimeError):
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            offset = partial.stat().st_size if partial.exists() else 0
    raise RuntimeError("model download attempts exhausted")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_root(path: Path, field: str, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    resolved = path.resolve(strict=must_exist)
    if resolved == Path(resolved.anchor):
        raise RuntimeError(f"{field} must not be a drive root")
    return resolved


def _assert_regular_tree(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for item in root.rglob("*"):
        if item.is_symlink():
            raise RuntimeError("model tree contains a symbolic link")
        if item.is_dir():
            continue
        if not item.is_file():
            raise RuntimeError("model tree contains a non-regular file")
        files.append(item)
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def _model_files(download_root: Path) -> tuple[Path, ...]:
    all_files = _assert_regular_tree(download_root)
    selected = tuple(
        item
        for item in all_files
        if item.relative_to(download_root).parts[0] != ".cache"
    )
    if not selected:
        raise RuntimeError("downloaded model tree is empty")
    if any(item.name == MANIFEST_NAME for item in selected):
        raise RuntimeError("downloaded model unexpectedly contains model-manifest.json")
    return selected


def _manifest_entries(model_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for item in _assert_regular_tree(model_root):
        if item.name == MANIFEST_NAME:
            continue
        relative = item.relative_to(model_root).as_posix()
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise RuntimeError("model file has an unsafe relative path")
        entries.append(
            {
                "path": relative,
                "size_bytes": item.stat().st_size,
                "sha256": _sha256(item),
            }
        )
    if not entries:
        raise RuntimeError("prepared model tree is empty")
    return entries


def _assert_model_bin(model_root: Path) -> None:
    model_bin = model_root / "model.bin"
    if (
        not model_bin.is_file()
        or model_bin.is_symlink()
        or model_bin.stat().st_size != MODEL_BIN_SIZE_BYTES
        or _sha256(model_bin) != MODEL_BIN_SHA256
    ):
        raise RuntimeError("model.bin identity mismatch")


def _write_manifest(model_root: Path) -> Path:
    manifest = model_root / MANIFEST_NAME
    payload = {
        "schema_version": MODEL_MANIFEST_VERSION,
        "model_id": FASTER_WHISPER_MODEL_ID,
        "model_revision": FASTER_WHISPER_REVISION,
        "model_path": FASTER_WHISPER_RELATIVE_PATH,
        "files": _manifest_entries(model_root),
    }
    temporary = manifest.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, manifest)
    return manifest


def _default_downloader(**kwargs: object) -> str:
    expected = {"repo_id", "revision", "local_dir"}
    if set(kwargs) != expected:
        raise RuntimeError("model downloader received unexpected arguments")
    if (
        kwargs["repo_id"] != FASTER_WHISPER_MODEL_ID
        or kwargs["revision"] != FASTER_WHISPER_REVISION
    ):
        raise RuntimeError("model downloader identity mismatch")
    download_root = _strict_root(
        Path(str(kwargs["local_dir"])), "local_dir", must_exist=True
    )
    allowed_names = set(FIXED_MODEL_FILES)
    for item in download_root.iterdir():
        if item.name not in allowed_names and not (
            item.name.endswith(".partial")
            and item.name.removesuffix(".partial") in allowed_names
        ):
            raise RuntimeError("download staging contains an unexpected entry")
        if item.is_symlink() or not item.is_file():
            raise RuntimeError("download staging contains a non-regular entry")

    session = _hugging_face_backend()
    try:
        for filename in FIXED_MODEL_FILES:
            encoded = quote(filename, safe="")
            url = (
                "https://huggingface.co/"
                f"{FASTER_WHISPER_MODEL_ID}/resolve/"
                f"{FASTER_WHISPER_REVISION}/{encoded}"
            )
            _download_fixed_file(
                session,
                url=url,
                destination=download_root / filename,
            )
        completed = {
            item.name
            for item in download_root.iterdir()
            if item.is_file() and not item.is_symlink()
        }
        if completed != set(FIXED_MODEL_FILES):
            raise RuntimeError("fixed model download file set mismatch")
    finally:
        session.close()
    return str(download_root)


def validate_local_model(cache_root: Path) -> dict[str, object]:
    cache = _strict_root(cache_root, "cache_root", must_exist=False)
    target = cache / Path(*PurePosixPath(FASTER_WHISPER_RELATIVE_PATH).parts)
    target_manifest = target / MANIFEST_NAME
    status = validate_faster_whisper_cache(cache, target_manifest)
    if not status.available:
        raise RuntimeError(
            f"local model artifact is unavailable: {status.reason_code}"
        )
    _assert_model_bin(target)
    return {
        "schema_version": "faster-whisper-model-preparation/1",
        "status": "validated-offline",
        "model_id": FASTER_WHISPER_MODEL_ID,
        "model_revision": FASTER_WHISPER_REVISION,
        "model_path": str(target),
        "manifest_path": str(target_manifest),
        "manifest_sha256": _sha256(target_manifest),
    }


def prepare_model(
    cache_root: Path,
    staging_root: Path,
    *,
    downloader: Callable[..., str] = _default_downloader,
) -> dict[str, object]:
    cache = _strict_root(cache_root, "cache_root", must_exist=False)
    staging = _strict_root(staging_root, "staging_root", must_exist=False)
    target = cache / Path(*PurePosixPath(FASTER_WHISPER_RELATIVE_PATH).parts)
    target_manifest = target / MANIFEST_NAME

    if target.exists():
        result = validate_local_model(cache)
        result["status"] = "reused"
        return result

    if not staging.exists():
        staging.mkdir(parents=True)
    else:
        staging_entries = tuple(staging.iterdir())
        if any(item.name != "download" for item in staging_entries):
            raise RuntimeError("staging_root contains an unexpected entry")
        if staging_entries and (
            staging_entries[0].is_symlink() or not staging_entries[0].is_dir()
        ):
            raise RuntimeError("staging download root is invalid")

    download_root = staging / "download"
    download_root.mkdir(exist_ok=True)
    returned = Path(
        downloader(
            repo_id=FASTER_WHISPER_MODEL_ID,
            revision=FASTER_WHISPER_REVISION,
            local_dir=str(download_root),
        )
    ).resolve(strict=True)
    if returned != download_root.resolve(strict=True):
        raise RuntimeError("downloader returned a path outside the fixed staging root")

    candidate_cache = staging / "candidate-cache"
    candidate_model = candidate_cache / Path(
        *PurePosixPath(FASTER_WHISPER_RELATIVE_PATH).parts
    )
    candidate_model.mkdir(parents=True)
    for source in _model_files(download_root):
        relative = source.relative_to(download_root)
        destination = candidate_model / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)

    _assert_model_bin(candidate_model)
    candidate_manifest = _write_manifest(candidate_model)
    candidate_status = validate_faster_whisper_cache(
        candidate_cache, candidate_manifest
    )
    if not candidate_status.available:
        raise RuntimeError(
            f"staged model cache validation failed: {candidate_status.reason_code}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError("final model cache appeared during preparation")
    os.replace(candidate_model, target)
    final_status = validate_faster_whisper_cache(cache, target_manifest)
    if not final_status.available:
        raise RuntimeError(
            f"promoted model cache validation failed: {final_status.reason_code}"
        )
    _assert_model_bin(target)
    return {
        "schema_version": "faster-whisper-model-preparation/1",
        "status": "prepared",
        "model_id": FASTER_WHISPER_MODEL_ID,
        "model_revision": FASTER_WHISPER_REVISION,
        "model_path": str(target),
        "manifest_path": str(target_manifest),
        "manifest_sha256": _sha256(target_manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--report-path", type=Path, required=True)
    args = parser.parse_args()
    if args.offline_only:
        if args.staging_root is not None:
            parser.error("--staging-root is not accepted with --offline-only")
        result = validate_local_model(args.cache_root)
    else:
        if args.staging_root is None:
            parser.error("--staging-root is required unless --offline-only is set")
        result = prepare_model(args.cache_root, args.staging_root)
    report = _strict_root(args.report_path.parent, "report_parent", must_exist=True)
    report_path = (report / args.report_path.name).resolve()
    if report_path.parent != report:
        raise RuntimeError("report_path escapes report_parent")
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "model_id": result["model_id"],
                "model_revision": result["model_revision"],
                "manifest_sha256": result["manifest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
