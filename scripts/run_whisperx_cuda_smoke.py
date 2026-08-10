from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import time
import uuid
import wave
from pathlib import Path

ASR_MODEL_ID = "Systran/faster-whisper-large-v3"
ASR_REVISION = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"
ALIGN_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
ALIGN_REVISION = "51d27579a1040ee4e967979278d5f76b9c32c375"
ASR_RELATIVE_PATH = f"whisper-large-v3/{ASR_REVISION}"
ALIGN_RELATIVE_PATH = (
    f"wav2vec2-large-xlsr-53-chinese-zh-cn/{ALIGN_REVISION}"
)
MODEL_DOWNLOAD_ATTEMPTS = 3
MODEL_DOWNLOAD_RETRY_SECONDS = 2


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest(
    root: Path,
    model_path: Path,
    *,
    model_id: str,
    revision: str,
    relative_path: str,
) -> None:
    files = []
    for path in sorted(model_path.rglob("*")):
        if not path.is_file() or path.name == "model-manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(model_path).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files:
        raise RuntimeError(f"empty model cache: {model_id}")
    payload = {
        "schema_version": "asr-model-manifest/1",
        "model_id": model_id,
        "model_revision": revision,
        "model_path": relative_path,
        "files": files,
    }
    (model_path / "model-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_smoke_punkt(nltk_root: Path) -> None:
    from nltk.tokenize.punkt import PunktParameters, save_punkt_params

    punkt_dir = nltk_root / "tokenizers" / "punkt_tab" / "english"
    punkt_dir.mkdir(parents=True, exist_ok=True)
    save_punkt_params(PunktParameters(), dir=str(punkt_dir))


def _hugging_face_backend():
    import requests

    class TLS12HTTPAdapter(requests.adapters.HTTPAdapter):
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
            super().__init__(*args, **kwargs)

        def init_poolmanager(
            self,
            connections: int,
            maxsize: int,
            block: bool = requests.adapters.DEFAULT_POOLBLOCK,
            **pool_kwargs: object,
        ) -> None:
            pool_kwargs["ssl_context"] = self._ssl_context
            super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

        def proxy_manager_for(self, proxy: str, **proxy_kwargs: object):
            proxy_kwargs["ssl_context"] = self._ssl_context
            return super().proxy_manager_for(proxy, **proxy_kwargs)

        def build_connection_pool_key_attributes(
            self,
            request: requests.PreparedRequest,
            verify: object,
            cert: object = None,
        ) -> tuple[dict[str, object], dict[str, object]]:
            if verify is not True:
                raise RuntimeError("Hugging Face download requires certificate verification")
            host_params, pool_kwargs = super().build_connection_pool_key_attributes(
                request, verify, cert
            )
            pool_kwargs["ssl_context"] = self._ssl_context
            return host_params, pool_kwargs

    session = requests.Session()
    session.mount("https://", TLS12HTTPAdapter())
    return session


def _download_model(**kwargs: object) -> str:
    import requests
    from huggingface_hub import configure_http_backend, snapshot_download

    os.environ["HF_HUB_DISABLE_XET"] = "1"
    configure_http_backend(backend_factory=_hugging_face_backend)
    kwargs.setdefault("max_workers", 1)
    for attempt in range(1, MODEL_DOWNLOAD_ATTEMPTS + 1):
        try:
            return snapshot_download(**kwargs)
        except requests.exceptions.SSLError:
            if attempt == MODEL_DOWNLOAD_ATTEMPTS:
                raise
            time.sleep(MODEL_DOWNLOAD_RETRY_SECONDS * attempt)
    raise AssertionError("model download retry loop exhausted")


def _prepare_model(
    root: Path,
    staging: Path,
    *,
    label: str,
    model_id: str,
    revision: str,
    relative_path: str,
    allow_patterns: list[str],
) -> None:
    from asr_service.model_cache import (
        validate_whisperx_align_cache,
        validate_whisperx_cache,
    )

    target = root / relative_path
    manifest = target / "model-manifest.json"
    validator = (
        validate_whisperx_cache if label == "asr" else validate_whisperx_align_cache
    )
    if target.exists():
        if not validator(root, manifest).available:
            raise RuntimeError(f"existing {label} model cache is invalid")
        return

    staged_root = staging / label
    staged_model = staged_root / relative_path
    _download_model(
        repo_id=model_id,
        revision=revision,
        local_dir=staged_model,
        local_dir_use_symlinks=False,
        allow_patterns=allow_patterns,
        max_workers=1,
    )
    _write_manifest(
        staged_root,
        staged_model,
        model_id=model_id,
        revision=revision,
        relative_path=relative_path,
    )
    if not validator(staged_root, staged_model / "model-manifest.json").available:
        raise RuntimeError(f"staged {label} model cache validation failed")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_model, target)


def _model_preparation_diagnostic(error: Exception) -> dict[str, str]:
    message = str(error).lower()
    model = "asr" if "asr" in message else "aligner" if "align" in message else "unknown"
    if "existing" in message and "cache is invalid" in message:
        kind = "existing_cache_invalid"
    elif any(marker in message for marker in ("ssl", "huggingface", "connection", "proxy", "timeout")):
        kind = "snapshot_download_failed"
    elif isinstance(error, PermissionError):
        kind = "filesystem_or_permission_failure"
    else:
        kind = "evidence_insufficient"
    return {
        "schema_version": "whisperx-model-preparation-failure/1",
        "status": "fail",
        "stage": "model_preparation",
        "kind": kind,
        "model": model,
        "exception_type": type(error).__name__,
    }


def prepare_models(root: Path, nltk_root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    staging = root / ".staging" / uuid.uuid4().hex
    _prepare_model(
        root,
        staging,
        label="asr",
        model_id=ASR_MODEL_ID,
        revision=ASR_REVISION,
        relative_path=ASR_RELATIVE_PATH,
        allow_patterns=[
            "config.json",
            "model.bin",
            "preprocessor_config.json",
            "tokenizer.json",
            "vocabulary.json",
        ],
    )
    _prepare_model(
        root,
        staging,
        label="aligner",
        model_id=ALIGN_MODEL_ID,
        revision=ALIGN_REVISION,
        relative_path=ALIGN_RELATIVE_PATH,
        allow_patterns=["*.json", "*.bin", "*.safetensors", "*.txt", "*.model"],
    )
    prepare_smoke_punkt(nltk_root)


def smoke(source_root: Path, model_root: Path, nltk_root: Path, wav_path: Path) -> dict[str, object]:
    os.environ["NLTK_DATA"] = str(nltk_root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"

    import ctranslate2
    import torch

    from asr_service.engine_protocol import PreparedAudioChunk, WHISPERX_SERVICE_CONFIG
    from asr_service.engines.whisperx import WhisperXEngine
    from asr_service.model_cache import (
        validate_whisperx_align_cache,
        validate_whisperx_cache,
    )
    from src.transcription.normalizer import normalize_candidate
    from src.transcription.profile import ProfileSnapshot, TranscriptionExecutionConfig
    from src.transcription.profile_catalog import WHISPERX_PROFILE_ID, build_phase3_profile_catalog
    from src.transcription.provider_protocol import ProviderCandidate, ProviderFailure
    from src.transcription.types import TranscriptionInputRef

    if not torch.cuda.is_available():
        raise RuntimeError("torch CUDA unavailable")
    if ctranslate2.get_cuda_device_count() != 1:
        raise RuntimeError("unexpected CTranslate2 CUDA device count")
    if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
        raise RuntimeError("CTranslate2 FP16 unavailable")

    content = wav_path.read_bytes()
    with wave.open(str(wav_path), "rb") as handle:
        duration_ms = round(handle.getnframes() * 1000 / handle.getframerate())
    input_ref = TranscriptionInputRef(
        str(uuid.uuid5(uuid.NAMESPACE_URL, "ragpincheng:whisperx:r3:synthetic")),
        "audio",
        hashlib.sha256(content).hexdigest(),
        len(content),
        duration_ms,
    )
    profile = next(
        entry.profile
        for entry in build_phase3_profile_catalog()
        if entry.profile.profile_id == WHISPERX_PROFILE_ID
    )
    execution = TranscriptionExecutionConfig.create(
        profile, input_ref, language="zh-CN", timeout_ms=600_000
    )
    snapshot = ProfileSnapshot.create(profile, execution)
    asr_path = model_root / ASR_RELATIVE_PATH
    align_path = model_root / ALIGN_RELATIVE_PATH
    asr_cache = validate_whisperx_cache(
        model_root, asr_path / "model-manifest.json"
    )
    align_cache = validate_whisperx_align_cache(
        model_root, align_path / "model-manifest.json"
    )
    if not asr_cache.available or not align_cache.available:
        raise RuntimeError(
            f"model cache validation failed: {asr_cache.reason_code}/"
            f"{align_cache.reason_code}"
        )
    engine = WhisperXEngine(
        model_cache_ready=lambda: asr_cache.available and align_cache.available,
        model_path=asr_cache.model_path,
        align_model_path=align_cache.model_path,
    )
    result = engine.transcribe_chunk(
        PreparedAudioChunk(0, 0, duration_ms, content),
        WHISPERX_SERVICE_CONFIG,
    )
    if type(result) is ProviderFailure:
        stage = engine.last_failure_stage or "contract"
        failure_type = engine.last_failure_type or "ProviderFailure"
        raise RuntimeError(
            f"engine failure: {result.error_code.value}; "
            f"stage={stage}; type={failure_type}"
        )
    candidate = ProviderCandidate(
        result.provider_key,
        result.language,
        result.duration_ms,
        result.segments,
        result.artifact_refs,
    )
    canonical = normalize_candidate(input_ref, candidate, snapshot, execution)
    return {
        "schema_version": "whisperx-cuda-smoke/1",
        "status": "pass",
        "provider_key": candidate.provider_key,
        "profile_id": profile.profile_id,
        "asr_model_id": ASR_MODEL_ID,
        "asr_model_revision": ASR_REVISION,
        "align_model_id": ALIGN_MODEL_ID,
        "align_model_revision": ALIGN_REVISION,
        "punkt_source": "generated-default-smoke-only",
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "gpu_name": torch.cuda.get_device_name(0),
        "ctranslate2_version": ctranslate2.__version__,
        "segment_count": len(canonical.segments),
        "canonical_sha256": canonical.content_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--nltk-root", type=Path, required=True)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--model-preparation-diagnostic", type=Path)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        try:
            prepare_models(args.model_root, args.nltk_root)
        except Exception as error:
            if args.model_preparation_diagnostic is not None:
                args.model_preparation_diagnostic.parent.mkdir(parents=True, exist_ok=True)
                args.model_preparation_diagnostic.write_text(
                    json.dumps(_model_preparation_diagnostic(error), sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            raise
        return 0
    if args.wav is None or args.report is None:
        parser.error("--wav and --report are required for smoke")
    payload = smoke(
        args.source_root.resolve(),
        args.model_root.resolve(),
        args.nltk_root.resolve(),
        args.wav.resolve(),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "pass", "segment_count": payload["segment_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
