"""Phase 0 ASR sandbox — BGE baseline measurement (entry point 01).

Per R2 spec §十三:
  - Health response parsed via JSON ({"status":"ok","model_loaded":true}).
  - /model-info fingerprint (model + reranker + device + torch version)
    captured at start; required to match config expectations.
  - Embedding and Rerank latencies tracked separately; P95 NOT mixed.
  - Strict 20 Embed RPM + 10 Rerank RPM pacing (not "30 req/min blended").
  - Abort report (failure) and success report use distinct types and
    different file names.
  - Schema version written into every report.
  - Only `ok=true` AND target/config-matched baselines are loadable by
    later coexistence tests (validated by reference_target_id and
    config_sha256 fields).

Run on the production Windows GPU host after license gate + config gate.

Run:
  C:\\FunASR-Phase0\\venv\\Scripts\\python.exe ^
    E:\\Repository\\Github\\RAGPinCheng\\scripts\\funasr_phase0\\01_measure_bge_baseline.py ^
    --config E:\\path\\to\\phase0-config.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

BASELINE_SCHEMA_VERSION = "phase0-bge-baseline/1"
BASELINE_ABORT_SCHEMA_VERSION = "phase0-bge-baseline-abort/1"

REPORT_KIND_SUCCESS = "bge_baseline"
REPORT_KIND_ABORT = "bge_baseline_abort"

# Non-sensitive synthetic Chinese text. Hard-coded; not from any production
# corpus, customer document, or training material. Composed for BGE probe only.
SYNTH_EMBED_BASE = (
    "BGE 基线测试使用的中文文本，涵盖工程叙述与监控场景，"
    "无任何客户或敏感信息。"
)
SYNTH_RERANK_QUERIES = [
    "BGE 基线测试",
    "合成监控文本",
    "GPU 推理延迟",
    "重排模型候选段落",
]


def _post_json(url: str, body: dict, token: str, timeout: float = 10.0) -> tuple[int, float]:
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return r.status, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        try:
            e.read()
        except Exception:  # noqa: BLE001
            pass
        return e.code, time.monotonic() - t0
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, time.monotonic() - t0


def _get_json(url: str, token: str, timeout: float = 5.0) -> tuple[int, dict | str, float]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(body), time.monotonic() - t0
            except json.JSONDecodeError:
                return r.status, body, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", errors="replace"), time.monotonic() - t0
        except Exception:  # noqa: BLE001
            return e.code, "", time.monotonic() - t0
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e), time.monotonic() - t0


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    arr = sorted(xs)
    if len(arr) == 1:
        return float(arr[0])
    k = (len(arr) - 1) * (q / 100.0)
    f = int(k)
    c = min(f + 1, len(arr) - 1)
    return float(arr[f] + (arr[c] - arr[f]) * (k - f))


def _config_match(cfg_model_info: dict, expected_model: str, expected_reranker: str,
                  expected_device: str, expected_torch: str) -> tuple[bool, list[str]]:
    """Check BGE /model-info against approved config expectations."""
    mismatches: list[str] = []
    if cfg_model_info.get("embedding_model") != expected_model:
        mismatches.append(f"embedding_model {cfg_model_info.get('embedding_model')!r} != {expected_model!r}")
    if cfg_model_info.get("reranker_model") != expected_reranker:
        mismatches.append(f"reranker_model {cfg_model_info.get('reranker_model')!r} != {expected_reranker!r}")
    if cfg_model_info.get("device") != expected_device:
        mismatches.append(f"device {cfg_model_info.get('device')!r} != {expected_device!r}")
    if cfg_model_info.get("torch_version") != expected_torch:
        mismatches.append(f"torch_version {cfg_model_info.get('torch_version')!r} != {expected_torch!r}")
    return (len(mismatches) == 0, mismatches)


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="phase0-config.json")
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config, gate_for_gpu_entry, ConfigGateError
    cfg = load_config(args.config)

    try:
        gate_for_gpu_entry(cfg, command_name="01_measure_bge_baseline")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1

    bge_token = os.environ.get("GPU_SERVICE_TOKEN", "")
    base = cfg.bge_base_url
    embed_url = base + "/v1/embeddings"
    rerank_url = base + "/v1/rerank"
    health_url = base + "/health"
    model_info_url = base + "/model-info"

    # Health (JSON)
    h_status, h_body, h_lat = _get_json(health_url, bge_token)
    health_ok = (
        h_status == 200
        and isinstance(h_body, dict)
        and h_body.get("status") == "ok"
        and h_body.get("model_loaded") is True
    )
    if not health_ok:
        _write_abort(cfg, base, reason=f"health_not_ok status={h_status} body={h_body}")
        return 1

    # /model-info fingerprint
    mi_status, mi_body, _ = _get_json(model_info_url, bge_token)
    if not (mi_status == 200 and isinstance(mi_body, dict)):
        _write_abort(cfg, base, reason=f"model_info_not_ok status={mi_status}")
        return 1
    matches, mismatches = _config_match(mi_body, cfg.bge_expected_model, cfg.bge_expected_reranker,
                                        cfg.bge_expected_device, cfg.bge_expected_torch_version)
    if not matches:
        _write_abort(cfg, base, reason=f"model_info_mismatch {mismatches}")
        return 1

    # Strict pacing
    embed_period = 60.0 / max(1, cfg.embed_rpm)
    rerank_period = 60.0 / max(1, cfg.rerank_rpm)
    duration_s = cfg.baseline_duration_s

    samples: list[dict] = []
    embed_latencies: list[float] = []
    rerank_latencies: list[float] = []
    n_embed = n_rerank = 0
    n_embed_err = n_rerank_err = 0

    t_end = time.monotonic() + duration_s
    t_next_embed = time.monotonic()
    t_next_rerank = time.monotonic() + 1.0
    t_next_health = time.monotonic() + 5.0
    i = 0
    while time.monotonic() < t_end:
        now = time.monotonic()
        if now >= t_next_embed:
            t_next_embed += embed_period
            text = (SYNTH_EMBED_BASE * 25)[:1000]
            s, lat = _post_json(embed_url, {"texts": [text], "normalize": True}, bge_token)
            samples.append({"kind": "embed", "status": s, "latency_s": lat, "at": now})
            embed_latencies.append(lat)
            n_embed += 1
            if s != 200:
                n_embed_err += 1
            current_error = n_embed_err / n_embed * 100.0
            if current_error > cfg.thresholds.bge_error_rate_pct:
                _write_abort(cfg, base, reason=f"embed_error_rate_pct={current_error:.3f}")
                return 2
        if now >= t_next_rerank:
            t_next_rerank += rerank_period
            passages = [(SYNTH_EMBED_BASE * 2)[:200] for _ in range(50)]
            s, lat = _post_json(
                rerank_url,
                {"query": SYNTH_RERANK_QUERIES[n_rerank % len(SYNTH_RERANK_QUERIES)],
                 "passages": passages, "use_header": True},
                bge_token,
            )
            samples.append({"kind": "rerank", "status": s, "latency_s": lat, "at": now})
            rerank_latencies.append(lat)
            n_rerank += 1
            if s != 200:
                n_rerank_err += 1
            current_error = n_rerank_err / n_rerank * 100.0
            if current_error > cfg.thresholds.bge_error_rate_pct:
                _write_abort(cfg, base, reason=f"rerank_error_rate_pct={current_error:.3f}")
                return 2
        if now >= t_next_health:
            t_next_health += 5.0
            h_status, h_body, _ = _get_json(health_url, bge_token)
            if not (h_status == 200 and isinstance(h_body, dict)
                    and h_body.get("status") == "ok"
                    and h_body.get("model_loaded") is True):
                _write_abort(cfg, base, reason=f"health_degraded_during_baseline status={h_status}")
                return 2
        time.sleep(max(0.02, min(t_next_embed, t_next_rerank) - time.monotonic()))

    embed_err_rate = (n_embed_err / n_embed * 100.0) if n_embed else 0.0
    rerank_err_rate = (n_rerank_err / n_rerank * 100.0) if n_rerank else 0.0
    embed_p50 = _percentile(embed_latencies, 50)
    embed_p95 = _percentile(embed_latencies, 95)
    embed_p99 = _percentile(embed_latencies, 99)
    rerank_p50 = _percentile(rerank_latencies, 50)
    rerank_p95 = _percentile(rerank_latencies, 95)
    rerank_p99 = _percentile(rerank_latencies, 99)

    # Compute target_id and config_hash for fingerprint
    target_id = hashlib.sha256(
        f"{cfg.bge_base_url}|{cfg.bge_expected_model}|{cfg.bge_expected_reranker}|"
        f"{cfg.bge_expected_device}|{cfg.bge_expected_torch_version}".encode("utf-8")
    ).hexdigest()[:16]
    summary = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "report_kind": REPORT_KIND_SUCCESS,
        "ok": (
            embed_err_rate <= cfg.thresholds.bge_error_rate_pct
            and rerank_err_rate <= cfg.thresholds.bge_error_rate_pct
        ),
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "target_id": target_id,
        "bge_base_url": cfg.bge_base_url,
        "expected": {
            "model": cfg.bge_expected_model,
            "reranker": cfg.bge_expected_reranker,
            "device": cfg.bge_expected_device,
            "torch_version": cfg.bge_expected_torch_version,
        },
        "fingerprint": mi_body,
        "duration_s": duration_s,
        "n_embed": n_embed, "n_rerank": n_rerank,
        "n_embed_errors": n_embed_err, "n_rerank_errors": n_rerank_err,
        "embed_error_rate_pct": round(embed_err_rate, 3),
        "rerank_error_rate_pct": round(rerank_err_rate, 3),
        "embed_p50_s": round(embed_p50, 4),
        "embed_p95_s": round(embed_p95, 4),
        "embed_p99_s": round(embed_p99, 4),
        "embed_max_s": round(max(embed_latencies, default=float("nan")), 4),
        "rerank_p50_s": round(rerank_p50, 4),
        "rerank_p95_s": round(rerank_p95, 4),
        "rerank_p99_s": round(rerank_p99, 4),
        "rerank_max_s": round(max(rerank_latencies, default=float("nan")), 4),
        "samples_count": len(samples),
    }
    # sidecar
    sidecar = {
        "schema_version": BASELINE_SCHEMA_VERSION + "/samples",
        "samples": samples,
    }
    reports_dir = Path(cfg.reports_root) / cfg.run_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_json = reports_dir / f"bge-baseline-{cfg.run_id}-{stamp}.json"
    tmp_json = out_json.with_suffix(out_json.suffix + ".tmp")
    tmp_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_json, out_json)
    out_samples = reports_dir / f"bge-baseline-{cfg.run_id}-{stamp}.samples.jsonl"
    with out_samples.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f">> wrote {out_json}")
    print(f">> wrote {out_samples}")
    print(
        f"embed p50={embed_p50*1000:.0f}ms p95={embed_p95*1000:.0f}ms err={embed_err_rate:.2f}% | "
        f"rerank p50={rerank_p50*1000:.0f}ms p95={rerank_p95*1000:.0f}ms err={rerank_err_rate:.2f}%"
    )
    return 0 if summary["ok"] else 2


def _write_abort(cfg, base: str, reason: str) -> None:
    import datetime as dt
    reports_dir = Path(cfg.reports_root) / cfg.run_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"bge-baseline-abort-{cfg.run_id}-{stamp}.json"
    payload = {
        "schema_version": BASELINE_ABORT_SCHEMA_VERSION,
        "report_kind": REPORT_KIND_ABORT,
        "ok": False,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "bge_base_url": base,
        "reason": reason,
    }
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(f"!! abort wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
