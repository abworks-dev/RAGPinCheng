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

    def test_windows_gpu_service_survives_runner_cleanup_without_checkout_token(self):
        self.assertIn('"RUNNER_TRACKING_ID"', self.windows)
        self.assertIn('"GIT_TOKEN"', self.windows)
        self.assertGreaterEqual(
            self.windows.count("[Environment]::SetEnvironmentVariable("),
            4,
        )
        self.assertIn("$null,", self.windows)
        self.assertIn("$runnerTrackingId,", self.windows)
        self.assertIn("$gitTokenForRestore,", self.windows)
        self.assertIn("$process = Start-Process", self.windows)
        self.assertNotIn("$psi.RedirectStandardOutput = $true", self.windows)
        self.assertNotIn("$psi.RedirectStandardError = $true", self.windows)

    def test_windows_gpu_service_persists_logs_and_cleans_failed_child(self):
        self.assertIn('$logFile = "$RepositoryPath\\gpu_service.log"', self.windows)
        self.assertIn(
            '$errorLogFile = "$RepositoryPath\\gpu_service.error.log"',
            self.windows,
        )
        self.assertIn("-RedirectStandardOutput $logFile", self.windows)
        self.assertIn("-RedirectStandardError $errorLogFile", self.windows)
        self.assertIn("Get-Content -LiteralPath $path -Tail 120", self.windows)
        self.assertIn("if (-not $process.HasExited)", self.windows)
        self.assertIn("Stop-Process -Id $process.Id -Force", self.windows)
        self.assertIn('throw "GPU service health check failed"', self.windows)


if __name__ == "__main__":
    unittest.main()
