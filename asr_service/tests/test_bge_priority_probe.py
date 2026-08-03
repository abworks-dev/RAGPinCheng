from __future__ import annotations

import json

import httpx
import pytest

from asr_service.bge_priority_probe import HttpBgePriorityProbe
from asr_service.scheduler import BgePriorityDecision


def response(payload, status=200):
    return httpx.Response(status, content=json.dumps(payload).encode("utf-8"))


def valid_payload(**overrides):
    payload = {
        "api_version": "gpu-activity/1",
        "model_loaded": True,
        "inflight_requests": 0,
        "asr_chunk_allowed": True,
    }
    payload.update(overrides)
    return payload


def test_probe_allows_only_explicit_idle_loaded_response():
    captured = {}

    def get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return response(valid_payload())

    probe = HttpBgePriorityProbe(
        "http://127.0.0.1:8100/v1/activity",
        "secret-token",
        get=get,
    )
    assert probe.allow_next_asr_chunk() is BgePriorityDecision.allow
    assert captured["url"].endswith("/v1/activity")
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}
    assert "secret-token" not in repr(probe)


@pytest.mark.parametrize(
    "payload",
    [
        valid_payload(model_loaded=False, asr_chunk_allowed=False),
        valid_payload(inflight_requests=1, asr_chunk_allowed=False),
        valid_payload(asr_chunk_allowed=False),
    ],
)
def test_probe_reports_valid_non_idle_states_as_busy(payload):
    probe = HttpBgePriorityProbe("http://probe/v1/activity", "token", get=lambda *_a, **_k: response(payload))
    assert probe.allow_next_asr_chunk() is BgePriorityDecision.pause_bge_busy


@pytest.mark.parametrize(
    "payload",
    [
        {**valid_payload(), "extra": None},
        valid_payload(api_version="gpu-activity/2"),
        valid_payload(inflight_requests=True),
        valid_payload(inflight_requests=-1),
        valid_payload(model_loaded=1),
        valid_payload(asr_chunk_allowed=1),
    ],
)
def test_probe_rejects_invalid_contract_payloads(payload):
    probe = HttpBgePriorityProbe("http://probe/v1/activity", "token", get=lambda *_a, **_k: response(payload))
    assert probe.allow_next_asr_chunk() is BgePriorityDecision.pause_probe_unavailable


def test_probe_fails_closed_for_http_json_and_transport_errors():
    cases = [
        lambda *_a, **_k: response(valid_payload(), status=503),
        lambda *_a, **_k: httpx.Response(200, content=b"not-json"),
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.TimeoutException("timeout")),
    ]
    for get in cases:
        probe = HttpBgePriorityProbe("http://probe/v1/activity", "token", get=get)
        assert probe.allow_next_asr_chunk() is BgePriorityDecision.pause_probe_unavailable
