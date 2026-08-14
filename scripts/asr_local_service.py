"""Loopback-only ASR service factory for the local development lab."""
from __future__ import annotations

import os
from pathlib import Path

from services.asr_service.app import create_app
from services.asr_service.config import AsrServiceSettings
from services.asr_service.engine_protocol import (
    QWEN3_ASR_SERVICE_CONFIG,
    WHISPERX_FULL_DECODE_SERVICE_CONFIG,
    WHISPERX_SERVICE_CONFIG,
)
from services.asr_service.engine_registry import EngineRegistration, EngineRegistry
from services.asr_service.engines.qwen3_asr import Qwen3AsrEngine
from services.asr_service.engines.whisperx import WhisperXEngine
from services.asr_service.model_cache import (
    validate_qwen3_aligner_cache,
    validate_qwen3_asr_cache,
    validate_whisperx_align_cache,
    validate_whisperx_cache,
)
from services.asr_service.scheduler import (
    BgePriorityDecision,
    FixedBgePriorityProbe,
    Scheduler,
)
from services.asr_service.storage import LocalJobRepository
from scripts.asr_local_lab import (
    ENGINE_PORTS,
    MARKER_NAME,
    ensure_unprivileged,
    load_marker,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _settings(
    lab_root: Path,
    token: str,
    port: int,
    engine_name: str,
    model_root: Path,
    asr_manifest: Path,
    align_manifest: Path,
) -> AsrServiceSettings:
    optional = {}
    if engine_name == "qwen3-asr":
        optional = {
            "qwen3_asr_model_cache_root": model_root,
            "qwen3_asr_model_manifest_path": asr_manifest,
            "qwen3_aligner_model_cache_root": model_root,
            "qwen3_aligner_model_manifest_path": align_manifest,
        }
    else:
        optional = {
            "whisperx_model_cache_root": model_root,
            "whisperx_model_manifest_path": asr_manifest,
            "whisperx_align_model_cache_root": model_root,
            "whisperx_align_model_manifest_path": align_manifest,
        }
    return AsrServiceSettings(
        enabled=True,
        token=token,
        host="127.0.0.1",
        port=port,
        spool_root=lab_root / "runs/service-spool" / str(port),
        max_input_bytes=2 * 1024**3,
        max_upload_part_bytes=8 * 1024**2,
        max_queue_length=2,
        chunk_duration_ms=300_000,
        consecutive_failure_limit=3,
        # The injected Scheduler uses FixedBgePriorityProbe. This URL is only
        # present so the production settings invariant remains unchanged.
        bge_priority_probe_url="http://127.0.0.1:9/v1/activity",
        bge_priority_probe_token="local-not-used",
        **optional,
    )


def create_local_app():
    ensure_unprivileged()
    lab_root = Path(_required("ASR_LOCAL_LAB_ROOT")).resolve()
    source_root = Path(_required("ASR_LOCAL_SOURCE_ROOT")).resolve()
    load_marker(lab_root, source_root)
    if not (lab_root / MARKER_NAME).is_file():
        raise RuntimeError("local lab marker is unavailable")
    engine_name = _required("ASR_LOCAL_ENGINE")
    token = _required("ASR_LOCAL_TOKEN")
    port = int(_required("ASR_LOCAL_PORT"))
    if engine_name not in ENGINE_PORTS or port != ENGINE_PORTS[engine_name]:
        raise RuntimeError("invalid local engine port")

    if engine_name == "qwen3-asr":
        model_root = lab_root / "models/qwen3-asr"
        asr_manifest = (
            model_root
            / "Qwen3-ASR-0.6B"
            / "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
            / "model-manifest.json"
        )
        align_manifest = (
            model_root
            / "Qwen3-ForcedAligner-0.6B"
            / "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
            / "model-manifest.json"
        )
        asr = validate_qwen3_asr_cache(
            model_root,
            asr_manifest,
        )
        align = validate_qwen3_aligner_cache(
            model_root,
            align_manifest,
        )
        if not asr.available or not align.available:
            raise RuntimeError("local Qwen model cache unavailable")
        engine = Qwen3AsrEngine(
            model_cache_ready=lambda: True,
            asr_model_path=asr.model_path,
            aligner_model_path=align.model_path,
            language_policy=_required("ASR_LOCAL_QWEN_LANGUAGE_POLICY"),
            timing_diagnostics=True,
        )
        config = QWEN3_ASR_SERVICE_CONFIG
    else:
        from scripts.run_whisperx_cuda_smoke import (
            ALIGN_RELATIVE_PATH,
            ASR_RELATIVE_PATH,
        )
        model_root = lab_root / "models/whisperx"
        asr_manifest = model_root / ASR_RELATIVE_PATH / "model-manifest.json"
        align_manifest = model_root / ALIGN_RELATIVE_PATH / "model-manifest.json"
        asr = validate_whisperx_cache(
            model_root, asr_manifest
        )
        align = validate_whisperx_align_cache(
            model_root, align_manifest
        )
        if not asr.available or not align.available:
            raise RuntimeError("local WhisperX model cache unavailable")
        engine = WhisperXEngine(
            model_cache_ready=lambda: True,
            model_path=asr.model_path,
            align_model_path=align.model_path,
        )
        config = (
            WHISPERX_FULL_DECODE_SERVICE_CONFIG
            if os.environ.get("ASR_LOCAL_WHISPERX_CANDIDATE") == "full-decode"
            else WHISPERX_SERVICE_CONFIG
        )

    settings = _settings(
        lab_root,
        token,
        port,
        engine_name,
        model_root,
        asr_manifest,
        align_manifest,
    )
    repo = LocalJobRepository(
        settings.spool_root,
        settings.max_input_bytes,
        settings.max_upload_part_bytes,
    )
    scheduler = Scheduler(
        repo,
        EngineRegistry((EngineRegistration(engine, config),)),
        FixedBgePriorityProbe(BgePriorityDecision.allow),
        queue_limit=settings.max_queue_length,
        failure_limit=settings.consecutive_failure_limit,
        enabled=True,
    )
    return create_app(settings, scheduler)
