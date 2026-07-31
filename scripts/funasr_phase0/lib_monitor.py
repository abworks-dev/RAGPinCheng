"""Phase 0 ASR sandbox — monitor and auto-stop (R2 fix).

Per R2 spec §七:
  - Stop callback is invoked OUTSIDE any lock and at most once.
  - Any monitor thread that crashes fails closed (triggers stop).
  - Health response is parsed as JSON; embedded "ok"/"model_loaded" fields.
  - Health failures and 5xx are true consecutive counters; reset on success.
  - Embed and Rerank latencies tracked separately; P95/P99 per kind.
  - Steady-state VRAM uses rolling statistics (NOT last sample).
  - Tracks both WHOLE-CARD VRAM and the specific ASR worker PID VRAM.
  - Baseline P95 / degradation threshold are loaded from approved config.
  - Error rate uses a rolling window of the last N samples.
  - Stop reason is atomically written into a run-specific directory.
  - Stop callback can terminate the ASR process group and then trigger
    a BGE verification.

Threading model:
  - One background thread per source: gpu / bge_health / bge_embed_ping /
    bge_rerank_ping / disk.
  - Each thread: try/except around its loop body; any unhandled exception
    triggers a fail-closed stop.
"""
from __future__ import annotations

import dataclasses
import json
import os
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

MONITOR_SCHEMA_VERSION = "phase0-monitor/1"


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MonitorConfig:
    bge_health_url: str
    bge_model_info_url: str
    bge_auth_token: str  # may be empty string; NEVER logged
    bge_expected_model: str
    bge_expected_reranker: str
    bge_expected_device: str
    bge_expected_torch_version: str
    run_id: str
    worker_pid: int
    worker_start_time_iso: str
    worker_script: str
    # Probe intervals
    gpu_poll_interval_s: float = 5.0
    bge_health_interval_s: float = 5.0
    bge_embed_ping_interval_s: float = 30.0
    bge_rerank_ping_interval_s: float = 60.0
    disk_poll_interval_s: float = 30.0
    # Thresholds from approved config
    bge_p95_degradation_pct: float = 100.0
    embed_baseline_p95_ms: float | None = None
    rerank_baseline_p95_ms: float | None = None
    bge_error_rate_pct: float = 0.5
    bge_5xx_consecutive: int = 3
    asr_peak_vram_gb: float = 8.0
    asr_steady_vram_gb: float = 6.0
    combined_vram_max_gb: float = 14.0
    disk_free_min_gb: float = 5.0
    # Error-rate rolling window
    error_rate_window: int = 30
    rolling_window_s: float = 60.0
    # VRAM rolling window (steady-state)
    vram_rolling_window: int = 12
    # Paths
    stop_reasons_dir: Path = Path(".")  # run-specific dir
    data_drive: str = "E:\\"
    enable_bge_probe_threads: bool = True
    # Verifier callback (e.g. 07_verify_bge) — receives reason + detail
    on_stop: Callable[[str, dict[str, Any]], None] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# nvidia-smi helpers
# ─────────────────────────────────────────────────────────────────────────────


def nvidia_smi_csv() -> dict[str, float] | None:
    """Whole-card VRAM + utilization. None on driver / process error."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu,utilization.memory,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    line = out.stdout.strip().splitlines()[0]
    cols = [c.strip() for c in line.split(",")]
    keys = ["index", "memory_used_mib", "memory_total_mib",
            "util_gpu_pct", "util_mem_pct", "temp_c"]
    if len(cols) < len(keys):
        return None
    try:
        return {k: float(v) for k, v in zip(keys, cols[:len(keys)])}
    except ValueError:
        return None


def asr_pid_vram_mib(pid: int) -> float | None:
    """Per-process VRAM via nvidia-smi --query-compute-apps."""
    if pid <= 0:
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    for line in out.stdout.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 2:
            continue
        try:
            p = int(cols[0])
        except ValueError:
            continue
        if p == pid:
            try:
                return float(cols[1])
            except ValueError:
                return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# BGE health (JSON parsing)
# ─────────────────────────────────────────────────────────────────────────────


def bge_get_json(url: str, token: str, timeout: float = 3.0) -> tuple[int, dict | str, float]:
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
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body), time.monotonic() - t0
            except json.JSONDecodeError:
                return e.code, body, time.monotonic() - t0
        except Exception:  # noqa: BLE001
            return e.code, "", time.monotonic() - t0
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e), time.monotonic() - t0


def bge_ping(url: str, body: dict, token: str, timeout: float = 5.0) -> tuple[int, float]:
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


# ─────────────────────────────────────────────────────────────────────────────
# Disk free
# ─────────────────────────────────────────────────────────────────────────────


def disk_free_gb(path: str) -> float | None:
    try:
        import shutil
        total, used, free = shutil.disk_usage(path)
        return free / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Monitor
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _State:
    bge_health_failures: int = 0
    bge_5xx_streak: int = 0
    bge_5xx_total: int = 0
    embed_5xx_streak: int = 0
    rerank_5xx_streak: int = 0
    embed_latencies_ms: deque = field(default_factory=lambda: deque(maxlen=200))
    rerank_latencies_ms: deque = field(default_factory=lambda: deque(maxlen=200))
    embed_outcomes: deque = field(default_factory=lambda: deque(maxlen=30))
    rerank_outcomes: deque = field(default_factory=lambda: deque(maxlen=30))
    vram_used_mib_window: deque = field(default_factory=lambda: deque(maxlen=12))
    vram_asr_mib_window: deque = field(default_factory=lambda: deque(maxlen=12))
    asr_pid_vram_mib: float = 0.0
    whole_card_vram_mib: float = 0.0
    whole_card_vram_peak_mib: float = 0.0
    last_disk_free_gb: float | None = None
    bge_model_info: dict | None = None
    stop_reason: str | None = None
    stop_at: float | None = None
    thread_errors: list[dict[str, str]] = field(default_factory=list)


class Monitor:
    def __init__(self, cfg: MonitorConfig):
        self.cfg = cfg
        self.state = _State()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopped_once = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stop_reasons_dir = Path(cfg.stop_reasons_dir)
        self._stop_reasons_dir.mkdir(parents=True, exist_ok=True)
        # One capture for whole process
        self._process_start_time_iso = datetime.now().isoformat(timespec="seconds")
        # one log file for monitor internal errors
        self._log_path = self._stop_reasons_dir / "monitor-internal-errors.log"

    # ── Public ──
    def start(self) -> None:
        self._stop_event.clear()
        self._threads = [
            threading.Thread(target=self._safe_loop(self._gpu_loop, "gpu"), daemon=True),
            threading.Thread(target=self._safe_loop(self._bge_health_loop, "bge_health"), daemon=True),
            threading.Thread(target=self._safe_loop(self._disk_loop, "disk"), daemon=True),
        ]
        if self.cfg.enable_bge_probe_threads:
            self._threads.extend([
                threading.Thread(target=self._safe_loop(self._bge_embed_ping_loop, "bge_embed_ping"), daemon=True),
                threading.Thread(target=self._safe_loop(self._bge_rerank_ping_loop, "bge_rerank_ping"), daemon=True),
            ])
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=2.0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            el = [x[1] for x in self.state.embed_latencies_ms]
            rl = [x[1] for x in self.state.rerank_latencies_ms]
            eo = [x[1] for x in self.state.embed_outcomes]
            ro = [x[1] for x in self.state.rerank_outcomes]
            return {
                "schema_version": MONITOR_SCHEMA_VERSION,
                "asr_pid": self.cfg.worker_pid,
                "asr_pid_vram_mib": self.state.asr_pid_vram_mib,
                "whole_card_vram_mib": self.state.whole_card_vram_mib,
                "whole_card_vram_peak_mib": self.state.whole_card_vram_peak_mib,
                "vram_steady_asr_mib": _percentile(list(self.state.vram_asr_mib_window), 50),
                "vram_steady_whole_mib": _percentile(list(self.state.vram_used_mib_window), 50),
                "bge_health_failures": self.state.bge_health_failures,
                "bge_5xx_streak": self.state.bge_5xx_streak,
                "bge_5xx_total": self.state.bge_5xx_total,
                "embed_5xx_streak": self.state.embed_5xx_streak,
                "rerank_5xx_streak": self.state.rerank_5xx_streak,
                "embed_p50_ms": _percentile(el, 50),
                "embed_p95_ms": _percentile(el, 95),
                "embed_p99_ms": _percentile(el, 99),
                "embed_error_rate_pct": _err_rate_pct(eo),
                "embed_n": len(el),
                "rerank_p50_ms": _percentile(rl, 50),
                "rerank_p95_ms": _percentile(rl, 95),
                "rerank_p99_ms": _percentile(rl, 99),
                "rerank_error_rate_pct": _err_rate_pct(ro),
                "rerank_n": len(rl),
                "disk_free_gb": self.state.last_disk_free_gb,
                "bge_model_info": self.state.bge_model_info,
                "stop_reason": self.state.stop_reason,
                "stop_at": self.state.stop_at,
                "thread_errors": list(self.state.thread_errors),
            }

    def record_bge_result(self, kind: str, status: int, latency_s: float) -> None:
        """Record real coexist traffic and apply the same stop thresholds.

        When ``enable_bge_probe_threads`` is false, the coexistence driver
        feeds its real embed/rerank requests here so monitoring does not add
        traffic beyond the approved RPM.
        """
        if kind not in {"embed", "rerank"}:
            raise ValueError(f"unknown BGE result kind: {kind}")
        now = time.monotonic()
        with self._lock:
            latencies = (self.state.embed_latencies_ms if kind == "embed"
                         else self.state.rerank_latencies_ms)
            outcomes = (self.state.embed_outcomes if kind == "embed"
                        else self.state.rerank_outcomes)
            latencies.append((now, latency_s * 1000.0))
            outcomes.append((now, 1 if status == 200 else 0))
            cutoff = now - self.cfg.rolling_window_s
            while latencies and latencies[0][0] < cutoff:
                latencies.popleft()
            while outcomes and outcomes[0][0] < cutoff:
                outcomes.popleft()
            if kind == "embed":
                self.state.embed_5xx_streak = self.state.embed_5xx_streak + 1 if status >= 500 else 0
                streak = self.state.embed_5xx_streak
            else:
                self.state.rerank_5xx_streak = self.state.rerank_5xx_streak + 1 if status >= 500 else 0
                streak = self.state.rerank_5xx_streak
            err = _err_rate_pct([x[1] for x in outcomes])
            p95 = _percentile([x[1] for x in latencies], 95)
            count = len(outcomes)
        if streak >= self.cfg.bge_5xx_consecutive:
            self._trigger_stop(f"bge_{kind}_5xx_streak", {"streak": streak, "status": status})
        if count >= 3 and err > self.cfg.bge_error_rate_pct:
            self._trigger_stop(f"bge_{kind}_error_rate_exceeded", {"err_rate_pct": err})
        baseline = (self.cfg.embed_baseline_p95_ms if kind == "embed"
                    else self.cfg.rerank_baseline_p95_ms)
        if count >= 3 and baseline is not None and baseline > 0:
            degradation = (p95 - baseline) / baseline * 100.0
            if degradation > self.cfg.bge_p95_degradation_pct:
                self._trigger_stop(
                    f"bge_{kind}_p95_degraded",
                    {"p95_ms": p95, "baseline_p95_ms": baseline,
                     "degradation_pct": degradation},
                )

    # ── Loop wrappers (fail-closed) ──
    def _safe_loop(self, body: Callable[[], None], name: str) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                while not self._stop_event.is_set():
                    try:
                        body()
                    except Exception as e:  # noqa: BLE001
                        # log + fail-closed
                        msg = f"{datetime.now().isoformat(timespec='seconds')} {name}: {type(e).__name__}: {e}\n"
                        try:
                            self._log_path.parent.mkdir(parents=True, exist_ok=True)
                            with self._log_path.open("a", encoding="utf-8") as f:
                                f.write(msg)
                        except Exception:  # noqa: BLE001
                            pass
                        with self._lock:
                            self.state.thread_errors.append({"thread": name, "error": f"{type(e).__name__}: {e}"})
                        self._trigger_stop("monitor_thread_crashed", {"thread": name, "error": str(e)})
                        return
                    # No extra wait: each loop body already includes its own
                    # interval wait (e.g. self._stop_event.wait(interval)).
                    # The wait of 0 was a previous design; tests were too slow.
            except Exception as e:  # outer safeguard
                self._trigger_stop("monitor_outer_crashed", {"thread": name, "error": str(e)})
        wrapped.__name__ = f"loop_{name}"
        return wrapped

    # ── Internal loops ──
    def _gpu_loop(self) -> None:
        row = nvidia_smi_csv()
        if row is None:
            raise RuntimeError("nvidia-smi unavailable")
        asr_pid = self.cfg.worker_pid
        pid_row_mib = asr_pid_vram_mib(asr_pid) if asr_pid > 0 else None
        with self._lock:
            self.state.whole_card_vram_mib = row["memory_used_mib"]
            self.state.whole_card_vram_peak_mib = max(
                self.state.whole_card_vram_peak_mib, row["memory_used_mib"]
            )
            self.state.vram_used_mib_window.append(row["memory_used_mib"])
            if pid_row_mib is not None:
                self.state.asr_pid_vram_mib = pid_row_mib
                self.state.vram_asr_mib_window.append(pid_row_mib)
        # Threshold checks
        whole_gb = row["memory_used_mib"] / 1024.0
        if whole_gb > self.cfg.combined_vram_max_gb:
            self._trigger_stop("combined_vram_unsafe", {"whole_card_vram_gb": whole_gb})
        if pid_row_mib is not None:
            asr_gb = pid_row_mib / 1024.0
            if asr_gb > self.cfg.asr_peak_vram_gb:
                self._trigger_stop("asr_peak_vram_exceeded", {"asr_pid_vram_gb": asr_gb})
            with self._lock:
                steady_samples = list(self.state.vram_asr_mib_window)
            if len(steady_samples) >= 3:
                steady_gb = _percentile(steady_samples, 50) / 1024.0
                if steady_gb > self.cfg.asr_steady_vram_gb:
                    self._trigger_stop("asr_steady_vram_exceeded", {"asr_steady_vram_gb": steady_gb})
        self._stop_event.wait(self.cfg.gpu_poll_interval_s)

    def _bge_health_loop(self) -> None:
        status, body, _ = bge_get_json(self.cfg.bge_health_url, self.cfg.bge_auth_token)
        is_5xx = status >= 500
        is_2xx_ok = (
            status == 200
            and isinstance(body, dict)
            and body.get("status") == "ok"
            and body.get("model_loaded") is True
        )
        with self._lock:
            if is_2xx_ok:
                self.state.bge_health_failures = 0
                self.state.bge_5xx_streak = 0
            else:
                self.state.bge_health_failures += 1
                if is_5xx:
                    self.state.bge_5xx_streak += 1
                    self.state.bge_5xx_total += 1
                else:
                    self.state.bge_5xx_streak = 0
        if self.state.bge_5xx_streak >= self.cfg.bge_5xx_consecutive and is_5xx:
            self._trigger_stop("bge_5xx_streak",
                               {"streak": self.state.bge_5xx_streak, "status": status})
        explicit_degraded = (
            status == 200 and isinstance(body, dict)
            and (body.get("status") != "ok" or body.get("model_loaded") is not True)
        )
        if explicit_degraded:
            self._trigger_stop("bge_health_degraded",
                               {"failures": self.state.bge_health_failures,
                                "status": status, "body": str(body)[:200]})
        elif self.state.bge_health_failures >= 3 and not is_2xx_ok:
            self._trigger_stop("bge_health_degraded",
                               {"failures": self.state.bge_health_failures, "status": status,
                                "body": str(body)[:200]})
        # also fetch /model-info occasionally (every 60s)
        if not self.state.bge_model_info or (self.state.bge_5xx_total == 0 and self._loop_iter % 12 == 0):
            mi_status, mi, _ = bge_get_json(self.cfg.bge_model_info_url, self.cfg.bge_auth_token)
            expected = {
                "embedding_model": self.cfg.bge_expected_model,
                "reranker_model": self.cfg.bge_expected_reranker,
                "device": self.cfg.bge_expected_device,
                "torch_version": self.cfg.bge_expected_torch_version,
            }
            mismatches = [] if isinstance(mi, dict) else ["response_not_json_object"]
            if isinstance(mi, dict):
                mismatches.extend(
                    f"{k}: {mi.get(k)!r} != {v!r}"
                    for k, v in expected.items() if mi.get(k) != v
                )
                with self._lock:
                    self.state.bge_model_info = mi
            if mi_status != 200 or mismatches:
                self._trigger_stop("bge_model_info_mismatch",
                                   {"status": mi_status, "mismatches": mismatches})
        # Iter counter is a simple module-level mutable
        self._loop_iter = getattr(self, "_loop_iter", 0) + 1
        self._stop_event.wait(self.cfg.bge_health_interval_s)

    def _bge_embed_ping_loop(self) -> None:
        base = self.cfg.bge_health_url.rsplit("/", 1)[0]
        url = base + "/v1/embeddings"
        body = {"texts": ["phase0 monitor probe"], "normalize": True}
        status, lat = bge_ping(url, body, self.cfg.bge_auth_token)
        self.record_bge_result("embed", status, lat)
        self._stop_event.wait(self.cfg.bge_embed_ping_interval_s)

    def _bge_rerank_ping_loop(self) -> None:
        base = self.cfg.bge_health_url.rsplit("/", 1)[0]
        url = base + "/v1/rerank"
        passages = [f"phase0 monitor passage {i}" for i in range(5)]
        body = {"query": "phase0 monitor", "passages": passages, "use_header": True}
        status, lat = bge_ping(url, body, self.cfg.bge_auth_token)
        self.record_bge_result("rerank", status, lat)
        self._stop_event.wait(self.cfg.bge_rerank_ping_interval_s)

    def _disk_loop(self) -> None:
        free = disk_free_gb(self.cfg.data_drive)
        with self._lock:
            self.state.last_disk_free_gb = free
        if free is None:
            self._trigger_stop("disk_probe_unavailable", {"path": self.cfg.data_drive})
        elif free < self.cfg.disk_free_min_gb:
            self._trigger_stop("disk_low", {"free_gb": free})
        self._stop_event.wait(self.cfg.disk_poll_interval_s)

    # ── Stop trigger (OUTSIDE any lock) ──
    def _trigger_stop(self, reason: str, detail: dict[str, Any]) -> None:
        with self._lock:
            if self._stopped_once.is_set():
                return
            self.state.stop_reason = reason
            self.state.stop_at = time.time()
            self._stopped_once.set()
        # All I/O outside the lock.
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            payload = {
                "schema_version": MONITOR_SCHEMA_VERSION,
                "run_id": self.cfg.run_id,
                "worker_pid": self.cfg.worker_pid,
                "worker_start_time_iso": self.cfg.worker_start_time_iso,
                "worker_script": self.cfg.worker_script,
                "reason": reason,
                "detail": detail,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            # atomic write: temp + rename
            tmp = self._stop_reasons_dir / f".stop-{self.cfg.run_id}-{stamp}.tmp"
            final = self._stop_reasons_dir / f"stop-{self.cfg.run_id}-{stamp}.json"
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, final)
        except Exception as e:  # noqa: BLE001
            try:
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().isoformat()} stop-write-failed: {e}\n")
            except Exception:  # noqa: BLE001
                pass
        if self.cfg.on_stop is not None:
            try:
                self.cfg.on_stop(reason, detail)
            except Exception as e:  # noqa: BLE001
                try:
                    with self._log_path.open("a", encoding="utf-8") as f:
                        f.write(f"{datetime.now().isoformat()} on_stop-failed: {e}\n")
                except Exception:  # noqa: BLE001
                    pass


def _percentile(values, q: float) -> float:
    if not values:
        return float("nan")
    arr = sorted(values)
    if len(arr) == 1:
        return float(arr[0])
    k = (len(arr) - 1) * (q / 100.0)
    f = int(k)
    c = min(f + 1, len(arr) - 1)
    if f == c:
        return float(arr[f])
    return float(arr[f] + (arr[c] - arr[f]) * (k - f))


def _err_rate_pct(outcomes) -> float:
    if not outcomes:
        return 0.0
    return (sum(1 for x in outcomes if x == 0) / len(outcomes)) * 100.0
