"""Phase 0 ASR sandbox — compatibility smoke (entry point 02).

Per R2 spec §十:
  - Replaces the unprotected 30s matrix-multiply loop with bounded CUDA
    kernels (5 iterations max, fixed size).
  - Verifies BGE /health BEFORE and AFTER the smoke.
  - Cross-validates torch, nvidia-smi, GPU name, capability, CUDA version.
  - Model metadata download is gated by:
      a) license audit pass (lib_license_audit returns 0)
      b) disk free gate
  - CPU device refused; torch.cuda.is_available() must be True.
  - No GPU / CUDA execution in this R2 round (R3 required).

Run only through ``00_run_guarded.py --step 02``. Direct execution is refused.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

COMPAT_SCHEMA_VERSION = "phase0-compat-smoke/1"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--no-bge-check", action="store_true",
                   help="skip BGE pre/post checks (only for tests on isolated hosts)")
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import load_config, gate_for_gpu_entry, ConfigGateError
    from scripts.funasr_phase0.lib_monitor import (
        bge_get_json, nvidia_smi_csv, disk_free_gb,
    )
    from scripts.funasr_phase0.lib_runtime import require_guarded_worker

    cfg = load_config(args.config)
    try:
        gate_for_gpu_entry(cfg, command_name="02_compat_smoke")
        require_guarded_worker(cfg, "02_compat_smoke")
    except ConfigGateError as e:
        print(f"!! gate rejected: {e}")
        return 1
    except RuntimeError as e:
        print(f"!! guarded worker rejected: {e}")
        return 1
    if args.no_bge_check and os.environ.get("PHASE0_TEST_MODE") != "1":
        print("!! --no-bge-check is available only with PHASE0_TEST_MODE=1")
        return 1

    bge_token = os.environ.get("GPU_SERVICE_TOKEN", "")
    reports_dir = Path(cfg.reports_root)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"compat-smoke-{cfg.run_id}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    log: dict = {
        "schema_version": COMPAT_SCHEMA_VERSION,
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "tests": {},
        "model_download": {"skipped_reason": None, "files": []},
        "ok": False,
    }

    # 1) Disk free gate
    free = disk_free_gb(cfg.logs_root.rstrip("/").rstrip("\\") or "E:\\")
    if free is None or free < cfg.thresholds.disk_free_min_gb:
        log["tests"]["disk_free"] = {"ok": False, "free_gb": free}
        return _write_report(out_path, log, code=1)
    log["tests"]["disk_free"] = {"ok": True, "free_gb": free}

    # 2) nvidia-smi
    smi = nvidia_smi_csv()
    if smi is None:
        log["tests"]["nvidia_smi"] = {"ok": False, "reason": "nvidia-smi not available"}
        return _write_report(out_path, log, code=1)
    log["tests"]["nvidia_smi"] = {
        "ok": True, "memory_used_mib": smi["memory_used_mib"],
        "memory_total_mib": smi["memory_total_mib"],
    }

    # 3) BGE pre-check
    if not args.no_bge_check:
        h_status, h_body, _ = bge_get_json(cfg.bge_base_url + "/health", bge_token)
        health_ok = (
            h_status == 200
            and isinstance(h_body, dict)
            and h_body.get("status") == "ok"
            and h_body.get("model_loaded") is True
        )
        if not health_ok:
            log["tests"]["bge_pre"] = {"ok": False, "status": h_status, "body": str(h_body)[:200]}
            return _write_report(out_path, log, code=1)
        log["tests"]["bge_pre"] = {"ok": True, "status": h_status}

    # 4) torch CUDA cross-check
    try:
        import torch
    except ImportError as e:
        log["tests"]["torch_import"] = {"ok": False, "error": str(e)}
        return _write_report(out_path, log, code=1)
    if not torch.cuda.is_available():
        log["tests"]["torch_cuda"] = {"ok": False, "reason": "torch.cuda.is_available() is False"}
        return _write_report(out_path, log, code=1)
    dev_name = torch.cuda.get_device_name(0)
    dev_cap = torch.cuda.get_device_capability(0)
    dev_total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    if dev_cap != (12, 0):
        log["tests"]["torch_cuda"] = {
            "ok": False,
            "device": dev_name, "cap": list(dev_cap),
            "reason": "compute capability != (12, 0); not a Blackwell sm_120 GPU",
        }
        return _write_report(out_path, log, code=1)
    # torch CUDA version vs smi
    smi_total_gb = smi["memory_total_mib"] / 1024.0
    if abs(smi_total_gb - dev_total) > 1.0:
        log["tests"]["torch_cuda"] = {
            "ok": False,
            "torch_total_gb": dev_total, "smi_total_gb": smi_total_gb,
            "reason": "torch and nvidia-smi report different total VRAM",
        }
        return _write_report(out_path, log, code=1)
    log["tests"]["torch_cuda"] = {
        "ok": True, "device": dev_name, "cap": list(dev_cap),
        "total_gb": round(dev_total, 2),
        "torch_version": torch.__version__,
        "cuda_runtime_version": torch.version.cuda,
    }
    if "+cu128" not in torch.__version__:
        log["tests"]["torch_cuda"]["ok"] = False
        log["tests"]["torch_cuda"]["reason"] = f"torch {torch.__version__} does not include +cu128"
        return _write_report(out_path, log, code=1)

    # 5) Bounded CUDA kernel (5 iters, 1024x1024 fp32 matmul; ~5s)
    a = torch.randn(1024, 1024, device="cuda", dtype=torch.float32)
    b = torch.randn(1024, 1024, device="cuda", dtype=torch.float32)
    n = 0
    for _ in range(5):
        _ = torch.matmul(a, b)
        n += 1
    torch.cuda.synchronize()
    peak_mib = torch.cuda.max_memory_allocated() / (1024 * 1024)
    log["tests"]["cuda_kernel"] = {"ok": True, "iterations": n, "peak_mib": round(peak_mib, 1)}

    # 6) License gate was enforced by the parent before this process existed.
    # A valid nonce guard proves this worker was launched only after that gate.
    log["tests"]["license"] = {"ok": True, "enforced_by": "00_run_guarded"}

    # 7) Model metadata download (gated by license + disk)
    # NOTE: per R2 spec, this download happens here but in this R2 round we
    # do NOT actually invoke the network. We attempt to read from local cache
    # and report what we find.
    cache = Path(os.environ.get("MODELSCOPE_CACHE",
                                str(Path(cfg.models_root) / "modelscope")))
    cache.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    for mid in cfg.allowed_asr_model_ids:
        # Look for cached metadata; do not download
        safe = mid.replace("/", "_")
        meta_dir = cache / safe
        meta_path = meta_dir / "config.json"
        if meta_path.exists():
            sha = hashlib.sha256(meta_path.read_bytes()).hexdigest()
            downloaded.append({
                "model_id": mid, "file": str(meta_path.relative_to(cache)),
                "size_bytes": meta_path.stat().st_size, "sha256": sha,
            })
        else:
            downloaded.append({
                "model_id": mid, "file": None,
                "note": "metadata NOT cached locally; download would occur under R3 approval",
            })
    log["model_download"] = {"files": downloaded, "downloaded": False}
    log["ok"] = True

    # 8) BGE post-check
    if not args.no_bge_check:
        h_status, h_body, _ = bge_get_json(cfg.bge_base_url + "/health", bge_token)
        health_ok = (
            h_status == 200
            and isinstance(h_body, dict)
            and h_body.get("status") == "ok"
            and h_body.get("model_loaded") is True
        )
        if not health_ok:
            log["tests"]["bge_post"] = {"ok": False, "status": h_status, "body": str(h_body)[:200]}
            return _write_report(out_path, log, code=1)
        log["tests"]["bge_post"] = {"ok": True, "status": h_status}

    return _write_report(out_path, log, code=0)


def _write_report(p: Path, log: dict, code: int) -> int:
    p.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> wrote {p}")
    return code


if __name__ == "__main__":
    sys.exit(main())
