"""Read-only probe: how well do decode options transcribe a corpus WAV?

Corpus-authoring and incident tooling for the WhisperX profile. It decodes one or
more 16 kHz mono WAV files with the production decode options plus single-option
overrides and reports, per case, the decoded text together with standard-code and
BIM-term recall against expectations supplied on the command line.

Motivation (2026-09-13): production long audio degenerated into hotword/code loops
inside individual decode windows while a no-hotword decode of the same audio
recovered the real speech, so the approved follow-up is to ship the WhisperX
profile without hotwords. Absolute qualification gates (standard-code recall,
BIM-term recall) must still hold on the corpus, and this probe measures exactly
that before the corpus or the profile is changed.

Usage (inside the active release venv on the ASR node):

    python scripts/probe_whisperx_corpus_sample.py \
        --app-root <release>\\app \
        --config-path D:\\ServiceData\\RAGPinCheng-ASR\\config\\asr.env \
        --wav sample-a.wav,sample-b.wav \
        --expected-codes "GB 50011-2010,GB 50016-2014" \
        --expected-terms "构件碰撞,净高分析,梯梁,梯柱" \
        --report probe.json

Never mutates the service and writes only its JSON report.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import wave
from pathlib import Path

# Single-option overrides applied on top of the production decode options.
CASES: dict[str, dict[str, object]] = {
    "production": {},
    "no_hotwords": {"hotwords": None},
    "no_hotwords_no_prompt": {"hotwords": None, "initial_prompt": None},
    "ngram3": {"no_repeat_ngram_size": 3},
    "no_vad": {"vad_filter": False},
}


def load_env_file(config_path: Path) -> None:
    if not config_path.is_file():
        return
    for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def read_pcm(path: Path) -> tuple[list[float], int]:
    """Return (float samples, duration_ms) for a 16 kHz mono 16-bit PCM WAV."""
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected 16-bit PCM")
        if handle.getnchannels() != 1:
            raise SystemExit(f"{path.name}: expected mono")
        if handle.getframerate() != 16000:
            raise SystemExit(f"{path.name}: expected 16 kHz")
        frames = handle.readframes(handle.getnframes())
    import numpy as np

    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    duration_ms = int(round(pcm.size * 1000 / 16000))
    return pcm, duration_ms


def recall(text: str, needles: list[str]) -> dict[str, object]:
    compact = "".join(text.split())
    hits = [needle for needle in needles if "".join(needle.split()) in compact]
    return {
        "expected": needles,
        "hits": hits,
        "misses": [needle for needle in needles if needle not in hits],
        "recall": round(len(hits) / len(needles), 4) if needles else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--wav", required=True, help="comma separated 16 kHz mono WAV paths")
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-codes", default="")
    parser.add_argument("--expected-terms", default="")
    parser.add_argument("--cases", default="")
    args = parser.parse_args()

    load_env_file(Path(args.config_path))
    sys.path.insert(0, args.app_root)
    report: dict[str, object] = {"schema_version": "whisperx-corpus-sample-probe/1", "read_only": True}

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
    report["production_profile"] = {
        "service_profile_id": config.service_profile_id,
        "beam_size": config.beam_size,
        "temperature": config.temperature,
        "hotword_count": len(config.hotwords),
        "initial_prompt_enabled": bool(config.initial_prompt),
    }
    if not cache.available:
        report["status"] = "model-cache-unavailable"
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("CORPUS_PROBE_INCOMPLETE " + args.report)
        return 0

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

    expected_codes = [item for item in args.expected_codes.split(",") if item.strip()]
    expected_terms = [item for item in args.expected_terms.split(",") if item.strip()]

    samples: list[dict[str, object]] = []
    for raw_path in args.wav.split(","):
        path = Path(raw_path.strip())
        pcm, duration_ms = read_pcm(path)
        row: dict[str, object] = {
            "wav": path.name,
            "duration_ms": duration_ms,
            "cases": {},
        }
        for name in selected:
            options = dict(base_options)
            options.update(CASES[name])
            iterator, _info = model.transcribe(pcm, **options)
            texts = [segment.text.strip() for segment in iterator]
            joined = " ".join(text for text in texts if text)
            row["cases"][name] = {
                "segments": len(texts),
                "chars": len("".join(texts)),
                "text": joined,
                "code_recall": recall(joined, expected_codes),
                "term_recall": recall(joined, expected_terms),
            }
        samples.append(row)
        print(f"CORPUS_PROBE_SAMPLE_DONE {path.name}")

    report["samples"] = samples
    report["status"] = "ok"
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("CORPUS_PROBE_WRITTEN " + args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
