"""Phase 0 ASR sandbox — short sample ASR test (entry point 03).

Per R2 spec §十一:
  - ONE worker process loads ASR+VAD+punc ONCE.
  - Time categories: model_download_s, cold_start_s, warm_up_s,
    pure_inference_s, end_to_end_s.
  - RTF = pure_inference_s / audio_duration_s.
  - model revision + config_hash in report.
  - --device=cpu is removed; only --device=cuda accepted.
  - 0% failure rate required; any sample failure fails the step.
  - Per-sample atomic checkpoint.
  - Recovery verifies input hash + model revision + config hash.
  - Stop flag isolated by run_id.
  - Input must be inside the approved testdata root.
  - Sample report includes audio SHA-256, source_id, annotation_version.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

SHORT_SCHEMA_VERSION = "phase0-short/2"
SHORT_CHECKPOINT_SCHEMA_VERSION = "phase0-short-checkpoint/2"
REQUIRED_SHORT_SCENARIOS = frozenset({
    "clear_single_speaker", "multi_speaker", "background_noise", "fast_speech",
    "background_music", "long_silence", "bim_terms", "noise_with_bim",
})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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


def _character_diff(reference: str, hypothesis: str) -> list[dict]:
    """Return compact non-equal character spans for explicit diagnostics."""
    matcher = difflib.SequenceMatcher(a=reference, b=hypothesis, autojunk=False)
    return [
        {
            "operation": tag,
            "reference_start": i1,
            "reference_end": i2,
            "hypothesis_start": j1,
            "hypothesis_end": j2,
            "reference_text": reference[i1:i2],
            "hypothesis_text": hypothesis[j1:j2],
        }
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--device", default="cuda", choices=["cuda"],
                   help="only 'cuda' is allowed; CPU is removed per R2 spec")
    p.add_argument("--out", default=None)
    p.add_argument("--diagnostic-sample-id", default=None)
    p.add_argument("--include-diagnostic-text", action="store_true")
    args = p.parse_args(argv)

    if bool(args.diagnostic_sample_id) != bool(args.include_diagnostic_text):
        p.error(
            "--diagnostic-sample-id and --include-diagnostic-text must be used together"
        )

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config, gate_for_gpu_entry, ConfigGateError
    from scripts.funasr_phase0.lib_config import selected_asr_model
    from scripts.funasr_phase0.lib_runtime import (
        enable_offline_model_access, require_guarded_worker, resolve_staged_model,
    )
    from scripts.funasr_phase0.lib_metrics import (
        cer, code_metrics, bim_term_metrics, segment_metrics, Segment,
        rtf, realtime_speedup, CER_NORM_VERSION,
    )

    cfg = load_config(args.config)
    try:
        gate_for_gpu_entry(cfg, command_name="03_run_short")
        require_guarded_worker(cfg, "03_run_short")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1
    except RuntimeError as e:
        print(f"!! guarded worker rejected: {e}")
        return 1

    testdata_root = Path(cfg.testdata_root).resolve()
    manifests_root = testdata_root
    ckpt_dir = Path(cfg.checkpoints_root) / cfg.run_id / "03_run_short"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(cfg.logs_root) / cfg.run_id / "03_run_short"
    log_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest).resolve()
    try:
        manifest_path.relative_to(testdata_root)
    except ValueError:
        print(f"!! manifest {manifest_path} is NOT inside testdata_root {testdata_root}")
        return 1

    # Manifest
    samples: list[dict] = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            samples.append(json.loads(line))
    if not samples:
        print("!! short manifest is empty")
        return 1
    scenarios = [str(s.get("scenario", "")) for s in samples]
    if len(samples) != len(REQUIRED_SHORT_SCENARIOS) or set(scenarios) != REQUIRED_SHORT_SCENARIOS:
        print(
            "!! short manifest must contain exactly one sample for each preregistered "
            f"scenario; got={sorted(scenarios)}"
        )
        return 1
    diagnostic_sample = None
    if args.diagnostic_sample_id:
        diagnostic_sample = next(
            (s for s in samples if str(s.get("id", "")) == args.diagnostic_sample_id),
            None,
        )
        if diagnostic_sample is None:
            print(f"!! diagnostic sample not found: {args.diagnostic_sample_id}")
            return 1
        if (not str(diagnostic_sample.get("self_made", "")).strip()
                or diagnostic_sample.get("is_internal_recording") is not False):
            print("!! diagnostic text is allowed only for a self-made, non-internal sample")
            return 1

    # Validate samples are inside testdata_root
    for s in samples:
        sid = str(s.get("id", ""))
        if not _SAFE_ID_RE.fullmatch(sid):
            print(f"!! unsafe sample id: {sid!r}")
            return 1
        audio = (manifest_path.parent / Path(s["audio"])).resolve()
        try:
            audio.relative_to(testdata_root)
        except ValueError:
            print(f"!! sample {s.get('id')} audio {audio} is NOT inside testdata_root {testdata_root}")
            return 1
        if not audio.is_file():
            print(f"!! sample {sid} audio not found: {audio}")
            return 1

    # Resolve every model locally before importing FunASR.  There is no hub
    # fallback in a guarded worker.
    model_id, revision = selected_asr_model(cfg)
    try:
        local_model = resolve_staged_model(cfg, model_id)
        local_vad_model = resolve_staged_model(cfg, cfg.vad_model_id)
        local_punc_model = resolve_staged_model(cfg, cfg.punc_model_id)
    except RuntimeError as e:
        print(f"!! local model gate rejected: {e}")
        return 1
    enable_offline_model_access()

    # Load model ONCE
    print(">> loading ASR model (one-time per worker)")
    t_model_start = time.monotonic()
    model_load_meta: dict = {"device": args.device, "model_id": None, "revision": None, "config_hash": None}
    import torch  # noqa: F401  (import for side-effects / availability)
    try:
        from funasr import AutoModel
    except ImportError as e:
        print(f"!! funasr import failed: {e}")
        return 1
    model_load_meta.update({"model_id": model_id, "revision": revision})
    try:
        model = AutoModel(
            model=str(local_model),
            vad_model=str(local_vad_model),
            punc_model=str(local_punc_model),
            device=args.device,
            disable_update=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"!! AutoModel load failed: {e}")
        return 1
    cold_start_s = time.monotonic() - t_model_start

    # Warm-up: amortize one-time CUDA init (cuDNN, cudaMalloc, weight load,
    # tokenizer init) by running the full ASR+VAD+punc pipeline on the first
    # reviewed sample. Silent synthetic clips only warm VAD.
    print(">> warm-up (first reviewed sample, full ASR+VAD+punc pipeline)")
    t_warm_start = time.monotonic()
    warm_id = samples[0]["id"]
    warm_audio = (manifest_path.parent / Path(samples[0]["audio"])).resolve()
    try:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        warm_result = model.generate(input=str(warm_audio), batch_size_s=60, is_final=True)
        _ = warm_result  # ensure no lazy reference; result is discarded
    except Exception as e:  # noqa: BLE001
        print(f"!! warm-up failed on first sample {warm_id}: {e}")
        return 1
    warm_up_s = time.monotonic() - t_warm_start
    model_load_meta["warmup_sample_id"] = warm_id
    model_load_meta["warmup_input_sha256"] = _file_sha256(warm_audio)

    # config_hash: use model_id + revision as identity
    cfg_hash_input = (
        f"{model_id}|{revision}|{cfg.vad_model_id}|{cfg.vad_model_revision}|"
        f"{cfg.punc_model_id}|{cfg.punc_model_revision}|{args.device}|{cfg.config_sha256}"
    ).encode("utf-8")
    config_hash = hashlib.sha256(cfg_hash_input).hexdigest()
    model_load_meta["config_hash"] = config_hash
    model_load_meta["vad_model_id"] = cfg.vad_model_id
    model_load_meta["vad_model_revision"] = cfg.vad_model_revision
    model_load_meta["punc_model_id"] = cfg.punc_model_id
    model_load_meta["punc_model_revision"] = cfg.punc_model_revision
    model_load_meta["local_model_path"] = str(local_model)
    model_load_meta["local_vad_model_path"] = str(local_vad_model)
    model_load_meta["local_punc_model_path"] = str(local_punc_model)

    rows: list[dict] = []
    failures: list[dict] = []
    threshold_failures: list[dict] = []

    for i, s in enumerate(samples):
        sid = s["id"]
        audio = (manifest_path.parent / Path(s["audio"])).resolve()
        ref_text = s.get("reference_text", "")
        ref_segs = [Segment(**x) for x in s.get("reference_segments", [])]
        sample_sha = _file_sha256(audio)
        try:
            audio.relative_to(testdata_root)
        except ValueError:
            print(f"!! refusing to process {sid}: audio {audio} outside approved testdata root")
            return 1

        ckpt_path = ckpt_dir / f"{sid}.json"
        # Recovery: if checkpoint exists and matches input/config hash, reuse
        if ckpt_path.exists():
            try:
                ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
                if (ck.get("input_sha256") == sample_sha
                        and ck.get("schema_version") == SHORT_SCHEMA_VERSION
                        and ck.get("config_hash") == config_hash
                        and ck.get("model_id") == model_id
                        and ck.get("revision") == revision
                        and sid != args.diagnostic_sample_id):
                    print(f"   [{i+1}/{len(samples)}] {sid} reusing checkpoint")
                    rows.append(ck)
                    if ck.get("ok") is not True:
                        threshold_failures.append({
                            "id": sid,
                            "failed_checks": [c for c in ck.get("checks", []) if not c.get("pass")],
                        })
                    continue
            except (json.JSONDecodeError, OSError):
                pass

        sample_started = time.monotonic()
        t0 = time.monotonic()
        try:
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            result = model.generate(input=str(audio), batch_size_s=60, is_final=True)
        except Exception as e:  # noqa: BLE001
            failures.append({"id": sid, "error": f"{type(e).__name__}: {e}"})
            print(f"   [{i+1}/{len(samples)}] {sid} FAIL: {e}")
            continue
        pure_inference_s = time.monotonic() - t0
        if isinstance(result, list) and result:
            result = result[0]
        # Extract text
        full_text = (result or {}).get("text", "")
        if not full_text:
            info = (result or {}).get("sentence_info") or []
            full_text = "".join(x.get("text", "") for x in info)
        sentence_info = (result or {}).get("sentence_info") or []
        hyp_segs = [Segment(start_ms=int(x.get("start", 0)), end_ms=int(x.get("end", 0)),
                            text=x.get("text", "")) for x in sentence_info]

        # Audio duration
        import soundfile as sf
        dur_s = sf.info(str(audio)).frames / sf.info(str(audio)).samplerate

        cm = code_metrics(ref_text, full_text) if ref_text else None
        bm = bim_term_metrics(ref_text, full_text) if ref_text else None
        sm = segment_metrics(ref_segs, hyp_segs) if ref_segs else None
        peak_vram_gb = torch.cuda.max_memory_allocated() / (1024 ** 3)
        sample_rtf = rtf(dur_s, pure_inference_s)
        checks: list[dict] = []

        def add_check(name: str, observed: float, threshold: float, op: str) -> None:
            passed = observed <= threshold if op == "<=" else observed >= threshold
            checks.append({"name": name, "observed": observed,
                           "threshold": threshold, "operator": op, "pass": passed})

        cer_limit = (cfg.thresholds.cer_max_bim
                     if s.get("scenario") in {"bim_terms", "noise_with_bim"}
                     else cfg.thresholds.cer_max_clear)
        if ref_text:
            add_check("cer", cer(ref_text, full_text), cer_limit, "<=")
        if bm and s.get("scenario") in {"bim_terms", "noise_with_bim"}:
            add_check("bim_term_recall", bm.recall,
                      cfg.thresholds.bim_term_recall_min, ">=")
        if cm and (cm.true_positive + cm.false_negative) > 0:
            add_check("code_recall", cm.recall, cfg.thresholds.code_recall_min, ">=")
        if sm:
            add_check("timestamp_p95_drift_ms", sm.start_drift_p95_ms,
                      cfg.thresholds.timestamp_p95_drift_ms_short, "<=")
        add_check("rtf", sample_rtf, cfg.thresholds.rtf_max, "<=")
        add_check("peak_vram_gb", peak_vram_gb,
                  cfg.thresholds.asr_peak_vram_gb, "<=")
        verdict_ok = all(c["pass"] for c in checks)

        row = {
            "schema_version": SHORT_SCHEMA_VERSION,
            "id": sid,
            "input_sha256": sample_sha,
            "audio_path": str(audio),
            "source_id": s.get("source_id", s.get("source_url", "")),
            "annotation_version": s.get("annotation_version", ""),
            "scenario": s.get("scenario", ""),
            "license": s.get("license", ""),
            "model_id": model_id, "revision": revision,
            "config_hash": config_hash,
            "device": args.device,
            "audio_duration_s": round(dur_s, 3),
            "pure_inference_s": round(pure_inference_s, 3),
            "end_to_end_s": round(time.monotonic() - sample_started, 3),
            "rtf": round(sample_rtf, 4),
            "realtime_speedup": round(realtime_speedup(dur_s, pure_inference_s), 4),
            "cer_norm_version": CER_NORM_VERSION,
            "cer": round(cer(ref_text, full_text), 4) if ref_text else None,
            "code_metrics": cm.as_dict() if cm else None,
            "bim_term_metrics": bm.as_dict() if bm else None,
            "segment_metrics": sm.as_dict() if sm else None,
            "peak_vram_gb": round(peak_vram_gb, 4),
            "checks": checks,
            "ok": verdict_ok,
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        }
        # The checkpoint is always text-free. Explicit diagnostic text is
        # added only to the in-memory report row after the atomic checkpoint.
        _atomic_json_dump(ckpt_path, row)
        if sid == args.diagnostic_sample_id:
            row = {
                **row,
                "diagnostic": {
                    "non_sensitive_self_made": True,
                    "reference_text": ref_text,
                    "hypothesis_text": full_text,
                    "character_diff": _character_diff(ref_text, full_text),
                },
            }
        rows.append(row)
        if not verdict_ok:
            threshold_failures.append({
                "id": sid,
                "failed_checks": [c for c in checks if not c["pass"]],
            })
        print(f"   [{i+1}/{len(samples)}] {sid} verdict={'PASS' if verdict_ok else 'FAIL'} "
              f"rtf={row['rtf']} cer={row['cer']}")

    # 0% failure rate required
    failure_rate = len(failures) / len(samples) * 100.0
    if failure_rate > cfg.thresholds.short_failure_rate_pct or threshold_failures:
        print(f"!! processing failures={len(failures)} threshold failures={len(threshold_failures)}")
        # Write report anyway, but exit non-zero
        _write_report(cfg, rows, failures, threshold_failures, cold_start_s,
                      warm_up_s, model_load_meta, config_hash, args.out,
                      args.diagnostic_sample_id)
        return 2

    _write_report(cfg, rows, failures, threshold_failures, cold_start_s,
                  warm_up_s, model_load_meta, config_hash, args.out,
                  args.diagnostic_sample_id)
    return 0


def _write_report(cfg, rows, failures, threshold_failures, cold_start_s, warm_up_s,
                  model_load_meta, config_hash, out_override=None,
                  diagnostic_sample_id=None) -> None:
    reports_dir = Path(cfg.reports_root) / cfg.run_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = (Path(out_override).resolve() if out_override else
           reports_dir / f"03_run_short-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
    try:
        out.relative_to(reports_dir.resolve())
    except ValueError as e:
        raise ValueError(f"report output must stay under {reports_dir}: {out}") from e
    payload = {
        "schema_version": SHORT_SCHEMA_VERSION,
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "model": model_load_meta,
        "config_hash": config_hash,
        "diagnostic_sample_id": diagnostic_sample_id,
        "timing": {
            "cold_start_s": round(cold_start_s, 3),
            "warm_up_s": round(warm_up_s, 3),
        },
        "n_samples": len(rows),
        "n_failures": len(failures),
        "failures": failures,
        "n_threshold_failures": len(threshold_failures),
        "threshold_failures": threshold_failures,
        "ok": not failures and not threshold_failures,
        "rows": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
