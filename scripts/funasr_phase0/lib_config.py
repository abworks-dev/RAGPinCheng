"""Phase 0 ASR sandbox — config loader and gate.

All Phase 0 entry scripts MUST read config via lib_config.load_config(path).
Refuses to start any GPU entry if:
  - no valid run_id
  - approved_window is in the past or malformed
  - shared_production_gpu_confirmed is not explicitly True
  - required dirs are not set
  - BGE URL is empty
  - token is in the config file (must come from env)
  - any required threshold is missing

The example file at scripts/funasr_phase0/phase0-config.example.json uses
loopback addresses only; production config lives OUTSIDE the repo and is
not committed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = "phase0-config/2"


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Thresholds:
    bge_p95_degradation_pct: float
    bge_error_rate_pct: float
    bge_5xx_consecutive: int
    asr_peak_vram_gb: float
    asr_steady_vram_gb: float
    combined_vram_max_gb: float
    disk_free_min_gb: float
    short_failure_rate_pct: float
    long_failure_rate_pct: float
    cer_max_clear: float
    cer_max_bim: float
    bim_term_recall_min: float
    code_recall_min: float
    rtf_max: float
    timestamp_p95_drift_ms_short: float
    timestamp_p95_drift_ms_long: float


@dataclass(frozen=True)
class Config:
    schema_version: str
    run_id: str
    approved_window_start: dt.datetime
    approved_window_end: dt.datetime
    shared_production_gpu_confirmed: bool
    bge_base_url: str
    bge_expected_model: str
    bge_expected_reranker: str
    bge_expected_device: str
    bge_expected_torch_version: str
    embed_rpm: int
    rerank_rpm: int
    baseline_duration_s: float
    testdata_root: str
    models_root: str
    reports_root: str
    logs_root: str
    checkpoints_root: str
    audio_chunk_s: float
    allowed_asr_model_ids: tuple[str, ...]
    allowed_asr_revisions: tuple[str, ...]
    vad_model_id: str
    vad_model_revision: str
    punc_model_id: str
    punc_model_revision: str
    short_sample_tolerance_s: float
    thresholds: Thresholds
    extra: dict[str, Any] = field(default_factory=dict)
    config_sha256: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_ALLOWED_MODELS = {
    "paraformer-zh",
    "paraformer-large",
    "iic/SenseVoiceSmall",
    "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404",
    "damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "damo/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
}


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(s: str) -> dt.datetime:
    if not isinstance(s, str):
        raise ValueError(f"window timestamp must be string, got {type(s).__name__}")
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"window timestamp not ISO-8601: {s!r} ({e})") from e
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"window timestamp must include a UTC offset: {s!r}")
    return parsed


def _require(d: Mapping[str, Any], key: str) -> Any:
    if key not in d:
        raise ValueError(f"config missing required key: {key!r}")
    return d[key]


def _sha256_of_dict(d: Mapping[str, Any]) -> str:
    raw = json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────


def load_config(path: str | os.PathLike[str]) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    raw_obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict):
        raise ValueError("config must be a JSON object")

    schema = raw_obj.get("schema_version", "")
    if schema != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"config schema_version mismatch: got {schema!r}, want {CONFIG_SCHEMA_VERSION!r}"
        )

    # Token guard
    if "bge_token" in raw_obj and raw_obj["bge_token"]:
        raise ValueError(
            "bge_token MUST be empty in config file; supply via env GPU_SERVICE_TOKEN"
        )

    # run_id
    run_id = str(_require(raw_obj, "run_id"))
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"run_id invalid (must match {_RUN_ID_RE.pattern}): {run_id!r}")

    # window
    win_start = _parse_iso(str(_require(raw_obj, "approved_window_start")))
    win_end = _parse_iso(str(_require(raw_obj, "approved_window_end")))
    if win_end <= win_start:
        raise ValueError(
            f"approved_window_end ({win_end.isoformat()}) must be > "
            f"approved_window_start ({win_start.isoformat()})"
        )

    # Confirmation must be explicit, but CPU-only tooling may load a config
    # whose confirmation is false.  GPU entry gating enforces True below.
    if not isinstance(raw_obj.get("shared_production_gpu_confirmed"), bool):
        raise ValueError("shared_production_gpu_confirmed must be an explicit boolean")

    # BGE URL
    bge_url = str(_require(raw_obj, "bge_base_url"))
    if not re.match(r"^https?://[A-Za-z0-9._-]+(:\d+)?(/.*)?$", bge_url):
        raise ValueError(f"bge_base_url invalid: {bge_url!r}")

    # Allowed model IDs and revisions
    allowed_ids = tuple(_require(raw_obj, "allowed_asr_model_ids"))
    for mid in allowed_ids:
        if mid not in _ALLOWED_MODELS:
            raise ValueError(f"allowed_asr_model_ids contains non-whitelisted id: {mid!r}")
    allowed_revs = tuple(_require(raw_obj, "allowed_asr_revisions"))
    for rev in allowed_revs:
        if not re.match(r"^[A-Za-z0-9._-]{3,64}$", rev):
            raise ValueError(f"allowed_asr_revisions entry invalid: {rev!r}")
    if not allowed_ids or len(allowed_ids) != len(allowed_revs):
        raise ValueError(
            "allowed_asr_model_ids and allowed_asr_revisions must be non-empty and have equal length"
        )

    vad_model_id = str(_require(raw_obj, "vad_model_id"))
    vad_model_revision = str(_require(raw_obj, "vad_model_revision"))
    punc_model_id = str(_require(raw_obj, "punc_model_id"))
    punc_model_revision = str(_require(raw_obj, "punc_model_revision"))
    for name, value in (
        ("vad_model_id", vad_model_id), ("punc_model_id", punc_model_id),
        ("vad_model_revision", vad_model_revision),
        ("punc_model_revision", punc_model_revision),
    ):
        if not value or not re.match(r"^[A-Za-z0-9._/-]{3,128}$", value):
            raise ValueError(f"{name} invalid: {value!r}")

    # thresholds
    th_raw = _require(raw_obj, "thresholds")
    try:
        thresholds = Thresholds(
            bge_p95_degradation_pct=float(_require(th_raw, "bge_p95_degradation_pct")),
            bge_error_rate_pct=float(_require(th_raw, "bge_error_rate_pct")),
            bge_5xx_consecutive=int(_require(th_raw, "bge_5xx_consecutive")),
            asr_peak_vram_gb=float(_require(th_raw, "asr_peak_vram_gb")),
            asr_steady_vram_gb=float(_require(th_raw, "asr_steady_vram_gb")),
            combined_vram_max_gb=float(_require(th_raw, "combined_vram_max_gb")),
            disk_free_min_gb=float(_require(th_raw, "disk_free_min_gb")),
            short_failure_rate_pct=float(_require(th_raw, "short_failure_rate_pct")),
            long_failure_rate_pct=float(_require(th_raw, "long_failure_rate_pct")),
            cer_max_clear=float(_require(th_raw, "cer_max_clear")),
            cer_max_bim=float(_require(th_raw, "cer_max_bim")),
            bim_term_recall_min=float(_require(th_raw, "bim_term_recall_min")),
            code_recall_min=float(_require(th_raw, "code_recall_min")),
            rtf_max=float(_require(th_raw, "rtf_max")),
            timestamp_p95_drift_ms_short=float(_require(th_raw, "timestamp_p95_drift_ms_short")),
            timestamp_p95_drift_ms_long=float(_require(th_raw, "timestamp_p95_drift_ms_long")),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"thresholds invalid: {e}") from e

    positive_fields = {
        "bge_p95_degradation_pct": thresholds.bge_p95_degradation_pct,
        "bge_error_rate_pct": thresholds.bge_error_rate_pct,
        "bge_5xx_consecutive": thresholds.bge_5xx_consecutive,
        "asr_peak_vram_gb": thresholds.asr_peak_vram_gb,
        "asr_steady_vram_gb": thresholds.asr_steady_vram_gb,
        "combined_vram_max_gb": thresholds.combined_vram_max_gb,
        "disk_free_min_gb": thresholds.disk_free_min_gb,
        "timestamp_p95_drift_ms_short": thresholds.timestamp_p95_drift_ms_short,
        "timestamp_p95_drift_ms_long": thresholds.timestamp_p95_drift_ms_long,
        "rtf_max": thresholds.rtf_max,
    }
    for name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"thresholds.{name} must be > 0, got {value}")
    for name, value in {
        "short_failure_rate_pct": thresholds.short_failure_rate_pct,
        "long_failure_rate_pct": thresholds.long_failure_rate_pct,
    }.items():
        if not 0 <= value <= 100:
            raise ValueError(f"thresholds.{name} must be in [0, 100], got {value}")
    for name, value in {
        "cer_max_clear": thresholds.cer_max_clear,
        "cer_max_bim": thresholds.cer_max_bim,
        "bim_term_recall_min": thresholds.bim_term_recall_min,
        "code_recall_min": thresholds.code_recall_min,
    }.items():
        if not 0 <= value <= 1:
            raise ValueError(f"thresholds.{name} must be in [0, 1], got {value}")

    for name in ("embed_rpm", "rerank_rpm", "baseline_duration_s", "audio_chunk_s"):
        value = float(_require(raw_obj, name))
        if value <= 0:
            raise ValueError(f"{name} must be > 0, got {value}")

    cfg = Config(
        schema_version=schema,
        run_id=run_id,
        approved_window_start=win_start,
        approved_window_end=win_end,
        shared_production_gpu_confirmed=raw_obj["shared_production_gpu_confirmed"],
        bge_base_url=bge_url.rstrip("/"),
        bge_expected_model=str(_require(raw_obj, "bge_expected_model")),
        bge_expected_reranker=str(_require(raw_obj, "bge_expected_reranker")),
        bge_expected_device=str(_require(raw_obj, "bge_expected_device")),
        bge_expected_torch_version=str(_require(raw_obj, "bge_expected_torch_version")),
        embed_rpm=int(_require(raw_obj, "embed_rpm")),
        rerank_rpm=int(_require(raw_obj, "rerank_rpm")),
        baseline_duration_s=float(_require(raw_obj, "baseline_duration_s")),
        testdata_root=str(_require(raw_obj, "testdata_root")),
        models_root=str(_require(raw_obj, "models_root")),
        reports_root=str(_require(raw_obj, "reports_root")),
        logs_root=str(_require(raw_obj, "logs_root")),
        checkpoints_root=str(_require(raw_obj, "checkpoints_root")),
        audio_chunk_s=float(_require(raw_obj, "audio_chunk_s")),
        allowed_asr_model_ids=allowed_ids,
        allowed_asr_revisions=allowed_revs,
        vad_model_id=vad_model_id,
        vad_model_revision=vad_model_revision,
        punc_model_id=punc_model_id,
        punc_model_revision=punc_model_revision,
        short_sample_tolerance_s=float(_require(raw_obj, "short_sample_tolerance_s")),
        thresholds=thresholds,
        extra={k: v for k, v in raw_obj.items() if k not in {
            "schema_version", "run_id", "approved_window_start", "approved_window_end",
            "shared_production_gpu_confirmed", "bge_base_url", "bge_expected_model",
            "bge_expected_reranker", "bge_expected_device", "bge_expected_torch_version",
            "embed_rpm", "rerank_rpm", "baseline_duration_s", "testdata_root",
            "models_root", "reports_root", "logs_root", "checkpoints_root",
            "audio_chunk_s", "allowed_asr_model_ids", "allowed_asr_revisions",
            "vad_model_id", "vad_model_revision", "punc_model_id", "punc_model_revision",
            "short_sample_tolerance_s", "thresholds",
        }},
        config_sha256=_sha256_of_dict(raw_obj),
        raw=raw_obj,
    )
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Gate
# ─────────────────────────────────────────────────────────────────────────────


class ConfigGateError(RuntimeError):
    """Raised when a Phase 0 GPU entry must not start."""


def gate_for_gpu_entry(cfg: Config, *, command_name: str) -> None:
    """Refuse to start any GPU-consuming entry if config is not approved.

    Called by every entry script right after load_config(). Raises
    ConfigGateError with a specific reason.
    """
    now = _now_utc()
    if not (cfg.approved_window_start <= now <= cfg.approved_window_end):
        raise ConfigGateError(
            f"{command_name}: current time {now.isoformat()} is OUTSIDE approved "
            f"window [{cfg.approved_window_start.isoformat()}, "
            f"{cfg.approved_window_end.isoformat()}]"
        )
    if not cfg.shared_production_gpu_confirmed:
        raise ConfigGateError(f"{command_name}: shared_production_gpu_confirmed is False")
    if not cfg.bge_base_url:
        raise ConfigGateError(f"{command_name}: bge_base_url empty")
    _gate_paths(cfg, command_name)
    if not cfg.allowed_asr_model_ids:
        raise ConfigGateError(f"{command_name}: allowed_asr_model_ids empty")


def gate_for_cpu_entry(cfg: Config, *, command_name: str) -> None:
    """Validate paths required by CPU-only preparation/audit commands.

    CPU-only annotation and license reporting must not require an active GPU
    maintenance window, but they still fail closed on incomplete path config.
    """
    _gate_paths(cfg, command_name)


def _gate_paths(cfg: Config, command_name: str) -> None:
    for name in ("testdata_root", "models_root", "reports_root", "logs_root",
                 "checkpoints_root"):
        raw = getattr(cfg, name)
        if not raw:
            raise ConfigGateError(f"{command_name}: {name} empty")
        path = Path(raw)
        if not path.is_absolute():
            raise ConfigGateError(f"{command_name}: {name} must be absolute: {raw!r}")
        if not path.is_dir():
            raise ConfigGateError(f"{command_name}: {name} directory does not exist: {path}")


def selected_asr_model(cfg: Config, index: int = 0) -> tuple[str, str]:
    try:
        return cfg.allowed_asr_model_ids[index], cfg.allowed_asr_revisions[index]
    except IndexError as e:
        raise ConfigGateError(f"ASR model index out of range: {index}") from e


# ─────────────────────────────────────────────────────────────────────────────
# CLI helper
# ─────────────────────────────────────────────────────────────────────────────


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        required=True,
        help="path to phase0-config JSON (MUST exist; production config outside repo)",
    )


def config_sha256_of_file(path: str | os.PathLike[str]) -> str:
    return _sha256_of_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def to_jsonable(cfg: Config) -> dict[str, Any]:
    d = asdict(cfg)
    d["thresholds"] = asdict(cfg.thresholds)
    d["approved_window_start"] = cfg.approved_window_start.isoformat()
    d["approved_window_end"] = cfg.approved_window_end.isoformat()
    return d
