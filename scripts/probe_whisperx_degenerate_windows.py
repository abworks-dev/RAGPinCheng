"""Read-only probe: can a degenerate whisperx window recover its speech?

Motivation (2026-09-13 production): the stair inspection video (job
``459010b5``) decodes two spans as pure hotword/code loops, e.g.
``720.0-750.0s`` becomes ``建筑抗震设计规范 GB 40011-2014`` nine times and
``779.3-802.1s`` becomes five ``楼层平台`` segments, so the real speech inside
those 30s/23s spans never reaches the transcript. The same spans reproduce
byte-identically across two separate jobs, so the degeneration is deterministic
for a given set of decode options.

This probe replays the production decode options on chosen windows of a spooled
job and then replays the same audio with single-option overrides, reporting what
each variant recovers. It never mutates the service, never writes inside the
release tree, and writes only its JSON report.

Usage (inside the active release venv on the ASR node):

    python scripts/probe_whisperx_degenerate_windows.py \
        --app-root D:\\Services\\RAGPinCheng-ASR\\releases\\<id> \
        --config-path D:\\ServiceData\\RAGPinCheng-ASR\\config\\asr.env \
        --job-dir D:\\ServiceData\\RAGPinCheng-ASR\\spool\\<job-id> \
        --windows 716500:750000,779000:802500 \
        --report D:\\ServiceData\\RAGPinCheng-ASR\\diagnostics\\degenerate-probe.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import wave
from io import BytesIO
from pathlib import Path

# Single-option overrides applied on top of the production decode options. The
# point is to attribute the degeneration to one option at a time.
CASES: dict[str, dict[str, object]] = {
    "production": {},
    "no_hotwords": {"hotwords": None},
    "no_prompt": {"initial_prompt": None},
    "no_hotwords_no_prompt": {"hotwords": None, "initial_prompt": None},
    "ngram3": {"no_repeat_ngram_size": 3},
    "no_hotwords_ngram3": {"hotwords": None, "no_repeat_ngram_size": 3},
    "temperature_0_4": {"temperature": 0.4},
    "no_vad": {"vad_filter": False},
}


def reconstruct(job_dir: Path) -> bytes:
    manifest = json.loads((job_dir / "upload-manifest.json").read_text(encoding="utf-8"))
    parts = [
        (job_dir / "parts" / f"{int(record['part_number']):08d}.part").read_bytes()
        for record in manifest["parts"]
    ]
    return b"".join(parts)


def load_env_file(config_path: Path) -> None:
    if not config_path.is_file():
        return
    for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def loop_score(texts: list[str]) -> dict[str, object]:
    """Cheap degeneration signal: how much of the text is one repeated phrase."""
    joined = "".join(texts)
    if not joined:
        return {"chars": 0, "repeat_ratio": 0.0, "distinct_chars": 0}
    best = 0
    for size in range(4, min(40, len(joined) // 2) + 1):
        counts: dict[str, int] = {}
        for start in range(0, len(joined) - size + 1):
            fragment = joined[start : start + size]
            counts[fragment] = counts.get(fragment, 0) + 1
        for fragment, count in counts.items():
            if count >= 2:
                best = max(best, (count - 1) * len(fragment))
    return {
        "chars": len(joined),
        "distinct_chars": len(set(joined)),
        "repeat_ratio": round(best / len(joined), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--windows", required=True, help="comma separated start:end pairs in ms")
    parser.add_argument("--cases", default="", help="optional comma separated subset of case names")
    args = parser.parse_args()

    load_env_file(Path(args.config_path))
    sys.path.insert(0, args.app_root)
    report: dict[str, object] = {"schema_version": "whisperx-degenerate-probe/1", "read_only": True}

    import numpy as np

    from services.asr_service import audio as audio_module
    from services.asr_service.model_cache import validate_whisperx_cache
    from src.transcription.service_profiles import WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG as config

    def optional_path(name: str) -> Path | None:
        raw = os.environ.get(name, "").strip()
        return Path(raw) if raw else None

    cache = validate_whisperx_cache(
        optional_path("ASR_WHISPERX_MODEL_CACHE_ROOT"),
        optional_path("ASR_WHISPERX_MODEL_MANIFEST_PATH"),
    )
    report["model_cache_available"] = bool(cache.available)
    report["profile"] = {
        "service_profile_id": config.service_profile_id,
        "beam_size": config.beam_size,
        "temperature": config.temperature,
        "hotwords": list(config.hotwords),
        "initial_prompt": config.initial_prompt,
    }
    if not cache.available:
        report["status"] = "model-cache-unavailable"
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("DEGENERATE_PROBE_INCOMPLETE " + args.report)
        return 0

    samples = audio_module.decode_audio_samples(reconstruct(Path(args.job_dir)))
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    report["decoded_seconds"] = round(float(array.size) / 16000.0, 2)

    faster_whisper = importlib.import_module("faster_whisper")
    model = faster_whisper.WhisperModel(
        str(cache.model_path),
        device="cuda",
        compute_type="float16",
        local_files_only=True,
    )

    base_options: dict[str, object] = {
        "language": "zh",
        "task": "transcribe",
        "beam_size": config.beam_size,
        "temperature": config.temperature,
        "hotwords": " ".join(config.hotwords) if config.hotwords else None,
        "initial_prompt": config.initial_prompt or None,
        "condition_on_previous_text": False,
        "word_timestamps": False,
        "vad_filter": True,
    }

    selected = [name for name in args.cases.split(",") if name] or list(CASES)
    unknown = [name for name in selected if name not in CASES]
    if unknown:
        raise SystemExit(f"unknown case names: {unknown}")

    windows: list[dict[str, object]] = []
    for spec in args.windows.split(","):
        start_ms, end_ms = (int(part) for part in spec.split(":"))
        window_wav = audio_module.encode_wav_window(array, start_ms=start_ms, end_ms=end_ms)
        with wave.open(BytesIO(window_wav), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        row: dict[str, object] = {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "rms": round(float(np.sqrt(float((pcm ** 2).mean()))), 5),
            "cases": {},
        }
        for name in selected:
            options = dict(base_options)
            options.update(CASES[name])
            try:
                iterator, _info = model.transcribe(pcm, **options)
                segments = [
                    {
                        "start": round(float(segment.start), 2),
                        "end": round(float(segment.end), 2),
                        "text": segment.text.strip(),
                    }
                    for segment in iterator
                ]
                texts = [segment["text"] for segment in segments]
                row["cases"][name] = {
                    "segments": len(segments),
                    **loop_score(texts),
                    "spans": [[segment["start"], segment["end"]] for segment in segments],
                    "texts": texts,
                }
            except Exception as exc:  # keep the sweep going for the other cases
                row["cases"][name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        windows.append(row)
        print(f"DEGENERATE_PROBE_WINDOW_DONE {start_ms}-{end_ms}")

    report["windows"] = windows
    report["status"] = "ok"
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DEGENERATE_PROBE_WRITTEN " + args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
