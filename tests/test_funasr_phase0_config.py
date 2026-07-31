from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.funasr_phase0.lib_config import (
    CONFIG_SCHEMA_VERSION,
    ConfigGateError,
    gate_for_cpu_entry,
    gate_for_gpu_entry,
    load_config,
)


def valid_config(root: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "run_id": "test-run-001",
        "approved_window_start": (now - dt.timedelta(minutes=1)).isoformat(),
        "approved_window_end": (now + dt.timedelta(minutes=10)).isoformat(),
        "shared_production_gpu_confirmed": True,
        "bge_base_url": "http://127.0.0.1:18080",
        "bge_expected_model": "BAAI/bge-m3",
        "bge_expected_reranker": "BAAI/bge-reranker-v2-m3",
        "bge_expected_device": "cuda",
        "bge_expected_torch_version": "2.7.0+cu128",
        "embed_rpm": 20, "rerank_rpm": 10, "baseline_duration_s": 300,
        "testdata_root": root, "models_root": root, "reports_root": root,
        "logs_root": root, "checkpoints_root": root, "audio_chunk_s": 60,
        "allowed_asr_model_ids": ["iic/SenseVoiceSmall"],
        "allowed_asr_revisions": ["v1.0.0"],
        "vad_model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "vad_model_revision": "v2.0.4",
        "punc_model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "punc_model_revision": "v2.0.4",
        "short_sample_tolerance_s": 0.0,
        "thresholds": {
            "bge_p95_degradation_pct": 100.0, "bge_error_rate_pct": 0.5,
            "bge_5xx_consecutive": 3, "asr_peak_vram_gb": 8.0,
            "asr_steady_vram_gb": 6.0, "combined_vram_max_gb": 14.0,
            "disk_free_min_gb": 5.0, "short_failure_rate_pct": 0.0,
            "long_failure_rate_pct": 5.0, "cer_max_clear": 0.10,
            "cer_max_bim": 0.15, "bim_term_recall_min": 0.70,
            "code_recall_min": 0.95, "rtf_max": 0.6,
            "timestamp_p95_drift_ms_short": 1500.0,
            "timestamp_p95_drift_ms_long": 3000.0,
        },
    }


def write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestConfig(unittest.TestCase):
    def test_naive_window_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["approved_window_start"] = "2026-07-31T10:00:00"
            path = Path(td) / "cfg.json"
            write_config(path, data)
            with self.assertRaisesRegex(ValueError, "UTC offset"):
                load_config(path)

    def test_model_revision_lists_must_align(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["allowed_asr_revisions"] = []
            path = Path(td) / "cfg.json"
            write_config(path, data)
            with self.assertRaisesRegex(ValueError, "equal length"):
                load_config(path)

    def test_threshold_range_is_checked(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["thresholds"]["code_recall_min"] = 1.2
            path = Path(td) / "cfg.json"
            write_config(path, data)
            with self.assertRaisesRegex(ValueError, "code_recall_min"):
                load_config(path)

    def test_cpu_tool_can_load_unconfirmed_config_but_gpu_cannot(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["shared_production_gpu_confirmed"] = False
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            gate_for_cpu_entry(cfg, command_name="annotation")
            with self.assertRaises(ConfigGateError):
                gate_for_gpu_entry(cfg, command_name="worker")


if __name__ == "__main__":
    unittest.main()
