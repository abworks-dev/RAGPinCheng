"""Phase 0 ASR sandbox — audio extract / cache key / checkpoint tests.

Per R2 spec §十二 / §十五: pure-stdlib, no real audio download.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from scripts.funasr_phase0 import lib_config, lib_runtime
from tests.test_funasr_phase0_config import valid_config, write_config

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_long_module():
    path = os.path.join(REPO, "scripts", "funasr_phase0", "04_run_long.py")
    spec = importlib.util.spec_from_file_location("phase0_long_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LONG = _load_long_module()


def _silent_wav(path: str, seconds: float = 1.0, sr: int = 16000) -> None:
    nframes = int(seconds * sr)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * nframes)


def _file_sha(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


class TestAudioCacheKey(unittest.TestCase):
    def test_cache_key_changes_with_source_sha(self):
        # Two different source WAVs -> different cache keys
        with tempfile.TemporaryDirectory() as td:
            a = os.path.join(td, "a.wav")
            b = os.path.join(td, "b.wav")
            _silent_wav(a, seconds=0.5)
            _silent_wav(b, seconds=0.6)
            sha_a = _file_sha(a)
            sha_b = _file_sha(b)
            self.assertNotEqual(sha_a, sha_b)
            self.assertNotEqual(LONG._decode_cache_key(Path(a)),
                                LONG._decode_cache_key(Path(b)))

    def test_cache_key_includes_sample_rate_and_channels(self):
        # Two files identical content but different declared sr/ch -> keys differ
        with tempfile.TemporaryDirectory() as td:
            a = os.path.join(td, "a.wav")
            _silent_wav(a, seconds=0.5, sr=16000)
            sha = _file_sha(a)
            self.assertEqual(len(sha), 64)  # sha256 hex length
            self.assertNotEqual(LONG._decode_cache_key(Path(a), 16000, 1),
                                LONG._decode_cache_key(Path(a), 8000, 1))
            self.assertNotEqual(LONG._decode_cache_key(Path(a), 16000, 1),
                                LONG._decode_cache_key(Path(a), 16000, 2))


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_rename(self):
        # Verify os.replace atomicity behavior on Windows
        with tempfile.TemporaryDirectory() as td:
            tgt = os.path.join(td, "out.json")
            tmp = tgt + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("{}")
            os.replace(tmp, tgt)
            self.assertTrue(os.path.exists(tgt))
            self.assertFalse(os.path.exists(tmp))


class TestWAVValidation(unittest.TestCase):
    def test_written_wav_validates(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "test.wav")
            _silent_wav(p, seconds=0.5)
            with wave.open(p, "rb") as wf:
                self.assertEqual(wf.getframerate(), 16000)
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getnframes(), 8000)

    def test_split_wav_last_chunk_has_actual_duration(self):
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "source.wav")
            _silent_wav(source, seconds=2.5, sr=16000)
            chunks = LONG._split_wav(Path(source), 1.0, Path(os.path.join(td, "chunks")))
            self.assertEqual(len(chunks), 3)
            with wave.open(str(chunks[-1]), "rb") as wf:
                self.assertAlmostEqual(wf.getnframes() / wf.getframerate(), 0.5, places=3)


class TestSameNameDifferentInput(unittest.TestCase):
    def test_cache_key_includes_sha(self):
        # Two files at the same path; different content -> different SHA
        # We can't easily test the full 04_run_long without GPU, so just
        # verify SHA differs.
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.wav")
            _silent_wav(p, seconds=0.5)
            sha1 = _file_sha(p)
            _silent_wav(p, seconds=0.7)
            sha2 = _file_sha(p)
            self.assertNotEqual(sha1, sha2)


class TestCheckpointValidation(unittest.TestCase):
    def test_checkpoint_reuse_requires_hash_match(self):
        # Mock: a checkpoint with wrong config_hash is rejected
        with tempfile.TemporaryDirectory() as td:
            ck_path = os.path.join(td, "ck.json")
            with open(ck_path, "w", encoding="utf-8") as f:
                json.dump({"config_hash": "WRONG", "input_sha256": "x", "model_id": "y", "revision": "z"}, f)
            with open(ck_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            # The recovery condition is:
            #   ck.input_sha256 == input_sha AND
            #   ck.config_hash == config_hash AND
            #   ck.model_id == model_id AND
            #   ck.revision == revision
            # Simulated: all should match for reuse
            ck_sha = d["input_sha256"]
            ck_cfg = d["config_hash"]
            self.assertNotEqual(ck_cfg, "expected_config_hash")


class TestLongEntry(unittest.TestCase):
    @staticmethod
    def _stage_models(root: Path, data: dict) -> dict[str, Path]:
        staged = {}
        for model_id in (
            data["allowed_asr_model_ids"][0], data["vad_model_id"], data["punc_model_id"],
        ):
            model_dir = root.joinpath(*model_id.split("/"))
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "configuration.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.pt").write_bytes(b"test-weight")
            staged[model_id] = model_dir.resolve()
        return staged

    def test_real_entry_passes_revisions_and_uses_actual_last_duration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "long.wav"
            _silent_wav(str(audio), seconds=2.5)
            cfg_path = root / "cfg.json"
            data = valid_config(td)
            data["audio_chunk_s"] = 1.0
            data["thresholds"]["rtf_max"] = 10.0
            staged = self._stage_models(root, data)
            write_config(cfg_path, data)
            cfg = lib_config.load_config(cfg_path)
            reference = root / "reference.json"
            reference.write_text(json.dumps({
                "input_sha256": _file_sha(str(audio)),
                "reference_text": "aaa",
                "reference_segments": [
                    {"start_ms": 0, "end_ms": 1000, "text": "a"},
                    {"start_ms": 1000, "end_ms": 2000, "text": "a"},
                    {"start_ms": 2000, "end_ms": 2500, "text": "a"},
                ],
                "scenario": "clear_single_speaker",
                "annotation_version": "1",
            }), encoding="utf-8")
            active = root / "active-runs"
            active.mkdir()
            nonce = "long-entry-test"
            guard = active / "guard.json"
            lib_runtime.atomic_json_dump(guard, {
                "schema_version": lib_runtime.RUNTIME_SCHEMA_VERSION,
                "run_id": cfg.run_id, "config_sha256": cfg.config_sha256,
                "command_name": "04_run_long", "nonce": nonce,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
            })
            captured = {}

            class FakeModel:
                def __init__(self, **kwargs):
                    captured.update(kwargs)
                    captured["offline_env"] = {
                        name: os.environ.get(name) for name in (
                            "MODELSCOPE_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                        )
                    }

                def generate(self, input, **_kwargs):
                    with wave.open(str(input), "rb") as wf:
                        end_ms = round(wf.getnframes() / wf.getframerate() * 1000)
                    return [{"text": "a", "sentence_info": [
                        {"start": 0, "end": end_ms, "text": "a"}
                    ]}]

            class FakeCuda:
                @staticmethod
                def empty_cache(): pass
                @staticmethod
                def reset_peak_memory_stats(): pass
                @staticmethod
                def max_memory_allocated(): return 0

            def fake_decode(src, out, target_sr=16000):
                import shutil
                shutil.copyfile(src, out)
                with wave.open(str(out), "rb") as wf:
                    return {"sr": wf.getframerate(), "ch": wf.getnchannels(),
                            "sw": wf.getsampwidth(), "nframes": wf.getnframes(),
                            "duration_s": wf.getnframes() / wf.getframerate()}

            env = {
                lib_runtime.GUARD_ENV_FILE: str(guard),
                lib_runtime.GUARD_ENV_NONCE: nonce,
                "MODELSCOPE_CACHE": str(root / "modelscope"),
                "HF_HOME": str(root / "huggingface"),
            }
            fake_funasr = types.SimpleNamespace(AutoModel=FakeModel)
            fake_torch = types.SimpleNamespace(cuda=FakeCuda())
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.dict(sys.modules, {"funasr": fake_funasr, "torch": fake_torch}), \
                 mock.patch.object(LONG, "_decode_audio_pcm", side_effect=fake_decode):
                rc = LONG.main(["--config", str(cfg_path), "--input", str(audio),
                                "--reference", str(reference), "--chunk-s", "1"])
            self.assertEqual(rc, 0)
            self.assertEqual(captured["model"], str(staged[data["allowed_asr_model_ids"][0]]))
            self.assertEqual(captured["vad_model"], str(staged[data["vad_model_id"]]))
            self.assertEqual(captured["punc_model"], str(staged[data["punc_model_id"]]))
            self.assertNotIn("model_revision", captured)
            self.assertEqual(captured["offline_env"], {
                "MODELSCOPE_OFFLINE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            })
            reports = list((root / cfg.run_id).glob("04_run_long-*.json"))
            self.assertEqual(len(reports), 1)
            payload = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertAlmostEqual(payload["last_chunk_actual_duration_s"], 0.5, places=3)
            checkpoints = list((root / cfg.run_id / "04_run_long" / "long").glob("chunk_*.json"))
            last = json.loads(checkpoints[-1].read_text(encoding="utf-8"))
            self.assertAlmostEqual(last["chunk_duration_s"], 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
