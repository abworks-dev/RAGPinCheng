"""Environment-only configuration for the independent ASR service."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AsrServiceSettings:
    enabled: bool
    token: str
    host: str
    port: int
    spool_root: Path
    max_input_bytes: int
    max_upload_part_bytes: int
    max_queue_length: int
    chunk_duration_ms: int
    consecutive_failure_limit: int
    bge_priority_probe_url: str
    bge_priority_probe_token: str

    @classmethod
    def from_env(cls) -> "AsrServiceSettings":
        return cls(
            _enabled("ASR_SERVICE_ENABLED"),
            os.getenv("ASR_SERVICE_TOKEN", ""),
            os.getenv("ASR_SERVICE_HOST", "127.0.0.1"),
            int(os.getenv("ASR_SERVICE_PORT", "8200")),
            Path(os.getenv("ASR_SERVICE_SPOOL_ROOT", ".asr-spool")),
            int(os.getenv("ASR_MAX_INPUT_BYTES", str(2 * 1024**3))),
            int(os.getenv("ASR_UPLOAD_PART_BYTES", str(8 * 1024**2))),
            int(os.getenv("ASR_MAX_QUEUE_LENGTH", "8")),
            int(os.getenv("ASR_CHUNK_DURATION_MS", "300000")),
            int(os.getenv("ASR_CONSECUTIVE_FAILURE_LIMIT", "3")),
            os.getenv("BGE_PRIORITY_PROBE_URL", ""),
            os.getenv("BGE_PRIORITY_PROBE_TOKEN", ""),
        )

    def validate_for_startup(self) -> None:
        if self.enabled and not self.token:
            raise RuntimeError("ASR_SERVICE_TOKEN is required when service is enabled")
        if self.bge_priority_probe_url and not self.bge_priority_probe_token:
            raise RuntimeError(
                "BGE_PRIORITY_PROBE_TOKEN is required when probe URL is configured"
            )
        if (
            not self.host
            or self.port <= 0
            or self.port > 65535
            or self.max_input_bytes <= 0
            or self.max_upload_part_bytes <= 0
            or self.max_queue_length <= 0
            or self.chunk_duration_ms <= 0
            or self.consecutive_failure_limit <= 0
        ):
            raise RuntimeError("invalid ASR service numeric configuration")
