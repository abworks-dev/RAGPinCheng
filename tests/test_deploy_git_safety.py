from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestDeployGitSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/deploy-production.yml").read_text(
            encoding="utf-8"
        )
        cls.emergency_workflow = (
            ROOT / ".github/workflows/deploy-production-emergency.yml"
        ).read_text(encoding="utf-8")
        cls.windows = (ROOT / "scripts/deploy-gpu.ps1").read_text(encoding="utf-8")
        cls.promote = (ROOT / "scripts/promote-gpu-runtime.ps1").read_text(
            encoding="utf-8"
        )
        cls.windows_start = (ROOT / "scripts/start-gpu-service.ps1").read_text(
            encoding="utf-8"
        )
        cls.linux = (ROOT / "scripts/deploy-app.sh").read_text(encoding="utf-8")

    def test_no_script_persists_authenticated_remote(self):
        for name, text in (("windows", self.windows), ("linux", self.linux)):
            with self.subTest(name=name):
                self.assertNotIn("remote set-url", text)
                self.assertNotIn("x-access-token:${gitToken}@", text)
                self.assertNotIn("x-access-token:${GIT_TOKEN}@", text)

    def test_workflow_passes_manual_commit_to_both_jobs(self):
        self.assertGreaterEqual(self.workflow.count("DEPLOY_COMMIT_SHA:"), 2)
        self.assertGreaterEqual(self.workflow.count("DEPLOY_COMMIT_SHA: ${{ github.sha }}"), 2)
        self.assertNotIn("github.event.workflow_run", self.workflow)

    def test_emergency_workflow_fails_visibly_without_explicit_confirmation(self):
        self.assertIn("name: Deploy Production Emergency", self.emergency_workflow)
        self.assertIn("workflow_dispatch:", self.emergency_workflow)
        self.assertIn("default: CANCEL", self.emergency_workflow)
        self.assertIn("- DEPLOY", self.emergency_workflow)
        self.assertNotIn("if: ${{ inputs.confirm_production", self.emergency_workflow)
        self.assertIn("$env:CONFIRM_PRODUCTION -ne 'DEPLOY'", self.emergency_workflow)

    def test_emergency_workflow_is_master_only_and_preserves_deploy_order(self):
        self.assertIn("$env:SELECTED_REF -ne 'refs/heads/master'", self.emergency_workflow)
        self.assertGreaterEqual(
            self.emergency_workflow.count("DEPLOY_COMMIT_SHA: ${{ github.sha }}"), 2
        )
        self.assertIn("needs: [deploy-gpu]", self.emergency_workflow)
        self.assertIn("production-gpu-exclusive", self.emergency_workflow)
        self.assertIn("production-app-deployment", self.emergency_workflow)
        self.assertNotIn("cleanup-after-deploy", self.emergency_workflow)

    def test_emergency_workflow_diagnoses_divergence_without_overwriting_it(self):
        self.assertEqual(self.emergency_workflow.count("PRODUCTION_GIT_DIVERGENCE"), 2)
        self.assertEqual(self.emergency_workflow.count("PRODUCTION_GIT_LOCAL_ONLY"), 2)
        self.assertGreaterEqual(
            self.emergency_workflow.count("git merge-base --is-ancestor"), 2
        )
        self.assertGreaterEqual(
            self.emergency_workflow.count("git status --porcelain --untracked-files=no"),
            2,
        )
        self.assertNotIn("git reset", self.emergency_workflow)
        self.assertNotIn("git checkout --force", self.emergency_workflow)

    def test_scripts_require_full_commit_and_verify_head(self):
        self.assertIn("ValidatePattern('^[0-9a-fA-F]{40}$')", self.windows)
        self.assertIn("Deployed HEAD mismatch", self.windows)
        self.assertRegex(self.linux, re.escape("^[0-9a-fA-F]{40}$"))
        self.assertIn("deployed HEAD mismatch", self.linux)

    def test_fetch_uses_process_local_http_header(self):
        for text in (self.workflow, self.windows, self.linux):
            self.assertIn("http.extraHeader=AUTHORIZATION: basic", text)

    def test_fetch_is_http11_proxy_aware_and_bounded(self):
        self.assertIn("http.version=HTTP/1.1", self.workflow)
        self.assertIn("1..4", self.workflow)
        self.assertIn("http.version=HTTP/1.1", self.windows)
        self.assertIn("1..4", self.windows)
        self.assertIn("1 2 3 4", self.linux)
        for text in (self.workflow, self.windows, self.linux):
            self.assertNotIn("http.sslVerify=false", text)
            self.assertNotIn("git config --global http.proxy", text)

    def test_gpu_deploy_never_installs_into_global_python(self):
        self.assertNotIn("Scripts\\pip.exe", self.windows)
        self.assertNotIn("pip install", self.windows)
        self.assertIn("build-gpu-runtime.ps1", self.windows)
        self.assertIn("promote-gpu-runtime.ps1", self.windows)
        self.assertIn("Automatic GPU promotion requires a validated lock", self.windows)

    def test_unchanged_gpu_runtime_only_checks_health(self):
        self.assertIn("current-release.json", self.windows)
        self.assertIn("source_fingerprint", self.windows)
        self.assertIn("status=unchanged health=ok", self.windows)
        self.assertIn("refusing an implicit repair", self.windows)
        self.assertIn("runtime_source_fingerprint", self.windows)
        self.assertIn("runtime_lock_sha256", self.windows)

    def test_windows_gpu_service_uses_owned_release_task(self):
        self.assertIn('$TaskName = "RAGPinCheng-GPU"', self.promote)
        self.assertIn("New-ScheduledTaskAction", self.promote)
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", self.promote)
        self.assertIn('-UserId "Administrator"', self.promote)
        self.assertIn("-LogonType S4U", self.promote)
        self.assertIn("Register-ScheduledTask", self.promote)
        self.assertIn("Assert-OwnedTask", self.promote)
        self.assertIn("Refusing to modify an unexpected RAGPinCheng-GPU", self.promote)

    def test_start_wrapper_requires_validated_immutable_release(self):
        self.assertIn("GPU runtime release is not validated for production", self.windows_start)
        self.assertIn("GPU qualification evidence does not match", self.windows_start)
        self.assertIn("Join-Path $env:PRODUCTION_RUNTIME_ROOT 'releases'", self.windows_start)
        self.assertIn("runtime_python", self.windows_start)
        self.assertIn("RERANKER_USE_FP16", self.windows_start)
        self.assertIn("source-files.sha256.json", self.windows_start)
        self.assertIn("Set-Location -LiteralPath $sourceRoot", self.windows_start)
        self.assertNotIn("C:\\Program Files\\Python310\\python.exe", self.windows_start)

    def test_promotion_has_task_environment_and_release_rollback(self):
        self.assertIn("scheduled-task.xml", self.promote)
        self.assertIn("gpu-service.env", self.promote)
        self.assertIn("current-release.json", self.promote)
        self.assertIn("Register-ScheduledTask -TaskName $TaskName -Xml", self.promote)
        self.assertIn("Previous GPU release did not recover cleanly", self.promote)
        self.assertIn("foreach ($attempt in 1..5)", self.promote)
        self.assertIn("GPU model-info does not identify the promoted CUDA release", self.promote)
        self.assertNotIn("qualify-gpu-runtime.ps1", self.promote)
        self.assertIn("Remove-Item -LiteralPath $EnvFile", self.promote)
        self.assertIn("Remove-Item -LiteralPath $CurrentReleasePath", self.promote)

    def test_application_deploy_checks_gpu_health_and_contract(self):
        self.assertIn('${GPU_URL}/health', self.linux)
        self.assertIn("d.get('status') == 'ok'", self.linux)
        self.assertIn('${GPU_URL}/model-info', self.linux)
        self.assertIn('expected 1024', self.linux)


if __name__ == "__main__":
    unittest.main()
