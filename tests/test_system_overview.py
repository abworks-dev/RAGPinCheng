from __future__ import annotations

from unittest.mock import Mock

from api import system_overview


def test_collect_system_overview_reports_separate_nodes(monkeypatch):
    monkeypatch.setattr(system_overview, "SYSTEM_NODE_ID", "app-node")
    monkeypatch.setattr(system_overview, "collect_app_metrics", lambda now: {
        "status": "healthy",
        "cpu_percent": 20.0,
        "memory_used_bytes": 20,
        "memory_total_bytes": 100,
        "disk_used_bytes": 30,
        "disk_total_bytes": 100,
        "checked_at": now,
        "error_code": None,
    })
    monkeypatch.setattr(system_overview, "fetch_gpu_metrics", lambda now: {
        "status": "healthy",
        "model_loaded": True,
        "device_name": "test-gpu",
        "vram_used_bytes": 40,
        "vram_total_bytes": 100,
        "utilization_percent": 50.0,
        "temperature_celsius": 55.0,
        "inflight_requests": 0,
        "checked_at": now,
        "data_age_seconds": 0,
        "stale": False,
        "error_code": None,
        "_node_id": "gpu-node",
    })
    monkeypatch.setattr(system_overview, "fetch_office_processing_health", lambda now: {
        "enabled": True,
        "mode": "deployment_config",
        "disabled_reason": None,
        "status": "healthy",
        "checked_at": now,
        "error_code": None,
    })

    payload = system_overview.collect_system_overview(now=100)

    assert payload["topology"] == "separate"
    assert payload["app"]["disk_used_bytes"] == 30
    assert payload["gpu"]["device_name"] == "test-gpu"
    assert "_node_id" not in payload["gpu"]
    assert payload["office_processing"] == {
        "enabled": True,
        "mode": "deployment_config",
        "disabled_reason": None,
        "status": "healthy",
        "checked_at": 100,
        "error_code": None,
    }


def test_collect_system_overview_reports_disabled_office_processing(monkeypatch):
    monkeypatch.setattr(system_overview, "OFFICE_PROCESSING_ENABLED", False)
    monkeypatch.setattr(system_overview, "collect_app_metrics", lambda now: {"status": "healthy", "checked_at": now})
    monkeypatch.setattr(system_overview, "fetch_gpu_metrics", lambda now: {"status": "unavailable", "checked_at": now, "_node_id": None})

    payload = system_overview.collect_system_overview(now=100)

    assert payload["office_processing"] == {
        "enabled": False,
        "mode": "deployment_config",
        "disabled_reason": "office_processing_disabled",
        "status": "disabled",
        "checked_at": 100,
        "error_code": None,
    }


def test_collect_system_overview_reports_shared_nodes(monkeypatch):
    monkeypatch.setattr(system_overview, "SYSTEM_NODE_ID", "same-node")
    monkeypatch.setattr(system_overview, "collect_app_metrics", lambda now: {"status": "healthy", "checked_at": now, "error_code": None})
    monkeypatch.setattr(system_overview, "fetch_gpu_metrics", lambda now: {"status": "degraded", "checked_at": now, "_node_id": "same-node"})
    monkeypatch.setattr(system_overview, "fetch_office_processing_health", lambda now: {
        "enabled": True, "mode": "deployment_config", "disabled_reason": None,
        "status": "healthy", "checked_at": now, "error_code": None,
    })

    payload = system_overview.collect_system_overview(now=101)

    assert payload["topology"] == "shared"


def test_fetch_office_processing_health_probes_service(monkeypatch):
    monkeypatch.setattr(system_overview, "OFFICE_PROCESSING_ENABLED", True)
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "version": "synthetic"}
    get = Mock(return_value=response)
    monkeypatch.setattr(system_overview.httpx, "get", get)

    payload = system_overview.fetch_office_processing_health(now=102)

    assert payload["status"] == "healthy"
    assert payload["checked_at"] == 102
    assert payload["error_code"] is None
    get.assert_called_once_with(
        f"{system_overview.LIBREOFFICE_URL.rstrip('/')}/health",
        timeout=system_overview.LIBREOFFICE_HEALTH_TIMEOUT,
    )


def test_fetch_office_processing_health_reports_unreachable(monkeypatch):
    monkeypatch.setattr(system_overview, "OFFICE_PROCESSING_ENABLED", True)
    monkeypatch.setattr(
        system_overview.httpx,
        "get",
        Mock(side_effect=system_overview.httpx.TimeoutException("timeout")),
    )

    payload = system_overview.fetch_office_processing_health(now=103)

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "office_service_unreachable"


def test_fetch_gpu_metrics_keeps_recent_snapshot_when_probe_fails(monkeypatch):
    system_overview._LAST_GPU_SNAPSHOT = None
    system_overview._LAST_GPU_AT = 0
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "api_version": "gpu-system-metrics/1",
        "node_id": "gpu-node",
        "model_loaded": True,
        "gpu_available": True,
        "device_name": "test-gpu",
        "vram_used_bytes": 40,
        "vram_total_bytes": 100,
        "utilization_percent": 50,
        "temperature_celsius": 55,
        "inflight_requests": 0,
        "checked_at": 200,
    }
    monkeypatch.setattr(system_overview.httpx, "get", Mock(return_value=response))
    first = system_overview.fetch_gpu_metrics(now=200)
    monkeypatch.setattr(system_overview.httpx, "get", Mock(side_effect=system_overview.httpx.TimeoutException("timeout")))

    stale = system_overview.fetch_gpu_metrics(now=210)

    assert first["stale"] is False
    assert stale["stale"] is True
    assert stale["data_age_seconds"] == 10
    assert stale["device_name"] == "test-gpu"
