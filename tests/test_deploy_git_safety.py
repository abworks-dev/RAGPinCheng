from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestDeployGitSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/deploy-production.yml").read_text(encoding="utf-8")
        cls.windows = (ROOT / "scripts/deploy-gpu.ps1").read_text(encoding="utf-8")
        cls.windows_start = (ROOT / "scripts/start-gpu-service.ps1").read_text(encoding="utf-8")
        cls.linux = (ROOT / "scripts/deploy-app.sh").read_text(encoding="utf-8")

    def test_no_script_persists_authenticated_remote(self):
        for name, text in (("windows", self.windows), ("linux", self.linux)):
            with self.subTest(name=name):
                self.assertNotIn("remote set-url", text)
                self.assertNotIn("x-access-token:${gitToken}@", text)
                self.assertNotIn("x-access-token:${GIT_TOKEN}@", text)

    def test_workflow_passes_event_commit_to_both_jobs(self):
        self.assertGreaterEqual(self.workflow.count("DEPLOY_COMMIT_SHA:"), 2)
        self.assertIn("github.event.workflow_run.head_sha || github.sha", self.workflow)

    def test_scripts_require_full_commit_and_verify_head(self):
        self.assertIn("ValidatePattern('^[0-9a-fA-F]{40}$')", self.windows)
        self.assertIn("Deployed HEAD mismatch", self.windows)
        self.assertRegex(self.linux, re.escape("^[0-9a-fA-F]{40}$"))
        self.assertIn("deployed HEAD mismatch", self.linux)

    def test_fetch_uses_process_local_http_header(self):
        for text in (self.workflow, self.windows, self.linux):
            self.assertIn("http.extraHeader=AUTHORIZATION: basic", text)

    def test_linux_fetch_uses_optional_proxy_http11_and_bounded_retry(self):
        for text in (self.workflow, self.linux):
            with self.subTest(source="workflow" if text is self.workflow else "linux"):
                self.assertIn("DEPLOY_HTTP_PROXY", text)
                self.assertIn("http.version=HTTP/1.1", text)
                self.assertIn("1 2 3 4", text)
                self.assertIn("2 **", text)
                self.assertNotIn("http.sslVerify=false", text)
                self.assertNotIn("git config --global http.proxy", text)

    def test_windows_fetch_uses_optional_proxy_http11_and_bounded_retry(self):
        for text in (self.workflow, self.windows):
            with self.subTest(source="workflow" if text is self.workflow else "windows"):
                self.assertIn("DEPLOY_HTTP_PROXY" if text is self.workflow else "ProxyUrl", text)
                self.assertIn("http.version=HTTP/1.1", text)
                self.assertIn("1..4", text)
                self.assertIn("[math]::Pow(2, $attempt)", text)
                self.assertNotIn("http.sslVerify=false", text)
                self.assertNotIn("git config --global http.proxy", text)

    def test_windows_pull_failure_is_not_warning_only(self):
        self.assertNotIn('Write-Warning "git pull failed', self.windows)
        self.assertIn('throw "git fetch failed', self.windows)
        self.assertIn('throw "git fast-forward failed', self.windows)

    def test_windows_gpu_service_uses_owned_scheduled_task(self):
        self.assertIn('$TaskName = "RAGPinCheng-GPU"', self.windows)
        self.assertIn("New-ScheduledTaskAction", self.windows)
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", self.windows)
        self.assertIn('-UserId "Administrator"', self.windows)
        self.assertIn("-LogonType S4U", self.windows)
        self.assertIn("Register-ScheduledTask", self.windows)
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", self.windows)
        self.assertIn("Assert-GpuTaskIsOurs", self.windows)
        self.assertIn(
            "Refusing to modify an unexpected RAGPinCheng-GPU Scheduled Task",
            self.windows,
        )
        self.assertNotIn("$process = Start-Process", self.windows)

    def test_windows_gpu_task_owns_foreground_process_and_strict_env(self):
        self.assertIn(
            "GPU_SERVICE_TOKEN is required; refusing to generate or rotate it",
            self.windows,
        )
        self.assertNotIn("secrets.token_hex", self.windows)
        self.assertIn(
            '& $python -m gpu_service.app 1>> $logFile 2>> $errorLogFile',
            self.windows_start,
        )
        self.assertIn("Duplicate GPU service environment key", self.windows_start)
        self.assertIn("GPU_SERVICE_TOKEN must not be empty", self.windows_start)
        self.assertIn("GPU service HOST must be 192.168.11.11", self.windows_start)
        self.assertIn("GPU service PORT must be 8100", self.windows_start)
        self.assertIn("HF_HUB_OFFLINE=1", self.windows)
        self.assertIn("TRANSFORMERS_OFFLINE=1", self.windows)
        self.assertIn("GPU service HF_HUB_OFFLINE must be 1", self.windows_start)
        self.assertIn("GPU service TRANSFORMERS_OFFLINE must be 1", self.windows_start)
        self.assertNotIn("GIT_TOKEN", self.windows_start)
        self.assertNotIn("RUNNER_TRACKING_ID", self.windows_start)

    def test_windows_gpu_deploy_rolls_back_owned_task_and_listener(self):
        self.assertIn("Remove-ManagedGpuTaskAndListener", self.windows)
        self.assertIn("Stop-VerifiedGpuListeners", self.windows)
        self.assertIn("ForEach-Object { $_.OwningProcess }", self.windows)
        self.assertNotIn("$connections.OwningProcess", self.windows)
        self.assertIn("Unregister-ScheduledTask", self.windows)
        self.assertIn("Stop-Process -Id $processId -Force", self.windows)
        self.assertIn("Refusing to stop an unexpected process listening on TCP 8100", self.windows)
        self.assertIn('$task.State -ne "Running"', self.windows)
        self.assertIn("TCP port 8100 is not listening after GPU service activation", self.windows)
        self.assertIn("GPU service deployment failed; rolling back the managed task", self.windows)
        self.assertIn("Get-Content -LiteralPath $path -Tail 120", self.windows)


if __name__ == "__main__":
    unittest.main()
