"""Phase 0 ASR sandbox — BGE coexistence test (entry point 05).

Per R2 spec §十三:
  - DELETES local BGE copy code: no second instance, no port 18100, no
    test-only BGE token, no BGE weights staging.
  - NEVER imports or launches services.gpu_service.app from inside the ASR sandbox.
  - Same BGE instance for baseline and coexistence: keep
    bge_base_url, bge_expected_model, request body shape, RPM.
  - Steps:
      1. ASR startup: collect BGE-only baseline (already done by 01).
      2. Read latest ok=true + target_id-matching + config-matching baseline.
      3. Start independent ASR worker subprocess (04_run_long.py).
      4. Send identical embed + rerank traffic at the same RPM.
      5. Monitor health, error rate, p95, VRAM, ASR process.
      6. Any threshold breach stops the ASR worker immediately.
      7. After stop, full BGE verification.
  - Embed and Rerank compared separately (not mixed).
  - Do NOT wait 1h before judging degradation; rolling window compares
    baseline vs current every 60s.

Run on the production Windows GPU host as a separate process. The monitor
subprocess is started by the entry script and bounds the ASR run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

COEXIST_SCHEMA_VERSION = "phase0-bge-coexist/1"

# Hard-coded synthetic non-sensitive Chinese text (mirrors 01 baseline).
SYNTH_EMBED_BASE = (
    "BGE 共存测试使用的中文文本，涵盖工程叙述与监控场景，"
    "无任何客户或敏感信息。"
)
SYNTH_RERANK_QUERIES = [
    "BGE 共存测试", "合成监控文本", "GPU 推理延迟", "重排模型候选段落",
]


def _post_json(url: str, body: dict, token: str, timeout: float = 5.0) -> tuple[int, float]:
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


def _get_json(url: str, token: str, timeout: float = 3.0) -> tuple[int, dict | str, float]:
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


def _target_id_for(cfg) -> str:
    import hashlib as _h
    return _h.sha256(
        f"{cfg.bge_base_url}|{cfg.bge_expected_model}|{cfg.bge_expected_reranker}|"
        f"{cfg.bge_expected_device}|{cfg.bge_expected_torch_version}".encode("utf-8")
    ).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--asr-input", required=True,
                   help="path to 1h audio/video for ASR (must be inside testdata_root)")
    p.add_argument("--asr-reference", required=True,
                   help="reviewed reference JSON for the exact ASR input")
    p.add_argument("--duration-s", type=float, default=3600.0)
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config, gate_for_gpu_entry, ConfigGateError
    from scripts.funasr_phase0.lib_runtime import (
        GuardedProcess, atomic_json_dump, load_matching_baseline, worker_command,
    )
    cfg = load_config(args.config)
    try:
        gate_for_gpu_entry(cfg, command_name="05_bge_coexist")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1

    testdata_root = Path(cfg.testdata_root).resolve()
    asr_input = Path(args.asr_input).resolve()
    try:
        asr_input.relative_to(testdata_root)
    except ValueError:
        print(f"!! asr-input {asr_input} is NOT inside testdata_root {testdata_root}")
        return 1
    if not asr_input.exists():
        print(f"!! asr-input not found: {asr_input}")
        return 1
    asr_reference = Path(args.asr_reference).resolve()
    try:
        asr_reference.relative_to(testdata_root)
    except ValueError:
        print(f"!! asr-reference {asr_reference} is NOT inside testdata_root {testdata_root}")
        return 1
    if not asr_reference.is_file():
        print(f"!! asr-reference not found: {asr_reference}")
        return 1

    bge_token = os.environ.get("GPU_SERVICE_TOKEN", "")
    base = cfg.bge_base_url
    target_id = _target_id_for(cfg)
    reports_dir = Path(cfg.reports_root)

    # 1) Load baseline (must be ok=true + matching target_id + config)
    try:
        baseline = load_matching_baseline(cfg)
    except RuntimeError as e:
        print(f"!! {e}")
        return 1
    b_embed_p95 = baseline["embed_p95_s"]
    b_rerank_p95 = baseline["rerank_p95_s"]
    b_embed_err = baseline["embed_error_rate_pct"]
    b_rerank_err = baseline["rerank_error_rate_pct"]
    print(f">> baseline ok: embed_p95={b_embed_p95*1000:.0f}ms err={b_embed_err:.2f}% | "
          f"rerank_p95={b_rerank_p95*1000:.0f}ms err={b_rerank_err:.2f}%")

    # 2) Start ASR worker (independent process via 04_run_long.py)
    asr_label = f"coexist-{dt.datetime.now():%Y%m%d-%H%M%S}"
    asr_log = reports_dir / cfg.run_id / "05_coexist-asr.log"
    asr_log.parent.mkdir(parents=True, exist_ok=True)
    asr_out = reports_dir / cfg.run_id / f"05_coexist-asr-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    cmd = worker_command("04_run_long.py", [
        "--config", str(Path(args.config).resolve()),
        "--input", str(asr_input),
        "--reference", str(asr_reference),
        "--label", asr_label,
        "--out", str(asr_out),
    ])
    print(f">> starting ASR: {' '.join(cmd)}")
    asr_log_f = open(asr_log, "ab", buffering=0)
    runtime = GuardedProcess(
        cfg, args.config, "04_run_long", cmd, baseline=baseline,
        monitor_probes=False, stdout=asr_log_f,
    )
    try:
        runtime.start()
    except Exception as e:  # noqa: BLE001
        runtime.close(verify=False)
        asr_log_f.close()
        print(f"!! guarded ASR start failed: {type(e).__name__}: {e}")
        return 1

    # 3) Send identical traffic at the same RPM
    embed_period = 60.0 / max(1, cfg.embed_rpm)
    rerank_period = 60.0 / max(1, cfg.rerank_rpm)
    t_end = time.monotonic() + args.duration_s
    t_next_embed = time.monotonic()
    t_next_rerank = time.monotonic() + 1.0
    i = 0
    controller_error: str | None = None

    try:
        while time.monotonic() < t_end:
            if runtime.poll() is not None:
                break
            snap = runtime.monitor.snapshot()
            if snap.get("stop_reason"):
                controller_error = f"monitor stop: {snap['stop_reason']}"
                break
            now = time.monotonic()
            if now >= t_next_embed:
                t_next_embed += embed_period
                text = (SYNTH_EMBED_BASE * 25)[:1000]
                s, lat = _post_json(base + "/v1/embeddings",
                                    {"texts": [text], "normalize": True}, bge_token)
                runtime.monitor.record_bge_result("embed", s, lat)
            if now >= t_next_rerank:
                t_next_rerank += rerank_period
                passages = [(SYNTH_EMBED_BASE * 2)[:200] for _ in range(50)]
                s, lat = _post_json(base + "/v1/rerank",
                                    {"query": SYNTH_RERANK_QUERIES[i % len(SYNTH_RERANK_QUERIES)],
                                     "passages": passages, "use_header": True}, bge_token)
                runtime.monitor.record_bge_result("rerank", s, lat)
                i += 1
            time.sleep(max(0.02, min(t_next_embed, t_next_rerank) - time.monotonic()))
    except Exception as e:  # noqa: BLE001
        controller_error = f"controller failure: {type(e).__name__}: {e}"
    finally:
        if controller_error or runtime.poll() is None:
            try:
                runtime.terminate()
            except Exception as e:  # noqa: BLE001
                controller_error = controller_error or f"termination failure: {type(e).__name__}: {e}"
        try:
            asr_rc = runtime.wait(timeout=20)
        except Exception as e:  # noqa: BLE001
            asr_rc = -9
            controller_error = controller_error or f"wait failure: {type(e).__name__}: {e}"
        runtime_result = runtime.close(verify=True)
        asr_log_f.close()
    snap = runtime_result["monitor"]
    recovery = runtime_result["recovery"] or {"ok": False, "error": "missing recovery"}
    termination_error = runtime_result["termination_error"]
    embed_p95_delta = ((snap.get("embed_p95_ms", 0) / 1000 - b_embed_p95)
                       / b_embed_p95 * 100.0) if b_embed_p95 > 0 else 0.0
    rerank_p95_delta = ((snap.get("rerank_p95_ms", 0) / 1000 - b_rerank_p95)
                        / b_rerank_p95 * 100.0) if b_rerank_p95 > 0 else 0.0

    summary = {
        "schema_version": COEXIST_SCHEMA_VERSION,
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "target_id": target_id,
        "baseline": {
            "embed_p95_s": b_embed_p95, "embed_err_pct": b_embed_err,
            "rerank_p95_s": b_rerank_p95, "rerank_err_pct": b_rerank_err,
        },
        "coexist": {
            "embed_n": snap.get("embed_n", 0),
            "embed_p50_s": snap.get("embed_p50_ms", 0) / 1000,
            "embed_p95_s": snap.get("embed_p95_ms", 0) / 1000,
            "embed_p99_s": snap.get("embed_p99_ms", 0) / 1000,
            "embed_err_rate_pct": snap.get("embed_error_rate_pct", 100.0),
            "embed_p95_delta_pct": round(embed_p95_delta, 2),
            "rerank_n": snap.get("rerank_n", 0),
            "rerank_p50_s": snap.get("rerank_p50_ms", 0) / 1000,
            "rerank_p95_s": snap.get("rerank_p95_ms", 0) / 1000,
            "rerank_p99_s": snap.get("rerank_p99_ms", 0) / 1000,
            "rerank_err_rate_pct": snap.get("rerank_error_rate_pct", 100.0),
            "rerank_p95_delta_pct": round(rerank_p95_delta, 2),
        },
        "monitor": snap,
        "controller_error": controller_error,
        "termination_error": termination_error,
        "asr_returncode": asr_rc,
        "bge_recovery": recovery,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
    }
    summary["ok"] = (
        controller_error is None and termination_error is None and asr_rc == 0
        and not snap.get("stop_reason") and recovery.get("ok") is True
        and snap.get("embed_n", 0) > 0 and snap.get("rerank_n", 0) > 0
        and snap.get("embed_error_rate_pct", 100) <= cfg.thresholds.bge_error_rate_pct
        and snap.get("rerank_error_rate_pct", 100) <= cfg.thresholds.bge_error_rate_pct
        and embed_p95_delta <= cfg.thresholds.bge_p95_degradation_pct
        and rerank_p95_delta <= cfg.thresholds.bge_p95_degradation_pct
    )
    out = reports_dir / cfg.run_id / f"05_bge_coexist-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_dump(out, summary)
    print(f">> wrote {out}")
    print(f">> verdict ok={summary['ok']} stop_reason={snap.get('stop_reason')} recovery={recovery.get('ok')}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
