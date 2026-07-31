"""Guarded subprocess runtime for FunASR Phase 0.

GPU worker scripts (02/03/04) are intentionally not directly runnable.  A
parent controller creates a short-lived nonce file, starts the worker in an
independent process group, records ``active-runs/<run_id>.json``, and owns the
monitor/termination lifecycle.  This keeps auto-stop outside the process that
may be blocked in CUDA.
"""
from __future__ import annotations

import datetime as dt
import json
import hashlib
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from scripts.funasr_phase0.lib_config import Config
from scripts.funasr_phase0.lib_monitor import (
    Monitor,
    MonitorConfig,
    bge_get_json,
    bge_ping,
)

RUNTIME_SCHEMA_VERSION = "phase0-runtime/1"
GUARD_ENV_FILE = "PHASE0_GUARD_FILE"
GUARD_ENV_NONCE = "PHASE0_GUARD_NONCE"


def atomic_json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def require_guarded_worker(cfg: Config, command_name: str) -> dict[str, Any]:
    """Reject accidental/direct execution of a GPU worker."""
    raw_path = os.environ.get(GUARD_ENV_FILE, "")
    nonce = os.environ.get(GUARD_ENV_NONCE, "")
    if not raw_path or not nonce:
        raise RuntimeError(
            f"{command_name} is a guarded worker; run it through 00_run_guarded.py"
        )
    guard_path = Path(raw_path).resolve()
    active_root = (Path(cfg.logs_root) / "active-runs").resolve()
    if not _is_under(guard_path, active_root):
        raise RuntimeError("guard file is outside configured active-runs directory")
    try:
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"guard file invalid: {e}") from e
    expected = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "run_id": cfg.run_id,
        "config_sha256": cfg.config_sha256,
        "command_name": command_name,
        "nonce": nonce,
    }
    mismatches = [k for k, value in expected.items() if guard.get(k) != value]
    if mismatches:
        raise RuntimeError(f"guard file mismatch: {mismatches}")
    created = dt.datetime.fromisoformat(str(guard["created_at"]).replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise RuntimeError("guard created_at has no timezone")
    if abs((dt.datetime.now(dt.timezone.utc) - created).total_seconds()) > 300:
        raise RuntimeError("guard file is stale")
    models_root = Path(cfg.models_root).resolve()
    for env_name in ("MODELSCOPE_CACHE", "HF_HOME"):
        raw = os.environ.get(env_name, "")
        if not raw or not _is_under(Path(raw), models_root):
            raise RuntimeError(f"{env_name} must be inside configured models_root")
    return guard


def terminate_process_tree(pid: int) -> None:
    """Terminate exactly one worker process group/tree."""
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"taskkill failed for pid={pid}: "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def verify_bge(cfg: Config, token: str) -> dict[str, Any]:
    """Full health/model identity/5 embed/1 rerank verification."""
    health_status, health, _ = bge_get_json(cfg.bge_base_url + "/health", token)
    model_status, model_info, _ = bge_get_json(cfg.bge_base_url + "/model-info", token)
    health_ok = (
        health_status == 200 and isinstance(health, dict)
        and health.get("status") == "ok" and health.get("model_loaded") is True
    )
    expected = {
        "embedding_model": cfg.bge_expected_model,
        "reranker_model": cfg.bge_expected_reranker,
        "device": cfg.bge_expected_device,
        "torch_version": cfg.bge_expected_torch_version,
    }
    mismatches = (["response_not_json_object"] if not isinstance(model_info, dict)
                  else [f"{k}: {model_info.get(k)!r} != {v!r}"
                        for k, v in expected.items() if model_info.get(k) != v])
    embed_statuses = []
    for i in range(5):
        status, _ = bge_ping(
            cfg.bge_base_url + "/v1/embeddings",
            {"texts": [f"phase0 recovery probe {i + 1}"], "normalize": True},
            token,
        )
        embed_statuses.append(status)
    rerank_status, _ = bge_ping(
        cfg.bge_base_url + "/v1/rerank",
        {"query": "phase0 recovery probe",
         "passages": [f"synthetic passage {i}" for i in range(5)],
         "use_header": True},
        token,
    )
    ok = (health_ok and model_status == 200 and not mismatches
          and embed_statuses == [200] * 5 and rerank_status == 200)
    return {
        "schema_version": "phase0-bge-recovery/1",
        "ok": ok,
        "health_status": health_status,
        "health_ok": health_ok,
        "model_info_status": model_status,
        "model_info_mismatches": mismatches,
        "embed_statuses": embed_statuses,
        "rerank_status": rerank_status,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def enforce_license_gate(cfg: Config, config_path: str) -> Path:
    """Run the machine-enforced audit before a child can allocate CUDA."""
    from scripts.funasr_phase0 import lib_license_audit

    out = (Path(cfg.reports_root) / cfg.run_id /
           f"license-audit-{dt.datetime.now():%Y%m%d-%H%M%S}.md")
    rc = lib_license_audit.main(["--config", str(Path(config_path).resolve()),
                                "--out", str(out)])
    if rc != 0:
        raise RuntimeError(f"license gate rejected execution (exit {rc}); see {out}")
    return out


def load_matching_baseline(cfg: Config) -> dict[str, Any]:
    """Load the newest successful baseline produced for this exact config."""
    import hashlib

    target_id = hashlib.sha256(
        f"{cfg.bge_base_url}|{cfg.bge_expected_model}|{cfg.bge_expected_reranker}|"
        f"{cfg.bge_expected_device}|{cfg.bge_expected_torch_version}".encode("utf-8")
    ).hexdigest()[:16]
    root = Path(cfg.reports_root) / cfg.run_id
    for path in sorted(root.glob("bge-baseline-*.json"), reverse=True):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (row.get("schema_version") == "phase0-bge-baseline/1"
                and row.get("report_kind") == "bge_baseline"
                and row.get("ok") is True
                and row.get("config_sha256") == cfg.config_sha256
                and row.get("target_id") == target_id):
            return row
    raise RuntimeError(f"no matching successful BGE baseline under {root}")


class GuardedProcess:
    """Own one worker, its run registration, monitor, and recovery report."""

    def __init__(self, cfg: Config, config_path: str, command_name: str,
                 command: list[str], *, baseline: dict[str, Any],
                 monitor_probes: bool = True, stdout=None):
        self.cfg = cfg
        self.config_path = str(Path(config_path).resolve())
        self.command_name = command_name
        self.command = command
        self.baseline = baseline
        self.monitor_probes = monitor_probes
        self.stdout = stdout
        self.proc: subprocess.Popen | None = None
        self.monitor: Monitor | None = None
        self.guard_path: Path | None = None
        self.active_path = Path(cfg.logs_root) / "active-runs" / f"{cfg.run_id}.json"
        self.nonce = secrets.token_hex(24)
        self.recovery: dict[str, Any] | None = None
        self._stop_lock = threading.Lock()
        self._stopped = False

    def start(self) -> "GuardedProcess":
        if self.active_path.exists():
            raise RuntimeError(f"active run already exists: {self.active_path}")
        enforce_license_gate(self.cfg, self.config_path)
        active_root = self.active_path.parent
        active_root.mkdir(parents=True, exist_ok=True)
        self.guard_path = active_root / f".{self.cfg.run_id}-{self.nonce}.guard.json"
        guard = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "run_id": self.cfg.run_id,
            "config_sha256": self.cfg.config_sha256,
            "command_name": self.command_name,
            "nonce": self.nonce,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "controller_pid": os.getpid(),
            "config_path": self.config_path,
            "config_file_sha256": hashlib.sha256(
                Path(self.config_path).read_bytes()
            ).hexdigest().upper(),
        }
        atomic_json_dump(self.guard_path, guard)
        env = os.environ.copy()
        env[GUARD_ENV_FILE] = str(self.guard_path)
        env[GUARD_ENV_NONCE] = self.nonce
        env["MODELSCOPE_CACHE"] = str((Path(self.cfg.models_root) / "modelscope").resolve())
        env["HF_HOME"] = str((Path(self.cfg.models_root) / "huggingface").resolve())
        env.update(_OFFLINE_MODEL_ENV)
        popen_kwargs: dict[str, Any] = {"env": env, "stdout": self.stdout,
                                       "stderr": subprocess.STDOUT if self.stdout else None}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.command, **popen_kwargs)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        active = {
            **guard,
            "worker_pid": self.proc.pid,
            "worker_start_time_iso": started,
            "worker_script": self.command_name,
            "command": self.command,
        }
        atomic_json_dump(self.active_path, active)
        th = self.cfg.thresholds
        monitor_cfg = MonitorConfig(
            bge_health_url=self.cfg.bge_base_url + "/health",
            bge_model_info_url=self.cfg.bge_base_url + "/model-info",
            bge_auth_token=os.environ.get("GPU_SERVICE_TOKEN", ""),
            bge_expected_model=self.cfg.bge_expected_model,
            bge_expected_reranker=self.cfg.bge_expected_reranker,
            bge_expected_device=self.cfg.bge_expected_device,
            bge_expected_torch_version=self.cfg.bge_expected_torch_version,
            run_id=self.cfg.run_id,
            worker_pid=self.proc.pid,
            worker_start_time_iso=started,
            worker_script=self.command_name,
            bge_p95_degradation_pct=th.bge_p95_degradation_pct,
            embed_baseline_p95_ms=float(self.baseline["embed_p95_s"]) * 1000,
            rerank_baseline_p95_ms=float(self.baseline["rerank_p95_s"]) * 1000,
            bge_error_rate_pct=th.bge_error_rate_pct,
            bge_5xx_consecutive=th.bge_5xx_consecutive,
            asr_peak_vram_gb=th.asr_peak_vram_gb,
            asr_steady_vram_gb=th.asr_steady_vram_gb,
            combined_vram_max_gb=th.combined_vram_max_gb,
            disk_free_min_gb=th.disk_free_min_gb,
            stop_reasons_dir=Path(self.cfg.logs_root) / "run-stops" / self.cfg.run_id,
            data_drive=self.cfg.logs_root,
            enable_bge_probe_threads=self.monitor_probes,
            on_stop=self._on_monitor_stop,
        )
        self.monitor = Monitor(monitor_cfg)
        self.monitor.start()
        return self

    def _on_monitor_stop(self, _reason: str, _detail: dict[str, Any]) -> None:
        self.terminate()
        try:
            self.recovery = verify_bge(
                self.cfg, os.environ.get("GPU_SERVICE_TOKEN", "")
            )
        except Exception as e:  # noqa: BLE001 - recovery failure is recorded
            self.recovery = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def terminate(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            if self.proc is not None and self.proc.poll() is None:
                terminate_process_tree(self.proc.pid)
        except Exception:
            with self._stop_lock:
                self._stopped = False
            raise

    def poll(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def wait(self, timeout: float | None = None) -> int:
        if self.proc is None:
            raise RuntimeError("worker not started")
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.terminate()
            try:
                return self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                return -9

    def close(self, *, verify: bool = True) -> dict[str, Any]:
        termination_error: str | None = None
        if self.monitor is not None:
            self.monitor.stop()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.terminate()
                self.wait(timeout=20)
            except Exception as e:  # noqa: BLE001 - cleanup must continue
                termination_error = f"{type(e).__name__}: {e}"
        snapshot = self.monitor.snapshot() if self.monitor is not None else {}
        if verify and self.recovery is None:
            self.recovery = verify_bge(self.cfg, os.environ.get("GPU_SERVICE_TOKEN", ""))
        try:
            if self.active_path.exists():
                active = json.loads(self.active_path.read_text(encoding="utf-8"))
                if active.get("nonce") == self.nonce:
                    self.active_path.unlink()
            if self.guard_path and self.guard_path.exists():
                self.guard_path.unlink()
        except (OSError, json.JSONDecodeError):
            pass
        return {"monitor": snapshot, "recovery": self.recovery,
                "termination_error": termination_error}


def worker_command(script_name: str, args: list[str]) -> list[str]:
    script = Path(__file__).resolve().parent / script_name
    return [sys.executable, str(script), *args]


_OFFLINE_MODEL_ENV = {
    "MODELSCOPE_OFFLINE": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_MODEL_CONFIG_NAMES = ("configuration.json", "config.json")
_MODEL_WEIGHT_PATTERNS = ("*.pt", "*.bin", "*.safetensors")


def enable_offline_model_access() -> None:
    """Prevent guarded ASR workers from falling back to any model hub."""
    os.environ.update(_OFFLINE_MODEL_ENV)


def resolve_staged_model(cfg: Any, model_id: str) -> Path:
    """Resolve and validate a model staged below the approved models root."""
    models_root = Path(cfg.models_root).resolve()
    model_path = (models_root / Path(*model_id.split("/"))).resolve()
    try:
        model_path.relative_to(models_root)
    except ValueError as exc:
        raise RuntimeError(f"model {model_id!r} resolves outside models_root") from exc
    if not model_path.is_dir():
        raise RuntimeError(f"staged model directory not found: {model_path}")
    if not any((model_path / name).is_file() for name in _MODEL_CONFIG_NAMES):
        raise RuntimeError(f"staged model configuration not found: {model_path}")
    if not any(any(model_path.glob(pattern)) for pattern in _MODEL_WEIGHT_PATTERNS):
        raise RuntimeError(f"staged model weights not found: {model_path}")
    return model_path
