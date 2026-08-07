from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.funasr_phase0.lib_config import (
    ConfigGateError,
    gate_for_cpu_entry,
    gate_for_gpu_entry,
    load_config,
)
from tests.funasr_phase0_harness import build_test_config


def valid_config(root: str) -> dict:
    return build_test_config(root)


def write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestConfig(unittest.TestCase):
    def test_execution_mode_and_approved_root_are_required(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data.pop("execution_mode")
            path = Path(td) / "cfg.json"
            write_config(path, data)
            with self.assertRaisesRegex(ValueError, "execution_mode"):
                load_config(path)

    def test_test_mode_rejects_root_outside_temp_or_repo_test_root(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["approved_root"] = str(Path(__file__).resolve().parents[1])
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            with self.assertRaisesRegex(ConfigGateError, "test approved_root"):
                gate_for_cpu_entry(cfg, command_name="test")

    def test_ambiguous_posix_drive_alias_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["approved_root"] = "/e/Workspace/funasr-phase0-dev"
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            with self.assertRaisesRegex(ConfigGateError, "ambiguous POSIX drive alias"):
                gate_for_cpu_entry(cfg, command_name="test")
            self.assertFalse((Path(td) / "e").exists())

    def test_filesystem_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            root_anchor = Path(td).resolve().anchor
            data["execution_mode"] = "approved_sandbox"
            data["approved_root"] = root_anchor
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            with self.assertRaisesRegex(ConfigGateError, "filesystem root"):
                gate_for_cpu_entry(cfg, command_name="test")

    def test_path_outside_approved_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            data = valid_config(td)
            data["reports_root"] = outside
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            with self.assertRaisesRegex(ConfigGateError, "strictly inside"):
                gate_for_cpu_entry(cfg, command_name="test")

    def test_approved_sandbox_accepts_explicit_root_and_children(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["execution_mode"] = "approved_sandbox"
            path = Path(td) / "cfg.json"
            write_config(path, data)
            gate_for_cpu_entry(load_config(path), command_name="approved")

    def test_symlink_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            data = valid_config(td)
            link = Path(td) / "reports-link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            data["reports_root"] = str(link)
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            with self.assertRaisesRegex(ConfigGateError, "strictly inside"):
                gate_for_cpu_entry(cfg, command_name="test")

    def test_contextual_paraformer_exact_id_and_revision_are_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["allowed_asr_model_ids"] = [
                "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404"
            ]
            data["allowed_asr_revisions"] = [
                "6c4333d3114b38f1ab6aabecf1702c70a7b0df56"
            ]
            path = Path(td) / "cfg.json"
            write_config(path, data)
            cfg = load_config(path)
            self.assertEqual(cfg.allowed_asr_model_ids, tuple(data["allowed_asr_model_ids"]))

    def test_contextual_paraformer_near_miss_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = valid_config(td)
            data["allowed_asr_model_ids"] = [
                "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-typo"
            ]
            path = Path(td) / "cfg.json"
            write_config(path, data)
            with self.assertRaisesRegex(ValueError, "non-whitelisted"):
                load_config(path)

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
