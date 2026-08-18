"""Read-only production resource snapshots for the admin overview."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from src.config import (
    DATA_DIR,
    GPU_SERVICE_TOKEN,
    GPU_SERVICE_URL,
    GPU_SYSTEM_METRICS_TIMEOUT_SECONDS,
    LIBREOFFICE_HEALTH_TIMEOUT,
    LIBREOFFICE_URL,
    OFFICE_PROCESSING_ENABLED,
    OFFICE_MIN_FREE_DISK_MB,
    SYSTEM_NODE_ID,
)


_GPU_CACHE_LOCK = threading.Lock()
_LAST_GPU_SNAPSHOT: dict[str, Any] | None = None
_LAST_GPU_AT = 0
_GPU_STALE_AFTER_SECONDS = 60


def _read_cpu_times() -> tuple[int, int] | None:
    try:
        line = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
        values = [int(value) for value in line.split()[1:]]
        if len(values) < 4:
            return None
        return sum(values), values[3]
    except (OSError, ValueError, IndexError):
        return None


def _cpu_percent() -> float | None:
    first = _read_cpu_times()
    if first is None:
        return None
    time.sleep(0.05)
    second = _read_cpu_times()
    if second is None:
        return None
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _read_key_value_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            key, separator, value = line.partition(":")
            if separator:
                result[key.strip()] = value.strip().split()[0]
    except (OSError, UnicodeError):
        return {}
    return result


def _memory_bytes() -> tuple[int, int] | None:
    current_paths = (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"))
    limit_paths = (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"))
    try:
        current_path = next(path for path in current_paths if path.is_file())
        limit_path = next(path for path in limit_paths if path.is_file())
        current = int(current_path.read_text(encoding="ascii").strip())
        raw_limit = limit_path.read_text(encoding="ascii").strip()
        if raw_limit != "max":
            limit = int(raw_limit)
            if 0 < limit < 1 << 60:
                return current, limit
    except (OSError, ValueError, StopIteration):
        pass
    values = _read_key_value_file(Path("/proc/meminfo"))
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    try:
        total = int(values["MemTotal"]) * 1024
        available = int(values["MemAvailable"]) * 1024
    except ValueError:
        return None
    return max(0, total - available), total


def collect_app_metrics(now: int | None = None) -> dict[str, Any]:
    checked_at = int(time.time() if now is None else now)
    values: dict[str, Any] = {
        "status": "healthy",
        "cpu_percent": None,
        "memory_used_bytes": None,
        "memory_total_bytes": None,
        "disk_used_bytes": None,
        "disk_total_bytes": None,
        "checked_at": checked_at,
        "error_code": None,
    }
    errors: list[str] = []
    try:
        values["cpu_percent"] = _cpu_percent()
        if values["cpu_percent"] is None:
            errors.append("cpu_metrics_unavailable")
    except (OSError, ValueError):
        errors.append("cpu_metrics_unavailable")
    memory = _memory_bytes()
    if memory is None:
        errors.append("memory_metrics_unavailable")
    else:
        values["memory_used_bytes"], values["memory_total_bytes"] = memory
    try:
        disk = shutil.disk_usage(DATA_DIR)
        values["disk_used_bytes"], values["disk_total_bytes"] = disk.used, disk.total
    except OSError:
        errors.append("disk_metrics_unavailable")
    if errors:
        values["status"] = "degraded" if len(errors) < 3 else "unavailable"
        values["error_code"] = ",".join(errors)
    return values


def _topology(gpu_node_id: str | None) -> str:
    if not SYSTEM_NODE_ID or not gpu_node_id:
        return "unknown"
    return "shared" if SYSTEM_NODE_ID == gpu_node_id else "separate"


def _unavailable_gpu(now: int) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "model_loaded": None,
        "device_name": None,
        "vram_used_bytes": None,
        "vram_total_bytes": None,
        "utilization_percent": None,
        "temperature_celsius": None,
        "inflight_requests": None,
        "checked_at": now,
        "data_age_seconds": None,
        "stale": False,
        "error_code": "gpu_metrics_unreachable",
        "_node_id": None,
    }


def fetch_gpu_metrics(now: int | None = None) -> dict[str, Any]:
    global _LAST_GPU_AT, _LAST_GPU_SNAPSHOT
    checked_at = int(time.time() if now is None else now)
    headers = {"Authorization": f"Bearer {GPU_SERVICE_TOKEN}"} if GPU_SERVICE_TOKEN else {}
    try:
        response = httpx.get(
            f"{GPU_SERVICE_URL.rstrip('/')}/v1/system-metrics",
            headers=headers,
            timeout=GPU_SYSTEM_METRICS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("api_version") != "gpu-system-metrics/1":
            raise ValueError("unexpected_api_version")
        snapshot = {
            "status": "healthy" if payload.get("model_loaded") and payload.get("gpu_available") else "degraded",
            "model_loaded": bool(payload.get("model_loaded")),
            "device_name": payload.get("device_name"),
            "vram_used_bytes": payload.get("vram_used_bytes"),
            "vram_total_bytes": payload.get("vram_total_bytes"),
            "utilization_percent": payload.get("utilization_percent"),
            "temperature_celsius": payload.get("temperature_celsius"),
            "inflight_requests": payload.get("inflight_requests"),
            "checked_at": int(payload.get("checked_at") or checked_at),
            "data_age_seconds": 0,
            "stale": False,
            "error_code": None,
            "_node_id": payload.get("node_id"),
        }
        with _GPU_CACHE_LOCK:
            _LAST_GPU_SNAPSHOT = snapshot.copy()
            _LAST_GPU_AT = checked_at
        return snapshot
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        with _GPU_CACHE_LOCK:
            cached = _LAST_GPU_SNAPSHOT.copy() if _LAST_GPU_SNAPSHOT else None
            cached_at = _LAST_GPU_AT
        if cached is not None and checked_at - cached_at <= _GPU_STALE_AFTER_SECONDS:
            cached["status"] = "degraded"
            cached["stale"] = True
            cached["data_age_seconds"] = max(0, checked_at - int(cached["checked_at"]))
            cached["error_code"] = "gpu_metrics_stale"
            return cached
        return _unavailable_gpu(checked_at)


def fetch_office_processing_health(now: int | None = None) -> dict[str, Any]:
    checked_at = int(time.time() if now is None else now)
    disk = shutil.disk_usage(DATA_DIR)
    free_mb = disk.free // (1024 * 1024)
    disk_low = free_mb < OFFICE_MIN_FREE_DISK_MB
    if not OFFICE_PROCESSING_ENABLED:
        return {
            "enabled": False,
            "mode": "deployment_config",
            "disabled_reason": "office_processing_disabled",
            "status": "disabled",
            "checked_at": checked_at,
            "error_code": None,
            "disk_free_mb": free_mb,
            "disk_minimum_mb": OFFICE_MIN_FREE_DISK_MB,
        }
    try:
        response = httpx.get(
            f"{LIBREOFFICE_URL.rstrip('/')}/health",
            timeout=LIBREOFFICE_HEALTH_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("unexpected_office_health_response")
        return {
            "enabled": True,
            "mode": "deployment_config",
            "disabled_reason": None,
            "status": "degraded" if disk_low else "healthy",
            "checked_at": checked_at,
            "error_code": "office_disk_space_low" if disk_low else None,
            "disk_free_mb": free_mb,
            "disk_minimum_mb": OFFICE_MIN_FREE_DISK_MB,
        }
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "enabled": True,
            "mode": "deployment_config",
            "disabled_reason": None,
            "status": "unavailable",
            "checked_at": checked_at,
            "error_code": "office_service_unreachable",
            "disk_free_mb": free_mb,
            "disk_minimum_mb": OFFICE_MIN_FREE_DISK_MB,
        }


def collect_system_overview(now: int | None = None) -> dict[str, Any]:
    checked_at = int(time.time() if now is None else now)
    app = collect_app_metrics(checked_at)
    gpu = fetch_gpu_metrics(checked_at)
    topology = _topology(gpu.pop("_node_id", None))
    return {
        "topology": topology,
        "checked_at": checked_at,
        "app": app,
        "gpu": gpu,
        "office_processing": fetch_office_processing_health(checked_at),
    }
