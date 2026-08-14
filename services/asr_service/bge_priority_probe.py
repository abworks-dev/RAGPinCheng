"""Fail-closed HTTP adapter for the authenticated BGE activity contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import httpx

from .scheduler import BgePriorityDecision

GPU_ACTIVITY_VERSION = "gpu-activity/1"


@dataclass(frozen=True, slots=True)
class HttpBgePriorityProbe:
    url: str
    token: str = field(repr=False)
    connect_timeout_seconds: float = 3.0
    request_timeout_seconds: float = 5.0
    get: Callable[..., httpx.Response] = field(
        default=httpx.get,
        repr=False,
        compare=False,
    )

    def allow_next_asr_chunk(self) -> BgePriorityDecision:
        try:
            response = self.get(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=httpx.Timeout(
                    self.request_timeout_seconds,
                    connect=self.connect_timeout_seconds,
                ),
            )
            if response.status_code != 200:
                return BgePriorityDecision.pause_probe_unavailable
            payload = response.json()
            if type(payload) is not dict or set(payload) != {
                "api_version",
                "model_loaded",
                "inflight_requests",
                "asr_chunk_allowed",
            }:
                return BgePriorityDecision.pause_probe_unavailable
            if payload["api_version"] != GPU_ACTIVITY_VERSION:
                return BgePriorityDecision.pause_probe_unavailable
            if (
                type(payload["model_loaded"]) is not bool
                or type(payload["asr_chunk_allowed"]) is not bool
                or type(payload["inflight_requests"]) is not int
                or isinstance(payload["inflight_requests"], bool)
                or payload["inflight_requests"] < 0
            ):
                return BgePriorityDecision.pause_probe_unavailable
            if (
                payload["model_loaded"]
                and payload["inflight_requests"] == 0
                and payload["asr_chunk_allowed"]
            ):
                return BgePriorityDecision.allow
            return BgePriorityDecision.pause_bge_busy
        except Exception:
            return BgePriorityDecision.pause_probe_unavailable
