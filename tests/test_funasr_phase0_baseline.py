"""Phase 0 ASR sandbox — BGE baseline unit tests.

Per R2 spec §十三 / §十五: uses fake BGE HTTP, no production BGE.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import importlib.util
from scripts.funasr_phase0 import lib_config  # noqa: E402
from scripts.funasr_phase0.lib_runtime import load_matching_baseline  # noqa: E402
from tests.funasr_phase0_harness import build_test_config  # noqa: E402


def _import_module_by_path(module_name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SANDBOX = os.path.join(_REPO, "scripts", "funasr_phase0")
_01 = _import_module_by_path(
    "phase0_01_baseline",
    os.path.join(_SANDBOX, "01_measure_bge_baseline.py"),
)
_05 = _import_module_by_path(
    "phase0_05_coexist",
    os.path.join(_SANDBOX, "05_bge_coexist.py"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Fake BGE HTTP server
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(http.server.BaseHTTPRequestHandler):
    fail_embed = False
    fail_health = False
    bad_model_info = False
    request_count = {"embed": 0, "rerank": 0, "health": 0, "model_info": 0}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.__class__.request_count["health"] += 1
            if self.__class__.fail_health:
                self.send_error(500)
                return
            self._json({"status": "ok", "model_loaded": True})
        elif self.path == "/model-info":
            self.__class__.request_count["model_info"] += 1
            if self.__class__.bad_model_info:
                self._json({"embedding_model": "WRONG", "reranker_model": "X",
                            "device": "cpu", "torch_version": "0.0.0"})
                return
            self._json({
                "embedding_model": "BAAI/bge-m3",
                "reranker_model": "BAAI/bge-reranker-v2-m3",
                "device": "cuda",
                "torch_version": "2.7.0+cu128",
            })
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/v1/embeddings":
            self.__class__.request_count["embed"] += 1
            if self.__class__.fail_embed:
                self.send_error(500)
                return
            self._json({"embeddings": [{"dense": [0.0] * 4, "sparse_indices": [0], "sparse_values": [0.0]}]})
        elif self.path == "/v1/rerank":
            self.__class__.request_count["rerank"] += 1
            self._json({"scores": [0.5]})
        else:
            self.send_error(404)

    def _json(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@contextmanager
def _fake_bge():
    port = _free_port()
    _Handler.fail_embed = False
    _Handler.fail_health = False
    _Handler.bad_model_info = False
    _Handler.request_count = {"embed": 0, "rerank": 0, "health": 0, "model_info": 0}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        srv.shutdown()
        srv.server_close()


def _write_valid_config(path: str, base_url: str, run_id: str, root: str) -> None:
    cfg = build_test_config(
        root,
        run_id=run_id,
        bge_base_url=base_url,
        embed_rpm=60,
        rerank_rpm=30,
        baseline_duration_s=2.0,
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class TestBaseline(unittest.TestCase):
    def test_health_not_ok_writes_abort(self):
        with _fake_bge() as (base, _):
            _Handler.fail_health = True
            with tempfile.TemporaryDirectory() as td:
                cfg_path = os.path.join(td, "cfg.json")
                rep_dir = os.path.join(td, "rep")
                os.makedirs(rep_dir, exist_ok=True)
                _write_valid_config(cfg_path, base, "abort-test", td)
                # Override reports_root in config to td/rep
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg_obj = json.load(f)
                cfg_obj["reports_root"] = rep_dir
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg_obj, f)
                rc = _01.main(["--config", cfg_path])
                self.assertEqual(rc, 1)
                # Abort file should exist
                run_dir = os.path.join(rep_dir, "abort-test")
                aborts = [f for f in os.listdir(run_dir) if "abort" in f]
                self.assertTrue(len(aborts) >= 1)
                # Token must NOT be in body
                for f in aborts:
                    with open(os.path.join(run_dir, f), encoding="utf-8") as inp:
                        content = inp.read()
                    self.assertNotIn("token", content.lower())

    def test_model_info_mismatch_writes_abort(self):
        with _fake_bge() as (base, _):
            _Handler.bad_model_info = True
            with tempfile.TemporaryDirectory() as td:
                cfg_path = os.path.join(td, "cfg.json")
                rep_dir = os.path.join(td, "rep")
                os.makedirs(rep_dir, exist_ok=True)
                _write_valid_config(cfg_path, base, "mismatch-test", td)
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg_obj = json.load(f)
                cfg_obj["reports_root"] = rep_dir
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg_obj, f)
                rc = _01.main(["--config", cfg_path])
                self.assertEqual(rc, 1)
                run_dir = os.path.join(rep_dir, "mismatch-test")
                aborts = [f for f in os.listdir(run_dir) if "abort" in f]
                self.assertTrue(len(aborts) >= 1)
                with open(os.path.join(run_dir, aborts[0]), encoding="utf-8") as inp:
                    content = inp.read()
                self.assertIn("model_info_mismatch", content)

    def test_ok_baseline_writes_success_with_separate_embed_rerank_p95(self):
        with _fake_bge() as (base, _):
            with tempfile.TemporaryDirectory() as td:
                cfg_path = os.path.join(td, "cfg.json")
                rep_dir = os.path.join(td, "rep")
                os.makedirs(rep_dir, exist_ok=True)
                _write_valid_config(cfg_path, base, "ok-test", td)
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg_obj = json.load(f)
                cfg_obj["reports_root"] = rep_dir
                cfg_obj["baseline_duration_s"] = 2.0
                cfg_obj["embed_rpm"] = 60
                cfg_obj["rerank_rpm"] = 30
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg_obj, f)
                rc = _01.main(["--config", cfg_path])
                self.assertIn(rc, (0, 2))  # 0 if no errors, 2 if err_rate exceeded
                run_dir = os.path.join(rep_dir, "ok-test")
                success = [f for f in os.listdir(run_dir)
                          if "bge-baseline-" in f and "abort" not in f
                          and f.endswith(".json") and ".samples." not in f]
                self.assertEqual(len(success), 1)
                with open(os.path.join(run_dir, success[0]), encoding="utf-8") as f:
                    summary = json.load(f)
                self.assertIn("embed_p95_s", summary)
                self.assertIn("rerank_p95_s", summary)
                # embed and rerank tracked separately
                self.assertIsInstance(summary["embed_p95_s"], float)
                self.assertIsInstance(summary["rerank_p95_s"], float)
                # Config fingerprint present
                self.assertIn("target_id", summary)
                self.assertIn("config_sha256", summary)
                cfg = lib_config.load_config(cfg_path)
                loaded = load_matching_baseline(cfg)
                self.assertEqual(loaded["target_id"], summary["target_id"])


class TestConfigMatch(unittest.TestCase):
    def test_target_id_mismatch_blocks_load(self):
        # _latest_baseline-like filter: only ok=true + target_id + config_sha match
        with _fake_bge() as (base, _):
            with tempfile.TemporaryDirectory() as td:
                cfg_path = os.path.join(td, "cfg.json")
                _write_valid_config(cfg_path, base, "target-test", td)
                cfg = lib_config.load_config(cfg_path)
                # Synthesize a baseline with wrong target_id
                target = _05._target_id_for(cfg)
                fake_run_dir = os.path.join(cfg.reports_root, cfg.run_id)
                os.makedirs(fake_run_dir, exist_ok=True)
                with open(os.path.join(fake_run_dir, f"bge-baseline-test.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "schema_version": "phase0-bge-baseline/1",
                        "ok": True, "target_id": "WRONG",
                        "config_sha256": cfg.config_sha256,
                    }, f)
                with self.assertRaises(RuntimeError):
                    load_matching_baseline(cfg)

    def test_ok_baseline_with_matching_target_loads(self):
        from pathlib import Path
        with _fake_bge() as (base, _):
            with tempfile.TemporaryDirectory() as td:
                cfg_path = os.path.join(td, "cfg.json")
                _write_valid_config(cfg_path, base, "ok-load-test", td)
                cfg = lib_config.load_config(cfg_path)
                target = _05._target_id_for(cfg)
                run_dir = Path(cfg.reports_root) / cfg.run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                with open(run_dir / "bge-baseline-ok.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "schema_version": "phase0-bge-baseline/1",
                        "report_kind": "bge_baseline",
                        "ok": True, "target_id": target,
                        "config_sha256": cfg.config_sha256,
                        "embed_p95_s": 0.1, "rerank_p95_s": 0.2,
                        "embed_error_rate_pct": 0.0,
                        "rerank_error_rate_pct": 0.0,
                    }, f)
                result = load_matching_baseline(cfg)
                self.assertIsNotNone(result)


def PathType(p):  # tiny shim so we can pass string or Path
    from pathlib import Path
    return Path(p)


if __name__ == "__main__":
    unittest.main()
