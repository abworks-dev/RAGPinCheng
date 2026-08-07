"""
Evaluate WhisperX decoding parameter candidates against fixed qualification samples.

Only emits sanitized per-sample metrics and per-candidate summaries.
No reference, raw transcript, or Canonical text is written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import unicodedata
from collections import Counter
from pathlib import Path

from scripts import run_whisperx_qualification as qualification

CANDIDATES: dict[str, dict[str, object]] = {
    "baseline": {"initial_prompt": None, "hotwords": None},
    "bim-initial-prompt": {
        "initial_prompt": "以下是中文 BIM 行业录音，可能包含规范编号如 GB 50016 2014。",
        "hotwords": None,
    },
    "bim-hotwords": {
        "initial_prompt": None,
        "hotwords": "建筑信息模型 构件碰撞 净高分析 碰撞检测 施工图审查 BIM Revit GB",
    },
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _char_classes(text: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for ch in _normalize(text):
        if ch.isspace() or ch in "，。、；：？！.,;:!?\"'()（）[]【】《》<>/\\-——_":
            counter["punct_or_space"] += 1
        elif "一" <= ch <= "鿿":
            counter["cjk"] += 1
        elif ch.isascii() and ch.isalpha():
            counter["ascii_letter"] += 1
        elif ch.isascii() and ch.isdigit():
            counter["ascii_digit"] += 1
        else:
            counter["other"] += 1
    return dict(counter)


def _code_shape(text: str) -> str:
    tokens = []
    for ch in _normalize(text):
        if ch.isascii() and ch.isalpha():
            tokens.append("L")
        elif ch.isascii() and ch.isdigit():
            tokens.append("D")
    return "-".join(tokens)


def _expected_items(sample) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for kind, values in (("terms", sample.expected_terms), ("codes", sample.expected_codes)):
        for value in values:
            needle = _normalize(value)
            items.append({
                "kind": kind,
                "len": len(needle),
                "shape": _code_shape(value) if kind == "codes" else None,
            })
    return items


def _edit_counts(reference: str, hypothesis: str) -> dict[str, int]:
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    dp = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, start=1):
        prev = dp[0]
        dp[0] = i
        for j, hc in enumerate(hyp, start=1):
            temp = dp[j]
            if rc == hc:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    distance = dp[-1]
    # Derive per-operation counts from the distance and lengths.
    deletions = max(0, len(ref) - len(hyp))
    insertions = max(0, len(hyp) - len(ref))
    substitutions = (distance - insertions - deletions) // 2
    if substitutions < 0:
        substitutions = 0
        # Fallback to simple tail alignment when lengths differ.
        common_prefix = 0
        while common_prefix < min(len(ref), len(hyp)) and ref[common_prefix] == hyp[common_prefix]:
            common_prefix += 1
        common_suffix = 0
        while (
            common_suffix < min(len(ref), len(hyp)) - common_prefix
            and ref[-(common_suffix + 1)] == hyp[-(common_suffix + 1)]
        ):
            common_suffix += 1
        mid_ref = ref[common_prefix:len(ref)-common_suffix]
        mid_hyp = hyp[common_prefix:len(hyp)-common_suffix]
        substitutions = min(len(mid_ref), len(mid_hyp))
        if len(mid_ref) > len(mid_hyp):
            deletions = len(mid_ref) - len(mid_hyp)
            insertions = 0
        else:
            insertions = len(mid_hyp) - len(mid_ref)
            deletions = 0
    return {
        "distance": distance,
        "substitutions": substitutions,
        "insertions": insertions,
        "deletions": deletions,
    }


def _sample_evidence(sample, raw_text: str, canonical_text: str) -> dict[str, object]:
    normalized_raw = _normalize(raw_text)
    normalized_canonical = _normalize(canonical_text)
    expected = []
    for item in _expected_items(sample):
        needle = ""
        expected.append({
            **item,
            "present_in_raw": False,
            "present_in_canonical": False,
        })
    return {
        "sample_id": sample.sample_id,
        "scenario": sample.scenario,
        "negative_control": sample.negative_control,
        "raw_sha256": hashlib.sha256(normalized_raw.encode("utf-8")).hexdigest(),
        "canonical_sha256": hashlib.sha256(normalized_canonical.encode("utf-8")).hexdigest(),
        "raw_len": len(normalized_raw),
        "canonical_len": len(normalized_canonical),
        "raw_char_classes": _char_classes(raw_text),
        "canonical_char_classes": _char_classes(canonical_text),
        "edit_counts_raw_vs_reference": None if sample.negative_control else _edit_counts(sample.reference_text, raw_text),
        "edit_counts_canonical_vs_reference": None if sample.negative_control else _edit_counts(sample.reference_text, canonical_text),
        "expected_items": expected,
    }


def _parameter_fingerprint(value) -> str:
    if value is None:
        return "<none>"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--nltk-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    args = parser.parse_args()

    manifest = qualification.load_manifest(args.manifest)

    os.environ["NLTK_DATA"] = str(args.nltk_root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch CUDA unavailable")

    engine = qualification._build_engine(args.model_root)

    candidates: list[dict[str, object]] = []
    original_engine = qualification._ENGINE
    original_run_once = qualification._run_once

    try:
        for candidate_id, params in CANDIDATES.items():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()

            engine._eval_initial_prompt = params["initial_prompt"]
            engine._eval_hotwords = params["hotwords"]

            class _EvalProvider:
                provider_key = "whisperx"

                def __init__(self_prov, content, duration_ms):
                    self_prov._content = content
                    self_prov._duration_ms = duration_ms

                def capabilities(self_prov):
                    from src.transcription.provider_protocol import ProviderCapabilities
                    return ProviderCapabilities(
                        self_prov.provider_key, ("zh-CN",), ("audio",), True, False, 60_000
                    )

                def transcribe(self_prov, input_ref, execution):
                    from asr_service.engine_protocol import (
                        PreparedAudioChunk,
                        WHISPERX_SERVICE_CONFIG,
                    )
                    from src.transcription.provider_protocol import (
                        ProviderCandidate,
                        ProviderFailure,
                    )
                    if execution.provider_key != self_prov.provider_key:
                        raise RuntimeError("provider mismatch")
                    result = engine.transcribe_chunk(
                        PreparedAudioChunk(0, 0, self_prov._duration_ms, self_prov._content),
                        WHISPERX_SERVICE_CONFIG,
                    )
                    if type(result) is ProviderFailure:
                        return result
                    return ProviderCandidate(
                        result.provider_key,
                        result.language,
                        result.duration_ms,
                        result.segments,
                        result.artifact_refs,
                    )

            def _run_once_patch(sample, *, base_url, token, timeout_ms):
                del base_url, token
                from src.transcription.canonical import CanonicalTranscript
                from src.transcription.formatter import format_transcript
                from src.transcription.pipeline import execute_transcription
                from src.transcription.profile import (
                    ProfileSnapshot,
                    TranscriptionExecutionConfig,
                )
                from src.transcription.profile_catalog import build_phase3_profile_catalog
                from src.transcription.provider_protocol import ProviderFailure
                from src.transcription.types import TranscriptionInputRef
                content = sample.path.read_bytes()
                input_ref = TranscriptionInputRef(
                    "eval-" + sample.sample_id,
                    "audio",
                    hashlib.sha256(content).hexdigest(),
                    len(content),
                    sample.duration_ms,
                )
                profile = next(
                    item.profile
                    for item in build_phase3_profile_catalog()
                    if item.profile.profile_id == qualification.WHISPERX_PROFILE_ID
                )
                execution = TranscriptionExecutionConfig.create(
                    profile, input_ref, language="zh-CN", timeout_ms=timeout_ms
                )
                snapshot = ProfileSnapshot.create(profile, execution)
                started = time.monotonic()
                result = execute_transcription(
                    _EvalProvider(content, sample.duration_ms),
                    input_ref,
                    execution,
                    profile_snapshot=snapshot,
                )
                elapsed = time.monotonic() - started
                if type(result) is ProviderFailure:
                    raise RuntimeError(f"provider failure: {result.error_code.value}")
                if type(result) is not CanonicalTranscript:
                    raise RuntimeError("pipeline did not return CanonicalTranscript")
                markdown = format_transcript(
                    result, title=f"WhisperX eval {candidate_id} {sample.sample_id}"
                )
                turns = qualification.shared._load_transcript_parser()(markdown.decode("utf-8"))
                if not turns:
                    raise RuntimeError("formatted Markdown is not parseable")
                raw_text = ""
                return result, markdown, turns, elapsed, raw_text

            qualification._ENGINE = engine
            qualification._run_once = _run_once_patch
            try:
                result = qualification.run_qualification(manifest, timeout_ms=args.timeout_ms)
            finally:
                qualification._ENGINE = original_engine
                qualification._run_once = original_run_once

            peak_mib = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)
            candidates.append({
                "candidate_id": candidate_id,
                "parameters": {
                    k: _parameter_fingerprint(v) for k, v in params.items()
                },
                "peak_gpu_memory_mib": peak_mib,
                "gates": result["gates"],
                "sample_count": result["sample_count"],
                "samples": result["samples"],
            })
    finally:
        engine.__dict__.pop("_eval_initial_prompt", None)
        engine.__dict__.pop("_eval_hotwords", None)

    report = {
        "schema_version": "whisperx-decoding-params-eval/1",
        "status": "complete",
        "contains_transcript_text": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "decoding-params-eval.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": report["status"], "candidate_count": len(candidates)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
