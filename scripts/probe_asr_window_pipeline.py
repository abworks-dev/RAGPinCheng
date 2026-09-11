"""Read-only probe: reproduce the production ASR window path and report the engine stage.

Run inside the deployed release venv. It prints a sanitized JSON report with
audio-path statistics and, when GPU memory allows, the WhisperX engine outcome
for the exact window that production failed on. It never mutates the service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def load_env(config_path: Path) -> None:
    if not config_path.is_file():
        return
    for line in config_path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def reconstruct_content(job_dir: Path) -> bytes:
    manifest = json.loads((job_dir / "upload-manifest.json").read_text(encoding="utf-8"))
    chunks: list[bytes] = []
    for record in manifest["parts"]:
        part = job_dir / "parts" / f"{int(record['part_number']):08d}.part"
        chunks.append(part.read_bytes())
    return b"".join(chunks)


def free_gpu_mib() -> int | None:
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.free // (1024 * 1024))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--window-start-ms", type=int, required=True)
    parser.add_argument("--window-end-ms", type=int, required=True)
    parser.add_argument("--profile-id", default="whisperx-large-v3-zh-align-v2")
    parser.add_argument("--report", required=True)
    parser.add_argument("--min-free-gpu-mib", type=int, default=6144)
    args = parser.parse_args()

    app_root = Path(args.app_root)
    config_path = Path(args.config_path)
    job_dir = Path(args.job_dir)
    load_env(config_path)
    sys.path.insert(0, str(app_root))

    report: dict[str, object] = {
        "schema_version": "asr-window-probe/1",
        "read_only": True,
        "production_services_modified": False,
        "app_root": str(app_root),
        "job_dir": job_dir.name,
        "window_start_ms": args.window_start_ms,
        "window_end_ms": args.window_end_ms,
        "profile_id": args.profile_id,
    }

    import numpy as np

    from services.asr_service import audio as audio_module

    content = reconstruct_content(job_dir)
    report["content_bytes"] = len(content)
    report["content_sha256"] = hashlib.sha256(content).hexdigest()

    samples = audio_module.decode_audio_samples(content)
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    report["decoded_samples"] = int(array.size)
    report["decoded_seconds"] = round(float(array.size) / 16000.0, 3)

    window = audio_module.encode_wav_window(
        array, start_ms=args.window_start_ms, end_ms=args.window_end_ms
    )
    report["window_bytes"] = len(window)
    report["window_header_ok"] = window[:4] == b"RIFF" and window[8:12] == b"WAVE"

    import wave
    from io import BytesIO

    with wave.open(BytesIO(window), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        report["window_samples"] = int(handle.getnframes())
        report["window_channels"] = handle.getnchannels()
        report["window_rate"] = handle.getframerate()
        report["window_sampwidth"] = handle.getsampwidth()
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    report["window_abs_mean"] = round(float(np.abs(pcm).mean()), 6)
    report["window_abs_max"] = round(float(np.abs(pcm).max()), 6)
    report["window_nonzero_ratio"] = round(float((pcm != 0).mean()), 6)

    free_mib = free_gpu_mib()
    report["gpu_free_mib"] = free_mib
    if free_mib is not None and free_mib < args.min_free_gpu_mib:
        report["engine_probe"] = "skipped-insufficient-gpu-memory"
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0

    from src.transcription.candidate import CandidateSegment  # noqa: F401
    from src.transcription.provider_protocol import ProviderFailure
    from src.transcription.service_profiles import WHISPERX_V2_SERVICE_CONFIG
    from services.asr_service.engine_protocol import PreparedAudioChunk
    from services.asr_service.engines.whisperx import WhisperXEngine

    engine = WhisperXEngine(
        service_profile_id=args.profile_id,
        model_cache_ready=lambda: True,
        model_path=Path(os.environ["ASR_WHISPERX_MODEL_CACHE_ROOT"]),
        align_model_path=Path(os.environ["ASR_WHISPERX_ALIGN_MODEL_CACHE_ROOT"]),
        unavailable_reason_code="probe",
    )
    chunk = PreparedAudioChunk(0, args.window_start_ms, args.window_end_ms, window)
    result = engine.transcribe_chunk(chunk, WHISPERX_V2_SERVICE_CONFIG)
    probe: dict[str, object] = {
        "result_type": type(result).__name__,
        "last_failure_stage": getattr(engine, "last_failure_stage", None),
        "last_failure_type": getattr(engine, "last_failure_type", None),
    }
    if type(result) is ProviderFailure:
        probe["error_code"] = result.error_code.value
        probe["classification"] = result.classification.value
    else:
        probe["segment_count"] = len(result.segments)
        probe["duration_ms"] = result.duration_ms
    report["engine_probe"] = probe

    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
