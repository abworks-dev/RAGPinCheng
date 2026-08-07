"""Filesystem-isolated test harness for the FunASR Phase 0 sandbox."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from scripts.funasr_phase0.lib_config import CONFIG_SCHEMA_VERSION


def build_test_config(root: str | Path, *, run_id: str = "test-run-001",
                      bge_base_url: str = "http://127.0.0.1:18080",
                      **overrides: Any) -> dict[str, Any]:
    approved_root = Path(root).resolve()
    paths = {
        name: approved_root / name
        for name in ("testdata", "models", "reports", "logs", "checkpoints")
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    data: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "execution_mode": "test",
        "approved_root": str(approved_root),
        "run_id": run_id,
        "approved_window_start": (now - dt.timedelta(minutes=1)).isoformat(),
        "approved_window_end": (now + dt.timedelta(minutes=10)).isoformat(),
        "shared_production_gpu_confirmed": True,
        "bge_base_url": bge_base_url,
        "bge_expected_model": "BAAI/bge-m3",
        "bge_expected_reranker": "BAAI/bge-reranker-v2-m3",
        "bge_expected_device": "cuda",
        "bge_expected_torch_version": "2.7.0+cu128",
        "embed_rpm": 20,
        "rerank_rpm": 10,
        "baseline_duration_s": 300,
        "testdata_root": str(paths["testdata"]),
        "models_root": str(paths["models"]),
        "reports_root": str(paths["reports"]),
        "logs_root": str(paths["logs"]),
        "checkpoints_root": str(paths["checkpoints"]),
        "audio_chunk_s": 60,
        "allowed_asr_model_ids": ["iic/SenseVoiceSmall"],
        "allowed_asr_revisions": ["v1.0.0"],
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "vad_model_revision": "v2.0.4",
        "punc_model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "punc_model_revision": "v2.0.4",
        "short_sample_tolerance_s": 0.0,
        "thresholds": {
            "bge_p95_degradation_pct": 100.0,
            "bge_error_rate_pct": 0.5,
            "bge_5xx_consecutive": 3,
            "asr_peak_vram_gb": 8.0,
            "asr_steady_vram_gb": 6.0,
            "combined_vram_max_gb": 14.0,
            "disk_free_min_gb": 5.0,
            "short_failure_rate_pct": 0.0,
            "long_failure_rate_pct": 5.0,
            "cer_max_clear": 0.10,
            "cer_max_bim": 0.15,
            "bim_term_recall_min": 0.70,
            "code_recall_min": 0.95,
            "rtf_max": 0.6,
            "timestamp_p95_drift_ms_short": 1500.0,
            "timestamp_p95_drift_ms_long": 3000.0,
        },
    }
    data.update(overrides)
    return data


class FunASRTestHarness:
    """Own one temporary root and every filesystem path used by a test."""

    def __init__(self, *, prefix: str = "ragpincheng-funasr-test-") -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._tmp.name).resolve()
        self.paths = {
            name: self.root / name
            for name in ("testdata", "models", "reports", "logs", "checkpoints")
        }
        for path in self.paths.values():
            path.mkdir(parents=True)

    def cleanup(self) -> None:
        if os.environ.get("KEEP_TEST_ARTIFACTS") == "1":
            self._tmp._finalizer.detach()
            print(f"KEEP_TEST_ARTIFACTS=1: retained FunASR test root {self.root}")
            return
        self._tmp.cleanup()

    def __enter__(self) -> "FunASRTestHarness":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()

    def config(self, *, run_id: str = "test-run-001",
               bge_base_url: str = "http://127.0.0.1:18080",
               **overrides: Any) -> dict[str, Any]:
        return build_test_config(
            self.root,
            run_id=run_id,
            bge_base_url=bge_base_url,
            **overrides,
        )

    def write_config(self, *, name: str = "phase0-config.json",
                     **overrides: Any) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(self.config(**overrides), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
