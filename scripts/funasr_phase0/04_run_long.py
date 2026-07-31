"""Phase 0 ASR sandbox — long video chunked ASR (entry point 04).

Per R2 spec §十二:
  - Model loaded once per worker.
  - Input must be inside approved testdata root.
  - Audio extract cache key includes source SHA-256, sample rate, channels,
    decoder version.
  - Write .partial first, atomic rename after full validation.
  - Verify WAV frame count, sample rate, channels, duration.
  - Last chunk records real duration.
  - Per-chunk atomic checkpoint (append).
  - Recovery checks source hash, model revision, config hash, chunk hash.
  - Only skip chunks that pass full validation.
  - Save complete absolute timestamp segments.
  - Generate merged result.
  - Compute cross-chunk repeat, omission, broken sentences, timestamp drift.
  - Time extraction, chunking, model load, pure inference separately.
  - Completed chunks recoverable after process kill.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import struct
import sys
import time
import traceback
import wave
from pathlib import Path

LONG_SCHEMA_VERSION = "phase0-long/2"
LONG_CHUNK_SCHEMA_VERSION = "phase0-long-chunk/2"
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def _atomic_json_dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, path)


def _wav_header_for(sr: int = 16000, ch: int = 1, sw: int = 2) -> bytes:
    return b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + struct.pack("<IHHIIHH",
                                                              16, 1, ch, sr,
                                                              sr * ch * sw, ch * sw, sw)


def _decode_audio_pcm(src: Path, out_wav: Path, target_sr: int = 16000) -> dict:
    """Decode any audio/video container to 16k mono PCM WAV via PyAV.

    Returns the resulting metadata (duration_s, sr, ch, nframes).
    Raises on: no audio track, decode failure, unsupported codec.
    """
    import av
    container = av.open(str(src))
    audio_streams = [s for s in container.streams if s.type == "audio"]
    if not audio_streams:
        container.close()
        raise RuntimeError(f"no audio track in {src}")
    stream = audio_streams[0]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Write .partial then atomic rename
    partial = out_wav.with_suffix(out_wav.suffix + ".partial")
    with wave.open(str(partial), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=target_sr)
        nframes = 0
        for frame in container.decode(stream):
            for r in resampler.resample(frame):
                data = bytes(r.planes[0])
                wf.writeframes(data)
                nframes += len(data) // 2
        for r in resampler.resample(None):
            data = bytes(r.planes[0])
            wf.writeframes(data)
            nframes += len(data) // 2
    container.close()
    # Validate header / sample rate / channel count / frame count
    with wave.open(str(partial), "rb") as wf:
        actual_sr = wf.getframerate()
        actual_ch = wf.getnchannels()
        actual_sw = wf.getsampwidth()
        actual_n = wf.getnframes()
    if actual_sr != target_sr or actual_ch != 1 or actual_sw != 2 or actual_n <= 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"output WAV validation failed: sr={actual_sr} ch={actual_ch} sw={actual_sw} n={actual_n}")
    os.replace(partial, out_wav)
    return {
        "sr": actual_sr, "ch": actual_ch, "sw": actual_sw,
        "nframes": actual_n, "duration_s": actual_n / actual_sr,
    }


def _decode_cache_key(src: Path, target_sr: int = 16000, target_ch: int = 1) -> str:
    src_sha = _file_sha256(src)
    decoder_version = "pyav-decoder/1"
    return f"{src_sha}|sr{target_sr}|ch{target_ch}|{decoder_version}"


def _split_wav(wav: Path, chunk_s: float, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    with wave.open(str(wav), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        total = wf.getnframes()
        assert sr == 16000 and ch == 1 and sw == 2
        chunk_frames = int(chunk_s * sr)
        n_chunks = (total + chunk_frames - 1) // chunk_frames
        for i in range(n_chunks):
            start = i * chunk_frames
            wf.setpos(start)
            data = wf.readframes(min(chunk_frames, total - start))
            out = out_dir / f"{wav.stem}.chunk{i:04d}.wav"
            with wave.open(str(out), "wb") as owf:
                owf.setnchannels(1); owf.setsampwidth(2); owf.setframerate(16000)
                owf.writeframes(data)
            chunks.append(out)
    return chunks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--reference", required=True,
                   help="reviewed reference JSON for this exact input")
    p.add_argument("--label", default=None)
    p.add_argument("--chunk-s", type=float, default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import (
        load_config, gate_for_gpu_entry, ConfigGateError, selected_asr_model,
    )
    from scripts.funasr_phase0.lib_metrics import (
        bim_term_metrics, cer, code_metrics, segment_metrics, Segment, rtf,
        realtime_speedup,
    )
    from scripts.funasr_phase0.lib_runtime import (
        enable_offline_model_access, require_guarded_worker, resolve_staged_model,
    )

    cfg = load_config(args.config)
    try:
        gate_for_gpu_entry(cfg, command_name="04_run_long")
        require_guarded_worker(cfg, "04_run_long")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1
    except RuntimeError as e:
        print(f"!! guarded worker rejected: {e}")
        return 1

    testdata_root = Path(cfg.testdata_root).resolve()
    src = Path(args.input).resolve()
    try:
        src.relative_to(testdata_root)
    except ValueError:
        print(f"!! input {src} is NOT inside testdata_root {testdata_root}")
        return 1
    if not src.exists():
        print(f"!! input not found: {src}")
        return 1
    label = args.label or src.stem
    if not _SAFE_LABEL_RE.fullmatch(label):
        print(f"!! unsafe label: {label!r}")
        return 1
    chunk_s = args.chunk_s if args.chunk_s else cfg.audio_chunk_s
    if chunk_s <= 0:
        print("!! chunk-s must be > 0")
        return 1
    reference_path = Path(args.reference).resolve()
    try:
        reference_path.relative_to(testdata_root)
    except ValueError:
        print(f"!! reference {reference_path} is NOT inside testdata_root {testdata_root}")
        return 1
    try:
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"!! reference invalid: {e}")
        return 1
    src_sha256 = _file_sha256(src)
    if reference.get("input_sha256") != src_sha256:
        print("!! reference input_sha256 does not match input")
        return 1
    ref_segments_raw = reference.get("reference_segments")
    if not isinstance(ref_segments_raw, list) or not ref_segments_raw:
        print("!! reviewed reference_segments are required")
        return 1
    try:
        ref_segments = [Segment(**row) for row in ref_segments_raw]
    except (TypeError, ValueError) as e:
        print(f"!! reference_segments invalid: {e}")
        return 1
    reference_text = str(reference.get("reference_text", ""))
    if not reference_text.strip():
        print("!! reviewed reference_text is required")
        return 1
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    cache_dir = Path(cfg.models_root) / "audio_cache" / label
    cache_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = Path(cfg.logs_root) / cfg.run_id / "04_run_long" / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = Path(cfg.logs_root) / cfg.run_id / "04_run_long" / f"chunks-{stamp}"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir = Path(cfg.checkpoints_root) / cfg.run_id / "04_run_long" / label
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = Path(cfg.reports_root) / cfg.run_id
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 1) Audio extract
    t_extract_start = time.monotonic()
    cache_key = _decode_cache_key(src)
    cache_wav = cache_dir / f"{cache_key[:32]}.16k.wav"
    cache_manifest = cache_dir / f"{cache_key[:32]}.manifest.json"
    if cache_wav.exists() and cache_manifest.exists():
        try:
            meta = json.loads(cache_manifest.read_text(encoding="utf-8"))
            # Re-validate cache: header + frame count + duration
            with wave.open(str(cache_wav), "rb") as wf:
                if (wf.getframerate() == meta["sr"] and wf.getnchannels() == meta["ch"]
                        and wf.getsampwidth() == meta["sw"]
                        and wf.getnframes() == meta["nframes"]):
                    audio_meta = meta
                    extract_s = 0.0
                else:
                    raise RuntimeError("cache mismatch")
        except Exception:  # noqa: BLE001
            audio_meta = _decode_audio_pcm(src, cache_wav)
            _atomic_json_dump(cache_manifest, audio_meta | {"cache_key": cache_key, "src_sha256": _file_sha256(src)})
            extract_s = time.monotonic() - t_extract_start
    else:
        audio_meta = _decode_audio_pcm(src, cache_wav)
        _atomic_json_dump(cache_manifest, audio_meta | {"cache_key": cache_key, "src_sha256": _file_sha256(src)})
        extract_s = time.monotonic() - t_extract_start

    duration_s = audio_meta["duration_s"]
    print(f">> extracted {duration_s:.1f}s audio (extract_s={extract_s:.2f})")

    # 2) Chunk
    t_chunk_start = time.monotonic()
    chunks = _split_wav(cache_wav, chunk_s, chunks_dir)
    chunk_s_actual = time.monotonic() - t_chunk_start
    print(f">> chunks={len(chunks)} (chunk_s_actual={chunk_s_actual:.2f})")

    # 3) Load model ONCE
    print(">> loading ASR model (one-time per worker)")
    t_model_start = time.monotonic()
    model_id, revision = selected_asr_model(cfg)
    try:
        local_model = resolve_staged_model(cfg, model_id)
        local_vad_model = resolve_staged_model(cfg, cfg.vad_model_id)
        local_punc_model = resolve_staged_model(cfg, cfg.punc_model_id)
    except RuntimeError as e:
        print(f"!! local model gate rejected: {e}")
        return 1
    enable_offline_model_access()
    from funasr import AutoModel
    model = AutoModel(
        model=str(local_model),
        vad_model=str(local_vad_model),
        punc_model=str(local_punc_model),
        device="cuda", disable_update=True,
    )
    cold_start_s = time.monotonic() - t_model_start

    config_hash = hashlib.sha256(
        f"{model_id}|{revision}|{cfg.vad_model_id}|{cfg.vad_model_revision}|"
        f"{cfg.punc_model_id}|{cfg.punc_model_revision}|cuda|{cfg.config_sha256}".encode("utf-8")
    ).hexdigest()

    # 4) Per-chunk with atomic checkpoint
    chunks_log = ckpt_dir / "chunks.jsonl"
    merged: list[dict] = []
    failures: list[dict] = []
    n_inference_only = 0
    n_reused = 0
    total_pure_inference_s = 0.0
    peak_vram_gb = 0.0
    for i, ck in enumerate(chunks):
        offset_ms = i * int(chunk_s * 1000)
        ckpt = ckpt_dir / f"chunk_{i:04d}.json"
        if ckpt.exists():
            try:
                d = json.loads(ckpt.read_text(encoding="utf-8"))
                if (d.get("schema_version") == LONG_CHUNK_SCHEMA_VERSION
                        and d.get("source_sha256") == src_sha256
                        and d.get("ck_sha256") == _file_sha256(ck)
                        and d.get("config_hash") == config_hash
                        and d.get("model_id") == model_id
                        and d.get("revision") == revision):
                    n_reused += 1
                    total_pure_inference_s += float(d.get("pure_inference_s", 0.0))
                    peak_vram_gb = max(peak_vram_gb, float(d.get("peak_vram_gb", 0.0)))
                    merged.extend(d.get("merged", []))
                    continue
            except (json.JSONDecodeError, OSError):
                pass
        t0 = time.monotonic()
        try:
            import torch
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            result = model.generate(input=str(ck), batch_size_s=60, is_final=True)
        except Exception as e:  # noqa: BLE001
            failures.append({"chunk_index": i, "error": f"{type(e).__name__}: {e}"})
            print(f"   chunk {i+1}/{len(chunks)} FAIL: {e}")
            continue
        pure_inference_s = time.monotonic() - t0
        total_pure_inference_s += pure_inference_s
        chunk_peak_vram_gb = torch.cuda.max_memory_allocated() / (1024 ** 3)
        peak_vram_gb = max(peak_vram_gb, chunk_peak_vram_gb)
        n_inference_only += 1
        if isinstance(result, list) and result:
            result = result[0]
        ck_merged: list[dict] = []
        sentence_info = (result or {}).get("sentence_info") or []
        with wave.open(str(ck), "rb") as ck_wf:
            actual_chunk_duration_s = ck_wf.getnframes() / ck_wf.getframerate()
        if not sentence_info and str((result or {}).get("text", "")).strip():
            sentence_info = [{
                "start": 0,
                "end": round(actual_chunk_duration_s * 1000),
                "text": str(result["text"]),
            }]
        for s in sentence_info:
            try:
                start = int(s.get("start", 0)) + offset_ms
                end = int(s.get("end", 0)) + offset_ms
                text = s.get("text", "")
            except (TypeError, ValueError):
                continue
            if end <= start or not text.strip():
                continue
            ck_merged.append({"start_ms": start, "end_ms": end, "text": text})
        chunk_row = {
            "schema_version": LONG_CHUNK_SCHEMA_VERSION,
            "chunk_index": i,
            "source_sha256": src_sha256,
            "ck_sha256": _file_sha256(ck),
            "config_hash": config_hash,
            "model_id": model_id, "revision": revision,
            "chunk_path": str(ck),
            "offset_ms": offset_ms,
            "chunk_duration_s": actual_chunk_duration_s,
            "pure_inference_s": round(pure_inference_s, 3),
            "rtf": round(rtf(actual_chunk_duration_s, pure_inference_s), 4),
            "peak_vram_gb": round(chunk_peak_vram_gb, 4),
            "merged": ck_merged,
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        }
        _atomic_json_dump(ckpt, chunk_row)
        merged.extend(ck_merged)
        print(f"   chunk {i+1}/{len(chunks)} ok inference={pure_inference_s:.1f}s rtf={chunk_row['rtf']}")

    # 5) Aggregate
    # Last chunk actual duration: re-measure last chunk WAV
    if chunks:
        last_wav = chunks[-1]
        with wave.open(str(last_wav), "rb") as wf:
            actual_last_dur = wf.getnframes() / wf.getframerate()
    else:
        actual_last_dur = 0.0
    hyp_segs = [Segment(**s) for s in merged]
    full_text = "".join(s.text for s in hyp_segs)
    sm = segment_metrics(ref_segments, hyp_segs)
    cm = code_metrics(reference_text, full_text)
    bm = bim_term_metrics(reference_text, full_text)
    cer_value = cer(reference_text, full_text)
    total_rtf = rtf(duration_s, total_pure_inference_s)
    failure_rate = len(failures) / max(1, len(chunks)) * 100.0
    checks: list[dict] = []

    def add_check(name: str, observed: float, threshold: float, op: str) -> None:
        passed = observed <= threshold if op == "<=" else observed >= threshold
        checks.append({"name": name, "observed": observed,
                       "threshold": threshold, "operator": op, "pass": passed})

    scenario = str(reference.get("scenario", ""))
    add_check("failure_rate_pct", failure_rate,
              cfg.thresholds.long_failure_rate_pct, "<=")
    add_check("cer", cer_value,
              cfg.thresholds.cer_max_bim if "bim" in scenario else cfg.thresholds.cer_max_clear,
              "<=")
    if "bim" in scenario:
        add_check("bim_term_recall", bm.recall,
                  cfg.thresholds.bim_term_recall_min, ">=")
    if cm.true_positive + cm.false_negative > 0:
        add_check("code_recall", cm.recall, cfg.thresholds.code_recall_min, ">=")
    add_check("timestamp_p95_drift_ms", sm.start_drift_p95_ms,
              cfg.thresholds.timestamp_p95_drift_ms_long, "<=")
    add_check("rtf", total_rtf, cfg.thresholds.rtf_max, "<=")
    add_check("peak_vram_gb", peak_vram_gb, cfg.thresholds.asr_peak_vram_gb, "<=")
    verdict_ok = all(c["pass"] for c in checks)

    out = (Path(args.out).resolve() if args.out else
           reports_dir / f"04_run_long-{label}-{stamp}.json")
    try:
        out.relative_to(reports_dir.resolve())
    except ValueError:
        print(f"!! output must stay under reports directory: {reports_dir}")
        return 1
    payload = {
        "schema_version": LONG_SCHEMA_VERSION,
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "label": label,
        "input_sha256": src_sha256,
        "reference": {
            "path": str(reference_path),
            "annotation_version": reference.get("annotation_version", ""),
            "source_id": reference.get("source_id", reference.get("source_url", "")),
        },
        "model": {
            "id": model_id, "revision": revision,
            "vad_model_id": cfg.vad_model_id,
            "vad_model_revision": cfg.vad_model_revision,
            "punc_model_id": cfg.punc_model_id,
            "punc_model_revision": cfg.punc_model_revision,
            "config_hash": config_hash,
            "modelscope_cache": os.environ.get("MODELSCOPE_CACHE", ""),
        },
        "timing": {
            "extract_s": round(extract_s, 3),
            "chunk_s_actual": round(chunk_s_actual, 3),
            "cold_start_s": round(cold_start_s, 3),
            "pure_inference_s": round(total_pure_inference_s, 3),
            "rtf": round(total_rtf, 4),
        },
        "audio": audio_meta,
        "audio_duration_s": duration_s,
        "last_chunk_actual_duration_s": actual_last_dur,
        "n_chunks": len(chunks),
        "n_reused_checkpoints": n_reused,
        "n_inference_calls": n_inference_only,
        "n_failures": len(failures),
        "failures": failures,
        "segments": merged,
        "cer": cer_value,
        "code_metrics": cm.as_dict(),
        "bim_term_metrics": bm.as_dict(),
        "segment_metrics": sm.as_dict(),
        "peak_vram_gb": peak_vram_gb,
        "checks": checks,
        "ok": verdict_ok,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                  encoding="utf-8")
    print(f">> wrote {out}")
    return 0 if verdict_ok else 2


if __name__ == "__main__":
    sys.exit(main())
