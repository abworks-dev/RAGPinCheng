"""Phase 0 ASR sandbox — monitor unit tests.

Per R2 spec §七 / §十五: uses fake BGE HTTP + fake worker,
no production BGE, no GPU.
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

from scripts.funasr_phase0 import lib_monitor  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fake BGE HTTP server
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(http.server.BaseHTTPRequestHandler):
    model_info_response: dict = {
        "embedding_model": "BAAI/bge-m3",
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "device": "cuda",
        "torch_version": "2.7.0+cu128",
    }
    health_response: dict = {"status": "ok", "model_loaded": True}
    fail_embed = False
    fail_rerank = False
    embed_delay_s = 0.0
    request_log: list[dict] = []

    def log_message(self, *_args):  # silence
        pass

    def do_GET(self):
        self.__class__.request_log.append({"method": "GET", "path": self.path})
        if self.path == "/health":
            self._json(self.__class__.health_response)
        elif self.path == "/model-info":
            self._json(self.__class__.model_info_response)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.request_log.append({"method": "POST", "path": self.path,
                                          "body": body[:200].decode("utf-8", "replace")})
        if self.path == "/v1/embeddings":
            if self.__class__.fail_embed:
                self.send_error(500)
                return
            if self.__class__.embed_delay_s > 0:
                time.sleep(self.__class__.embed_delay_s)
            self._json({"embeddings": [{"dense": [0.0] * 4, "sparse_indices": [0], "sparse_values": [0.0]}]})
        elif self.path == "/v1/rerank":
            if self.__class__.fail_rerank:
                self.send_error(500)
                return
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
def _fake_bge(health=None, model_info=None, fail_embed=False, fail_rerank=False):
    port = _free_port()
    _Handler.health_response = health if health is not None else {"status": "ok", "model_loaded": True}
    _Handler.model_info_response = model_info if model_info is not None else {
        "embedding_model": "BAAI/bge-m3",
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "device": "cuda",
        "torch_version": "2.7.0+cu128",
    }
    _Handler.fail_embed = fail_embed
    _Handler.fail_rerank = fail_rerank
    _Handler.request_log = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def _patched_smi():
    """Patch nvidia_smi_csv and asr_pid_vram_mib to safe local fakes."""
    orig = lib_monitor.nvidia_smi_csv
    orig_pid = lib_monitor.asr_pid_vram_mib
    orig_disk = lib_monitor.disk_free_gb
    lib_monitor.nvidia_smi_csv = lambda: {
        "index": 0, "memory_used_mib": 100.0, "memory_total_mib": 16000.0,
        "util_gpu_pct": 0.0, "util_mem_pct": 0.0, "temp_c": 30.0,
    }
    lib_monitor.asr_pid_vram_mib = lambda pid: 100.0
    lib_monitor.disk_free_gb = lambda path: 100.0
    try:
        yield
    finally:
        lib_monitor.nvidia_smi_csv = orig
        lib_monitor.asr_pid_vram_mib = orig_pid
        lib_monitor.disk_free_gb = orig_disk


class TestMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stop_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _make_cfg(self, base: str, **overrides) -> lib_monitor.MonitorConfig:
        cfg = lib_monitor.MonitorConfig(
            bge_health_url=base + "/health",
            bge_model_info_url=base + "/model-info",
            bge_auth_token="test-token",
            bge_expected_model="BAAI/bge-m3",
            bge_expected_reranker="BAAI/bge-reranker-v2-m3",
            bge_expected_device="cuda",
            bge_expected_torch_version="2.7.0+cu128",
            run_id="test-001",
            worker_pid=99999,
            worker_start_time_iso="2026-07-31T00:00:00+00:00",
            worker_script="tests.fake_worker",
            stop_reasons_dir=self.stop_dir,
            data_drive=self.stop_dir,
            bge_health_interval_s=0.05,
            bge_embed_ping_interval_s=0.05,
            bge_rerank_ping_interval_s=0.05,
            gpu_poll_interval_s=0.05,
            disk_poll_interval_s=0.05,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_health_degraded_triggers_stop(self):
        with _fake_bge(health={"status": "degraded", "model_loaded": False}) as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base)
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(1.0)
                m.stop()
                snap = m.snapshot()
                self.assertEqual(snap["stop_reason"], "bge_health_degraded")

    def test_5xx_streak_triggers_stop(self):
        with _fake_bge() as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base, bge_5xx_consecutive=3,
                                     enable_bge_probe_threads=False)
                m = lib_monitor.Monitor(cfg)
                m.start()
                for _ in range(3):
                    m.record_bge_result("embed", 500, 0.01)
                time.sleep(0.1)
                m.stop()
                self.assertEqual(m.snapshot()["stop_reason"], "bge_embed_5xx_streak")

    def test_p95_degradation_triggers_stop(self):
        with _fake_bge() as (base, _), _patched_smi():
            cfg = self._make_cfg(base, enable_bge_probe_threads=False,
                                 embed_baseline_p95_ms=10.0,
                                 bge_p95_degradation_pct=50.0)
            m = lib_monitor.Monitor(cfg)
            m.start()
            for _ in range(3):
                m.record_bge_result("embed", 200, 0.1)
            time.sleep(0.1)
            m.stop()
            self.assertEqual(m.snapshot()["stop_reason"], "bge_embed_p95_degraded")

    def test_model_info_mismatch_triggers_stop(self):
        with _fake_bge(model_info={"embedding_model": "WRONG"}) as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base)
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(0.3)
                m.stop()
                self.assertEqual(m.snapshot()["stop_reason"], "bge_model_info_mismatch")

    def test_no_deadlock_when_stop_callback_acquires_lock(self):
        # on_stop calls back into the monitor; we must not deadlock
        invoked = []

        def cb(reason, detail):
            invoked.append(reason)
            # Try to acquire snapshot lock from within callback (out of lock
            # but we re-enter public API; should not deadlock because
            # _trigger_stop does not hold the lock when calling us).
            m.snapshot()

        with _fake_bge(health={"status": "bad", "model_loaded": False}) as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base)
                m = lib_monitor.Monitor(cfg)
                cfg_local = cfg
                cfg_local.on_stop = cb
                m = lib_monitor.Monitor(cfg_local)
                m.start()
                time.sleep(1.0)
                m.stop()
                self.assertGreaterEqual(len(invoked), 1)

    def test_stop_callback_invoked_at_most_once(self):
        count = []

        def cb(reason, detail):
            count.append(reason)

        with _fake_bge(health={"status": "bad", "model_loaded": False}) as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base)
                cfg.on_stop = cb
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(1.0)
                m.stop()
                self.assertEqual(len(count), 1)

    def test_health_json_field_parsing(self):
        # Health is a non-dict string body -> should NOT be considered ok
        with _fake_bge(health="not a dict") as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base)
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(0.6)
                m.stop()
                snap = m.snapshot()
                # Should trigger bge_health_degraded because not 2xx_ok
                self.assertEqual(snap["stop_reason"], "bge_health_degraded")

    def test_stop_file_is_run_specific(self):
        with _fake_bge(health={"status": "bad", "model_loaded": False}) as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base, run_id="run-A")
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(0.6)
                m.stop()
                files = [f for f in os.listdir(self.stop_dir) if f.startswith("stop-")]
                self.assertTrue(any("run-A" in f for f in files))

    def test_stop_file_does_not_block_new_run(self):
        # Simulate a previous run leaving a stop file
        with open(os.path.join(self.stop_dir, "stop-run-OLD.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        with _fake_bge() as (base, _):
            with _patched_smi():
                cfg = self._make_cfg(base, run_id="run-NEW")
                m = lib_monitor.Monitor(cfg)
                m.start()
                time.sleep(0.3)
                m.stop()
                # No new stop file should be created (BGE healthy)
                files = [f for f in os.listdir(self.stop_dir) if "run-NEW" in f]
                self.assertEqual(len(files), 0)


if __name__ == "__main__":
    unittest.main()
