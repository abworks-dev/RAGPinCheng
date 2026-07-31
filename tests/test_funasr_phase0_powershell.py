"""CPU-only PowerShell parser and emergency-stop behavior tests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = REPO / "scripts" / "funasr_phase0"
PS06 = SANDBOX / "06_emergency_stop.ps1"
PS07 = SANDBOX / "07_verify_bge.ps1"
PS_SETUP = SANDBOX / "setup_venv.ps1"


def _ps_exe() -> str | None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ps51 = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if ps51.is_file():
        return str(ps51)
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _run_ps(script: Path, args: list[str], *, env: dict[str, str] | None = None,
            timeout: float = 30) -> subprocess.CompletedProcess[str]:
    exe = _ps_exe()
    if exe is None:
        raise unittest.SkipTest("Windows PowerShell is unavailable")
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _parse_ps(script: Path) -> subprocess.CompletedProcess[str]:
    exe = _ps_exe()
    if exe is None:
        raise unittest.SkipTest("Windows PowerShell is unavailable")
    quoted = str(script).replace("'", "''")
    command = (
        "$tokens=$null; $errors=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{quoted}',"
        "[ref]$tokens,[ref]$errors); "
        "if($errors.Count -gt 0){$errors|ForEach-Object{Write-Error $_};exit 1}"
    )
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_config(root: Path, run_id: str) -> Path:
    path = root / "phase0-config.json"
    _write_json(path, {
        "run_id": run_id,
        "logs_root": str(root),
        "reports_root": str(root),
        "bge_base_url": "http://127.0.0.1:1",
        "bge_expected_model": "BAAI/bge-m3",
        "bge_expected_reranker": "BAAI/bge-reranker-v2-m3",
        "bge_expected_device": "cuda",
        "bge_expected_torch_version": "2.7.0+cu128",
    })
    return path


def _write_active_run(root: Path, run_id: str, config_path: Path, worker_pid: int,
                      started_at: str) -> Path:
    path = root / "active-runs" / f"{run_id}.json"
    _write_json(path, {
        "schema_version": "phase0-runtime/1",
        "run_id": run_id,
        "config_file_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest().upper(),
        "worker_pid": worker_pid,
        "worker_start_time_iso": started_at,
        "worker_script": "03_run_short",
    })
    return path


def _latest_report(root: Path) -> dict:
    reports = sorted((root / "stop-events").glob("stop-*.json"), reverse=True)
    if not reports:
        raise AssertionError("emergency stop did not write a report")
    return json.loads(reports[0].read_text(encoding="utf-8-sig"))


class TestPowerShell51Parser(unittest.TestCase):
    def test_phase0_scripts_parse(self):
        for script in (PS_SETUP, PS06, PS07):
            with self.subTest(script=script.name):
                proc = _parse_ps(script)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestSetupVenvContract(unittest.TestCase):
    def test_pip_is_invoked_through_venv_python(self):
        source = PS_SETUP.read_text(encoding="utf-8")
        self.assertNotIn('Scripts\\pip.exe', source)
        self.assertNotIn('& $VenvPip', source)
        self.assertIn('& $VenvPython -m pip @PipArguments', source)
        self.assertIn('& $VenvPython -m pip freeze', source)

    def test_native_stderr_is_not_a_false_powershell_failure(self):
        source = PS_SETUP.read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = 'Continue'", source)
        self.assertIn('$pipExitCode = $LASTEXITCODE', source)
        self.assertIn('$freezeExitCode = $LASTEXITCODE', source)


class TestEmergencyStopBehavior(unittest.TestCase):
    def setUp(self):
        if _ps_exe() is None:
            self.skipTest("Windows PowerShell is unavailable")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_id = "ps-stop-test"
        self.config = _write_config(self.root, self.run_id)
        # Prevent the script from invoking the developer machine's real GPU CLI.
        (self.root / "nvidia-smi.cmd").write_text(
            "@echo off\r\necho 0, Fake GPU, 0, 16384, 0, 30\r\n", encoding="ascii"
        )
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.root) + os.pathsep + self.env.get("PATH", "")
        self.sleeper: subprocess.Popen | None = None

    def tearDown(self):
        if self.sleeper is not None and self.sleeper.poll() is None:
            self.sleeper.terminate()
            try:
                self.sleeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.sleeper.kill()
        self.tmp.cleanup()

    def test_hash_mismatch_still_stops_exact_worker_and_exits_2(self):
        self.sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "03_run_short.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        _write_active_run(self.root, self.run_id, self.config,
                          self.sleeper.pid, started_at)
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["operator_note"] = "config changed after worker launch"
        _write_json(self.config, config)

        proc = _run_ps(PS06, ["-ConfigPath", str(self.config)], env=self.env)
        try:
            self.sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            self.fail(f"worker was not stopped:\n{proc.stdout}\n{proc.stderr}")
        try:
            report = _latest_report(self.root)
        except AssertionError:
            self.fail(f"stop report missing:\n{proc.stdout}\n{proc.stderr}")

        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIsNotNone(self.sleeper.returncode)
        self.assertTrue(report["stop_succeeded"])
        self.assertTrue(report["stop_performed"])
        self.assertIn("active_run_config_hash_mismatch", report["integrity_warnings"])

    def test_malformed_active_run_exits_2_without_false_success(self):
        path = self.root / "active-runs" / f"{self.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not-json", encoding="utf-8")

        proc = _run_ps(PS06, ["-ConfigPath", str(self.config)], env=self.env)
        report = _latest_report(self.root)

        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertFalse(report["stop_succeeded"])
        self.assertEqual(report["reason"], "active_run_unreadable")

    def test_missing_active_run_exits_2_without_probing_gpu(self):
        proc = _run_ps(PS06, ["-ConfigPath", str(self.config)], env=self.env)
        report = _latest_report(self.root)

        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertFalse(report["stop_succeeded"])
        self.assertEqual(report["reason"], "active_run_not_found")
        self.assertEqual(list(self.root.glob("stop-*-snapshot.txt")), [])

    def test_list_only_without_active_run_is_non_destructive(self):
        proc = _run_ps(
            PS06, ["-ConfigPath", str(self.config), "-ListOnly"], env=self.env
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(list((self.root / "stop-events").glob("stop-*.json")), [])


if __name__ == "__main__":
    unittest.main()
