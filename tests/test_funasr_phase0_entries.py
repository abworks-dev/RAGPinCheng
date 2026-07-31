from __future__ import annotations

import importlib.util
import json
import os
import hashlib
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from scripts.funasr_phase0 import lib_config, lib_monitor, lib_runtime
from tests.test_funasr_phase0_config import valid_config, write_config


def load_numbered(name: str, filename: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / "funasr_phase0" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SMOKE = load_numbered("phase0_smoke_entry", "02_compat_smoke.py")
SHORT = load_numbered("phase0_short_entry", "03_run_short.py")
ANNOTATE = load_numbered("phase0_annotate_entry", "08_annotate.py")


def guarded_env(root: Path, cfg, command_name: str) -> dict[str, str]:
    active = root / "active-runs"
    active.mkdir(exist_ok=True)
    nonce = f"nonce-{command_name}"
    guard = active / f"{command_name}.guard.json"
    import datetime as dt
    lib_runtime.atomic_json_dump(guard, {
        "schema_version": lib_runtime.RUNTIME_SCHEMA_VERSION,
        "run_id": cfg.run_id, "config_sha256": cfg.config_sha256,
        "command_name": command_name, "nonce": nonce,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return {
        lib_runtime.GUARD_ENV_FILE: str(guard),
        lib_runtime.GUARD_ENV_NONCE: nonce,
        "MODELSCOPE_CACHE": str(root / "modelscope"),
        "HF_HOME": str(root / "huggingface"),
    }


class TestSmokeFailClosed(unittest.TestCase):
    def test_disk_failure_returns_before_nvidia_or_torch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            cfg = lib_config.load_config(cfg_path)
            env = guarded_env(root, cfg, "02_compat_smoke")
            env["PHASE0_TEST_MODE"] = "1"
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(lib_monitor, "disk_free_gb", return_value=None), \
                 mock.patch.object(lib_monitor, "nvidia_smi_csv",
                                   side_effect=AssertionError("must not be called")):
                rc = SMOKE.main(["--config", str(cfg_path), "--no-bge-check"])
            self.assertEqual(rc, 1)

    def test_direct_worker_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "cfg.json"
            write_config(cfg_path, valid_config(td))
            with mock.patch.dict(os.environ, {}, clear=True):
                rc = SMOKE.main(["--config", str(cfg_path)])
            self.assertEqual(rc, 1)


class TestLicenseBeforeSpawn(unittest.TestCase):
    def test_license_failure_prevents_subprocess_creation(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "cfg.json"
            write_config(cfg_path, valid_config(td))
            cfg = lib_config.load_config(cfg_path)
            baseline = {"embed_p95_s": 0.1, "rerank_p95_s": 0.2}
            runtime = lib_runtime.GuardedProcess(
                cfg, str(cfg_path), "03_run_short", ["fake-python"], baseline=baseline,
            )
            with mock.patch.object(lib_runtime, "enforce_license_gate",
                                   side_effect=RuntimeError("blocked")), \
                 mock.patch.object(lib_runtime.subprocess, "Popen",
                                   side_effect=AssertionError("must not spawn")):
                with self.assertRaisesRegex(RuntimeError, "blocked"):
                    runtime.start()

    def test_active_run_lifecycle_is_owned_by_nonce(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            cfg = lib_config.load_config(cfg_path)
            baseline = {"embed_p95_s": 0.1, "rerank_p95_s": 0.2}

            class FakeProc:
                pid = 43210
                returncode = 0
                def poll(self): return 0
                def wait(self, timeout=None): return 0

            class FakeMonitor:
                def __init__(self, monitor_cfg): self.cfg = monitor_cfg
                def start(self): pass
                def stop(self): pass
                def snapshot(self): return {"stop_reason": None}

            runtime = lib_runtime.GuardedProcess(
                cfg, str(cfg_path), "03_run_short", ["fake-python"], baseline=baseline,
            )
            popen = mock.Mock(return_value=FakeProc())
            with mock.patch.object(lib_runtime, "enforce_license_gate"), \
                 mock.patch.object(lib_runtime.subprocess, "Popen", popen), \
                 mock.patch.object(lib_runtime, "Monitor", FakeMonitor), \
                 mock.patch.object(lib_runtime, "verify_bge", return_value={"ok": True}):
                runtime.start()
                worker_env = popen.call_args.kwargs["env"]
                self.assertEqual(worker_env["MODELSCOPE_OFFLINE"], "1")
                self.assertEqual(worker_env["HF_HUB_OFFLINE"], "1")
                self.assertEqual(worker_env["TRANSFORMERS_OFFLINE"], "1")
                self.assertTrue(runtime.active_path.exists())
                active = json.loads(runtime.active_path.read_text(encoding="utf-8"))
                self.assertEqual(active["worker_pid"], 43210)
                self.assertEqual(active["nonce"], runtime.nonce)
                runtime.close()
            self.assertFalse(runtime.active_path.exists())
            self.assertFalse(runtime.guard_path.exists())

    def test_monitor_callback_terminates_worker_and_verifies_bge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            cfg = lib_config.load_config(cfg_path)
            baseline = {"embed_p95_s": 0.1, "rerank_p95_s": 0.2}

            class RunningProc:
                pid = 54321
                returncode = None
                def poll(self): return None

            class FakeMonitor:
                def __init__(self, monitor_cfg): self.cfg = monitor_cfg
                def start(self): pass
                def stop(self): pass
                def snapshot(self): return {"stop_reason": "disk_low"}

            runtime = lib_runtime.GuardedProcess(
                cfg, str(cfg_path), "04_run_long", ["fake-python"], baseline=baseline,
            )
            with mock.patch.object(lib_runtime, "enforce_license_gate"), \
                 mock.patch.object(lib_runtime.subprocess, "Popen", return_value=RunningProc()), \
                 mock.patch.object(lib_runtime, "Monitor", FakeMonitor), \
                 mock.patch.object(lib_runtime, "terminate_process_tree") as terminate, \
                 mock.patch.object(lib_runtime, "verify_bge", return_value={"ok": True}):
                runtime.start()
                runtime.monitor.cfg.on_stop("disk_low", {"free_gb": 1})
                terminate.assert_called_once_with(54321)
                self.assertEqual(runtime.recovery, {"ok": True})
                runtime.proc = None
                runtime.close(verify=False)


class TestShortManifestGate(unittest.TestCase):
    @staticmethod
    def _stage_models(root: Path, data: dict) -> dict[str, Path]:
        staged = {}
        identities = [
            data["allowed_asr_model_ids"][0],
            data["vad_model_id"],
            data["punc_model_id"],
        ]
        for model_id in identities:
            model_dir = root.joinpath(*model_id.split("/"))
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "configuration.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.pt").write_bytes(b"test-weight")
            staged[model_id] = model_dir.resolve()
        return staged

    def test_empty_manifest_fails_before_model_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            cfg = lib_config.load_config(cfg_path)
            manifest = root / "empty.jsonl"
            manifest.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, guarded_env(root, cfg, "03_run_short"), clear=False), \
                 mock.patch.dict(__import__("sys").modules, {"torch": None, "funasr": None}):
                rc = SHORT.main(["--config", str(cfg_path), "--manifest", str(manifest)])
            self.assertEqual(rc, 1)

    def test_metric_threshold_failure_returns_nonzero_and_revision_is_passed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            data["thresholds"]["rtf_max"] = 10.0
            staged = self._stage_models(root, data)
            write_config(cfg_path, data)
            cfg = lib_config.load_config(cfg_path)
            scenarios = sorted(SHORT.REQUIRED_SHORT_SCENARIOS)
            manifest = root / "short.jsonl"
            rows = []
            for i, scenario in enumerate(scenarios):
                audio = root / f"s{i}.wav"
                with wave.open(str(audio), "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                    wf.writeframes(b"\x00\x00" * 1600)
                rows.append({
                    "id": f"s-{i}", "audio": audio.name, "scenario": scenario,
                    "reference_text": "a", "reference_segments": [],
                    "source_url": "https://example.invalid/sample", "license": "CC0",
                    "self_made": "non-sensitive synthetic fixture",
                    "is_internal_recording": False,
                    "annotation_version": "1",
                })
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            captured = {}

            class FakeModel:
                def __init__(self, **kwargs):
                    captured.update(kwargs)
                    captured["offline_env"] = {
                        name: os.environ.get(name) for name in (
                            "MODELSCOPE_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                        )
                    }
                    captured["generate_inputs"] = []
                def generate(self, **kwargs):
                    captured["generate_inputs"].append(kwargs["input"])
                    return [{"text": "b", "sentence_info": []}]

            class FakeCuda:
                @staticmethod
                def empty_cache(): pass
                @staticmethod
                def reset_peak_memory_stats(): pass
                @staticmethod
                def max_memory_allocated(): return 0

            class FakeSoundFile:
                @staticmethod
                def info(_path):
                    return types.SimpleNamespace(frames=1600, samplerate=16000)

            with mock.patch.dict(os.environ, guarded_env(root, cfg, "03_run_short"), clear=False), \
                 mock.patch.dict(__import__("sys").modules, {
                     "torch": types.SimpleNamespace(cuda=FakeCuda()),
                     "funasr": types.SimpleNamespace(AutoModel=FakeModel),
                     "soundfile": FakeSoundFile,
                 }):
                rc = SHORT.main(["--config", str(cfg_path), "--manifest", str(manifest)])
            self.assertEqual(rc, 2)
            self.assertEqual(captured["model"], str(staged[data["allowed_asr_model_ids"][0]]))
            self.assertEqual(captured["vad_model"], str(staged[data["vad_model_id"]]))
            self.assertEqual(captured["punc_model"], str(staged[data["punc_model_id"]]))
            self.assertNotIn("model_revision", captured)
            self.assertEqual(captured["offline_env"], {
                "MODELSCOPE_OFFLINE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            })
            self.assertEqual(len(captured["generate_inputs"]), 9)
            self.assertEqual(captured["generate_inputs"][0], str(root / "s0.wav"))
            self.assertEqual(captured["generate_inputs"][1], str(root / "s0.wav"))
            report = next((root / cfg.run_id).glob("03_run_short-*.json"))
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["n_threshold_failures"], 8)
            self.assertEqual(payload["model"]["warmup_sample_id"], "s-0")
            self.assertEqual(payload["model"]["revision"], "v1.0.0")
            self.assertIsNone(payload["diagnostic_sample_id"])
            self.assertTrue(all("diagnostic" not in row for row in payload["rows"]))

            diagnostic_out = root / cfg.run_id / "diagnostic.json"
            with mock.patch.dict(os.environ, guarded_env(root, cfg, "03_run_short"), clear=False), \
                 mock.patch.dict(__import__("sys").modules, {
                     "torch": types.SimpleNamespace(cuda=FakeCuda()),
                     "funasr": types.SimpleNamespace(AutoModel=FakeModel),
                     "soundfile": FakeSoundFile,
                 }):
                diagnostic_rc = SHORT.main([
                    "--config", str(cfg_path), "--manifest", str(manifest),
                    "--diagnostic-sample-id", "s-0", "--include-diagnostic-text",
                    "--out", str(diagnostic_out),
                ])
            self.assertEqual(diagnostic_rc, 2)
            self.assertEqual(len(captured["generate_inputs"]), 2)
            diagnostic_payload = json.loads(diagnostic_out.read_text(encoding="utf-8"))
            self.assertEqual(diagnostic_payload["diagnostic_sample_id"], "s-0")
            diagnostic_rows = [
                row for row in diagnostic_payload["rows"] if "diagnostic" in row
            ]
            self.assertEqual(len(diagnostic_rows), 1)
            self.assertEqual(diagnostic_rows[0]["diagnostic"]["reference_text"], "a")
            self.assertEqual(diagnostic_rows[0]["diagnostic"]["hypothesis_text"], "b")
            self.assertEqual(
                diagnostic_rows[0]["diagnostic"]["character_diff"][0]["operation"],
                "replace",
            )
            checkpoint = json.loads(
                (root / cfg.run_id / "03_run_short" / "s-0.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("diagnostic", checkpoint)

    def test_diagnostic_text_rejects_non_self_made_sample(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            write_config(cfg_path, data)
            cfg = lib_config.load_config(cfg_path)
            rows = []
            for i, scenario in enumerate(sorted(SHORT.REQUIRED_SHORT_SCENARIOS)):
                audio = root / f"s{i}.wav"
                with wave.open(str(audio), "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                    wf.writeframes(b"\x00\x00" * 1600)
                rows.append({
                    "id": f"s-{i}", "audio": audio.name, "scenario": scenario,
                    "is_internal_recording": False,
                })
            manifest = root / "short.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with mock.patch.dict(os.environ, guarded_env(root, cfg, "03_run_short"), clear=False):
                rc = SHORT.main([
                    "--config", str(cfg_path), "--manifest", str(manifest),
                    "--diagnostic-sample-id", "s-0", "--include-diagnostic-text",
                ])
            self.assertEqual(rc, 1)

    def test_missing_staged_weights_fail_before_funasr_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            write_config(cfg_path, data)
            cfg = lib_config.load_config(cfg_path)
            incomplete_model = root.joinpath(*data["allowed_asr_model_ids"][0].split("/"))
            incomplete_model.mkdir(parents=True)
            (incomplete_model / "configuration.json").write_text("{}", encoding="utf-8")
            rows = []
            for i, scenario in enumerate(sorted(SHORT.REQUIRED_SHORT_SCENARIOS)):
                audio = root / f"s{i}.wav"
                with wave.open(str(audio), "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                    wf.writeframes(b"\x00\x00" * 1600)
                rows.append({"id": f"s-{i}", "audio": audio.name, "scenario": scenario})
            manifest = root / "short.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with mock.patch.dict(os.environ, guarded_env(root, cfg, "03_run_short"), clear=False), \
                 mock.patch.dict(sys.modules, {"funasr": None}):
                rc = SHORT.main(["--config", str(cfg_path), "--manifest", str(manifest)])
            self.assertEqual(rc, 1)


class TestAnnotationContract(unittest.TestCase):
    def test_contiguous_segments_and_public_provenance_are_valid(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            data["shared_production_gpu_confirmed"] = False
            write_config(cfg_path, data)
            audio = root / "sample.wav"
            with wave.open(str(audio), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 16000)
            sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            draft = root / "draft.jsonl"
            draft.write_text(json.dumps({
                "id": "sample-1", "audio": "sample.wav", "audio_sha256": sha,
                "source_url": "https://example.invalid/public-sample",
                "license": "CC0", "internal_recording_consent_id": "",
                "scenario": "clear_single_speaker", "reference_text": "ab",
                "reference_segments": [
                    {"start_ms": 0, "end_ms": 500, "text": "a"},
                    {"start_ms": 500, "end_ms": 1000, "text": "b"},
                ],
                "annotator": "a", "reviewer": "b", "annotation_version": "1",
            }) + "\n", encoding="utf-8")
            out = root / "validated.jsonl"
            rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                "--out", str(out)])
            self.assertIsNone(rc)
            self.assertTrue(out.exists())

    def test_output_outside_testdata_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            draft = root / "draft.jsonl"
            draft.write_text("", encoding="utf-8")
            rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                "--out", str(Path(outside) / "out.jsonl")])
            self.assertEqual(rc, 1)

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            draft = root / "draft.jsonl"
            # Build a single line longer than the per-line limit.
            huge_line = "x" * (ANNOTATE.MAX_LINE_BYTES + 1)
            # Build a draft whose audio path is inside testdata_root, with a
            # single segment whose text is the oversized line.  This is a
            # pathological but realistic DoS-style input.
            audio = root / "oversize.wav"
            with wave.open(str(audio), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 1600)
            sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            draft.write_text(json.dumps({
                "id": "oversize", "audio": "oversize.wav", "audio_sha256": sha,
                "source_url": "https://example.invalid/x",
                "license": "CC0", "internal_recording_consent_id": "",
                "license_evidence": "self",
                "scenario": "clear_single_speaker",
                "reference_text": huge_line,
                "reference_segments": [
                    {"start_ms": 0, "end_ms": 100, "text": huge_line},
                ],
                "annotator": "a", "reviewer": "b", "annotation_version": "1",
            }) + "\n", encoding="utf-8")
            rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                "--out", str(root / "validated.jsonl")])
            self.assertEqual(rc, 1,
                             msg="oversized line must be rejected with non-zero exit")

    def test_input_file_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            draft = root / "draft.jsonl"
            draft.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(ANNOTATE, "MAX_INPUT_BYTES", 1):
                rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                    "--out", str(root / "validated.jsonl")])
            self.assertEqual(rc, 1)

    def test_input_line_count_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            write_config(cfg_path, valid_config(td))
            draft = root / "draft.jsonl"
            draft.write_text("# first\n# second\n", encoding="utf-8")
            with mock.patch.object(ANNOTATE, "MAX_INPUT_LINES", 1):
                rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                    "--out", str(root / "validated.jsonl")])
            self.assertEqual(rc, 1)

    def test_license_evidence_missing_is_advisory_not_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            data["shared_production_gpu_confirmed"] = False
            write_config(cfg_path, data)
            audio = root / "sample.wav"
            with wave.open(str(audio), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 16000)
            sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            draft = root / "draft.jsonl"
            # NO license_evidence field — should be advisory, not blocking.
            draft.write_text(json.dumps({
                "id": "sample-1", "audio": "sample.wav", "audio_sha256": sha,
                "source_url": "https://example.invalid/public-sample",
                "license": "CC0", "internal_recording_consent_id": "",
                "scenario": "clear_single_speaker", "reference_text": "ab",
                "reference_segments": [
                    {"start_ms": 0, "end_ms": 500, "text": "a"},
                    {"start_ms": 500, "end_ms": 1000, "text": "b"},
                ],
                "annotator": "a", "reviewer": "b", "annotation_version": "1",
            }) + "\n", encoding="utf-8")
            out = root / "validated.jsonl"
            rc = ANNOTATE.main(["--config", str(cfg_path), "--input", str(draft),
                                "--out", str(out)])
            self.assertIsNone(rc, msg="missing license_evidence must be advisory, not blocking")
            self.assertTrue(out.exists())
            # report sidecar should mention the advisory
            report_path = out.with_suffix(out.suffix + ".report.json")
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreater(len(report.get("advisory", [])), 0,
                               msg="report should record missing license_evidence as advisory")


if __name__ == "__main__":
    unittest.main()
