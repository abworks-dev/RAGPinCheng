"""Environment-only configuration for the independent ASR service."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


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
    model_cache_root: Path | None = None
    model_manifest_path: Path | None = None
    model_local_files_only: bool = True
    bge_probe_connect_timeout_seconds: float = 3.0
    bge_probe_request_timeout_seconds: float = 5.0
    log_dir: Path | None = None
    faster_whisper_model_cache_root: Path | None = None
    faster_whisper_model_manifest_path: Path | None = None

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
            _optional_path("ASR_MODEL_CACHE_ROOT"),
            _optional_path("ASR_MODEL_MANIFEST_PATH"),
            _enabled("ASR_MODEL_LOCAL_FILES_ONLY", True),
            float(os.getenv("BGE_PRIORITY_PROBE_CONNECT_TIMEOUT_SECONDS", "3")),
            float(os.getenv("BGE_PRIORITY_PROBE_REQUEST_TIMEOUT_SECONDS", "5")),
            _optional_path("ASR_LOG_DIR"),
            _optional_path("ASR_FASTER_WHISPER_MODEL_CACHE_ROOT"),
            _optional_path("ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH"),
        )

    def validate_for_startup(self) -> None:
        if self.enabled and not self.token:
            raise RuntimeError("ASR_SERVICE_TOKEN is required when service is enabled")
        if self.enabled and (not self.bge_priority_probe_url or not self.bge_priority_probe_token):
            raise RuntimeError("authenticated BGE priority probe is required when service is enabled")
        if self.bge_priority_probe_url:
            if not self.bge_priority_probe_token:
                raise RuntimeError(
                    "BGE_PRIORITY_PROBE_TOKEN is required when probe URL is configured"
                )
            parsed = urlparse(self.bge_priority_probe_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.path != "/v1/activity"
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise RuntimeError("invalid BGE_PRIORITY_PROBE_URL")
        if self.enabled and (self.model_cache_root is None or self.model_manifest_path is None):
            raise RuntimeError("local model cache and manifest are required when service is enabled")
        if not self.model_local_files_only:
            raise RuntimeError("ASR_MODEL_LOCAL_FILES_ONLY must remain true")
        if (
            self.faster_whisper_model_cache_root is None
        ) != (
            self.faster_whisper_model_manifest_path is None
        ):
            raise RuntimeError(
                "faster-whisper model cache and manifest must be configured together"
            )
        if (
            not self.host or self.port <= 0 or self.port > 65535
            or self.max_input_bytes <= 0 or self.max_upload_part_bytes <= 0
            or self.max_queue_length <= 0 or self.chunk_duration_ms <= 0
            or self.consecutive_failure_limit <= 0
            or self.bge_probe_connect_timeout_seconds <= 0
            or self.bge_probe_request_timeout_seconds <= 0
        ):
            raise RuntimeError("invalid ASR service numeric configuration")
