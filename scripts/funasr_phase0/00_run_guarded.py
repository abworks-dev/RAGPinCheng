"""Only supported launcher for Phase 0 GPU workers 02/03/04."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--step", required=True, choices=["02", "03", "04"])
    args, worker_args = p.parse_known_args(argv)
    if "--config" in worker_args:
        p.error("worker_args must not override --config")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.funasr_phase0.lib_config import (
        ConfigGateError, gate_for_gpu_entry, load_config,
    )
    from scripts.funasr_phase0.lib_runtime import (
        GuardedProcess, atomic_json_dump, load_matching_baseline, worker_command,
    )

    cfg = load_config(args.config)
    try:
        gate_for_gpu_entry(cfg, command_name=f"00_run_guarded:{args.step}")
        baseline = load_matching_baseline(cfg)
    except (ConfigGateError, RuntimeError) as e:
        print(f"!! guarded launch rejected: {e}")
        return 1

    names = {
        "02": ("02_compat_smoke", "02_compat_smoke.py"),
        "03": ("03_run_short", "03_run_short.py"),
        "04": ("04_run_long", "04_run_long.py"),
    }
    command_name, script = names[args.step]
    command = worker_command(script, ["--config", str(Path(args.config).resolve()),
                                      *worker_args])
    runtime = GuardedProcess(cfg, args.config, command_name, command, baseline=baseline)
    try:
        runtime.start()
        rc = runtime.wait()
    except Exception as e:  # noqa: BLE001
        print(f"!! guarded worker failed: {type(e).__name__}: {e}")
        rc = 1
    finally:
        result = runtime.close(verify=runtime.proc is not None)

    report = {
        "schema_version": "phase0-guarded-run/1",
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "step": args.step,
        "worker_returncode": rc,
        "monitor": result["monitor"],
        "recovery": result["recovery"],
        "termination_error": result["termination_error"],
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    report["ok"] = (
        rc == 0
        and not report["monitor"].get("stop_reason")
        and report["termination_error"] is None
        and bool(report["recovery"] and report["recovery"].get("ok"))
    )
    out = (Path(cfg.reports_root) / cfg.run_id /
           f"guarded-{args.step}-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
    atomic_json_dump(out, report)
    print(f">> wrote {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
