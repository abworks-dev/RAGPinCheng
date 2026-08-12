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
        cls.app_only_workflow = (
            ROOT / ".github/workflows/deploy-production-app-emergency.yml"
        ).read_text(encoding="utf-8")
        cls.app_backup_recovery_workflow = (
            ROOT / ".github/workflows/recover-production-app-backup.yml"
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

    def test_approved_app_backup_recovery_is_fixed_atomic_and_narrow(self):
        workflow = self.app_backup_recovery_workflow

        self.assertIn("RESTORE_APP_ONLY_31552723068_1", workflow)
        self.assertIn('BACKUP_PATH="${BACKUP_DIR}/app-only-31552723068-1"', workflow)
        self.assertIn('for name in ("app.sqlite", "parents.sqlite")', workflow)
        self.assertIn("PRAGMA integrity_check", workflow)
        self.assertIn('"${COMPOSE[@]}" stop backend', workflow)
        self.assertIn("os.replace(temporary, target)", workflow)
        self.assertIn('docker tag "${OLD_IMAGE_ID}" pincheng-rag-backend:latest', workflow)
        self.assertIn('"${RUNNING_IMAGE_ID}" = "${OLD_IMAGE_ID}"', workflow)
        self.assertIn("CURRENT_SCHEMA_VERSION == 5", workflow)
        self.assertIn("APP_BACKUP_RECOVERY status=complete", workflow)
        self.assertIn('container_status={{.State.Status}}', workflow)
        self.assertIn('logs --no-color --tail 200 backend', workflow)
        self.assertLess(
            workflow.index('"${COMPOSE[@]}" stop backend'),
            workflow.index("os.replace(temporary, target)"),
        )
        self.assertLess(
            workflow.index("os.replace(temporary, target)"),
            workflow.index('docker tag "${OLD_IMAGE_ID}" pincheng-rag-backend:latest'),
        )
        self.assertNotIn("qdrant", workflow.lower())
        self.assertNotIn("git merge", workflow)

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
            self.emergency_workflow.count(
                "DEPLOY_COMMIT_SHA: ${{ inputs.deploy_commit_sha }}"
            ),
            4,
        )
        self.assertNotIn("DEPLOY_COMMIT_SHA: ${{ github.sha }}", self.emergency_workflow)
        self.assertIn("deploy_commit_sha:", self.emergency_workflow)
        self.assertIn("ARCHIVE_AND_REALIGN", self.emergency_workflow)
        self.assertIn("needs: [deploy-gpu]", self.emergency_workflow)
        self.assertIn("production-gpu-exclusive", self.emergency_workflow)
        self.assertEqual(
            self.emergency_workflow.count(
                "production-app-emergency-${{ github.run_id }}"
            ),
            2,
        )
        self.assertIn("group: production-emergency-deployment-v2", self.emergency_workflow)
        self.assertNotIn("production-app-deployment", self.emergency_workflow)
        self.assertNotIn("cleanup-after-deploy", self.emergency_workflow)

    def test_emergency_workflow_prepares_app_before_gpu(self):
        self.assertIn("preflight-app:", self.emergency_workflow)
        self.assertIn("needs: [preflight-app]", self.emergency_workflow)
        self.assertIn(
            "PRODUCTION_GIT_PREFLIGHT node=app status=ready",
            self.emergency_workflow,
        )
        preflight = self.emergency_workflow.split("  deploy-gpu:", 1)[0]
        self.assertNotIn("deploy-app.sh", preflight)
        self.assertIn("source.backup(target)", preflight)
        self.assertIn("PRAGMA integrity_check", preflight)
        self.assertIn("qdrant-snapshot.json", preflight)
        self.assertIn("backend-image.txt", preflight)
        self.assertLess(
            preflight.index("PRODUCTION_BACKUP_OK path="),
            preflight.index('git branch -f master "${DEPLOY_COMMIT_SHA}"'),
        )

    def test_emergency_workflow_archives_divergence_before_realigning(self):
        self.assertEqual(self.emergency_workflow.count("PRODUCTION_GIT_DIVERGENCE"), 3)
        self.assertEqual(self.emergency_workflow.count("PRODUCTION_GIT_LOCAL_ONLY"), 3)
        self.assertGreaterEqual(
            self.emergency_workflow.count("git merge-base --is-ancestor"), 3
        )
        self.assertGreaterEqual(
            self.emergency_workflow.count("git status --porcelain --untracked-files=no"),
            3,
        )
        self.assertNotIn("git reset", self.emergency_workflow)
        self.assertNotIn("git checkout --force", self.emergency_workflow)
        self.assertIn("archive/production-app-before-realignment-", self.emergency_workflow)
        self.assertIn("archive/production-gpu-before-realignment-", self.emergency_workflow)
        self.assertGreaterEqual(self.emergency_workflow.count("git branch -f master"), 2)
        self.assertIn("EXPECTED_APP_HEAD", self.emergency_workflow)
        self.assertIn("EXPECTED_GPU_HEAD", self.emergency_workflow)
        self.assertIn(
            'git merge-base --is-ancestor "${DEPLOY_COMMIT_SHA}" "${WORKFLOW_COMMIT_SHA}"',
            self.emergency_workflow,
        )
        gpu = self.emergency_workflow.split("  deploy-gpu:", 1)[1].split(
            "  deploy-app:", 1
        )[0]
        self.assertLess(
            gpu.index("archive/production-gpu-before-realignment-"),
            gpu.index("& git branch -f master $env:DEPLOY_COMMIT_SHA"),
        )

    def test_app_only_manual_workflow_uses_the_selected_master_sha(self):
        workflow = self.app_only_workflow

        self.assertIn("name: Deploy Production App Manual", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("- DEPLOY_APP", workflow)
        self.assertIn("DEPLOY_COMMIT_SHA: ${{ github.sha }}", workflow)
        self.assertIn("SELECTED_REF: ${{ github.ref }}", workflow)
        self.assertIn('"${SELECTED_REF}" = "refs/heads/master"', workflow)
        self.assertIn('"${REPOSITORY_HEAD}" "${DEPLOY_COMMIT_SHA}"', workflow)
        self.assertIn("selected master commit is not a fast-forward", workflow)
        self.assertNotIn("APPROVED_DEPLOY_COMMIT", workflow)
        self.assertNotIn("4ed480e47057dc3414ad0ea3bb0e95d2d1a4c833", workflow)
        self.assertNotIn("git reset", workflow)
        self.assertNotIn("git branch -f", workflow)
        self.assertNotIn("deploy-gpu.ps1", workflow)
        self.assertNotIn("promote-gpu-runtime.ps1", workflow)

    def test_app_only_emergency_workflow_pins_gpu_contract_and_rolls_back_image(self):
        workflow = self.app_only_workflow

        self.assertIn('gpu_service/runtime-lock.json', workflow)
        self.assertIn('get("validation_status", "")', workflow)
        self.assertIn('get("qualification_run_id", "")', workflow)
        self.assertIn('get("source_commit", "")', workflow)
        self.assertIn('get("qualified_source_fingerprint", "")', workflow)
        self.assertIn('get("qualified_lock_sha256", "")', workflow)
        self.assertIn("COMPUTED_GPU_FINGERPRINT", workflow)
        self.assertIn("COMPUTED_LOCK_SHA256", workflow)
        self.assertIn("EXPECTED_GPU_RELEASE_ID=", workflow)
        self.assertNotIn("9b147c448b9b-fa16678de682", workflow)
        for path in (
            "src/providers.py",
            "gpu_service/app.py",
            "gpu_service/models.py",
            "gpu_service/schemas.py",
        ):
            self.assertIn(path, workflow)
        self.assertIn("APP_ONLY_CONTRACT status=identical", workflow)
        self.assertIn("/v1/embeddings", workflow)
        self.assertIn("/v1/rerank", workflow)
        self.assertIn("source.backup(target)", workflow)
        self.assertIn("PRAGMA integrity_check", workflow)
        self.assertIn("qdrant-snapshot.json", workflow)
        self.assertIn('assert d.get("result", {}).get("name")', workflow)
        self.assertIn("--max-time 120", workflow)
        self.assertIn("flock -n 9", workflow)
        self.assertIn('ROLLBACK_IMAGE_TAG="pincheng-rag-backend:app-only-rollback-', workflow)
        self.assertIn('docker tag "${OLD_IMAGE_ID}" "${ROLLBACK_IMAGE_TAG}"', workflow)
        self.assertIn('docker tag "${ROLLBACK_IMAGE_TAG}" pincheng-rag-backend:latest', workflow)
        self.assertIn('"${COMPOSE[@]}" stop backend', workflow)
        self.assertIn('SRC="${BACKUP_PATH}" DST="${DATA_PATH}" python3', workflow)
        self.assertIn('os.replace(temporary, target)', workflow)
        self.assertIn('APP_ONLY_ROLLBACK_DATABASES status=restored', workflow)
        self.assertIn('stage=restore-databases', workflow)
        self.assertLess(
            workflow.index('APP_ONLY_ROLLBACK_DATABASES status=restored'),
            workflow.index('docker tag "${ROLLBACK_IMAGE_TAG}" pincheng-rag-backend:latest'),
        )
        self.assertIn("--force-recreate backend", workflow)
        self.assertIn('[ "${RUNNING_IMAGE_ID}" = "${OLD_IMAGE_ID}" ]', workflow)
        self.assertIn("stage=verify-admission", workflow)
        self.assertIn("APP_ONLY_ROLLBACK status=complete", workflow)

    def test_app_only_workflow_can_transactionally_activate_faster_whisper(self):
        workflow = self.app_only_workflow

        self.assertIn("transcription_admission:", workflow)
        self.assertIn("ENABLE_FASTER_WHISPER", workflow)
        self.assertIn("ENABLE_FASTER_WHISPER_AND_WHISPERX", workflow)
        self.assertIn("whisperx-large-v3-zh-align-experimental-v1", workflow)
        self.assertIn("CONFIGURED_TRANSCRIPTION_ADMITTED_PROFILE_IDS", workflow)
        self.assertIn("PREVIOUS_TRANSCRIPTION_ADMITTED_PROFILE_IDS", workflow)
        self.assertIn("PREVIOUS_ASR_ENABLED", workflow)
        self.assertIn("export ASR_ENABLED=true", workflow)
        self.assertIn('export ASR_ENABLED="${PREVIOUS_ASR_ENABLED}"', workflow)
        self.assertIn("EXPECTED_ROLLBACK_ASR_STATE", workflow)
        self.assertIn("source=workflow-environment", workflow)
        self.assertNotIn("configure-transcription-admission.py", workflow)
        self.assertIn("verify_transcription_admission", workflow)
        self.assertIn("DEPLOY_STATUS=$?", workflow)
        self.assertIn('if [ "${DEPLOY_STATUS}" -ne 0 ]; then', workflow)
        self.assertNotIn("if ! (", workflow)
        self.assertIn('states[FASTER_WHISPER_PROFILE_ID] == ("enabled", "available")', workflow)
        self.assertIn('states[WHISPERX_PROFILE_ID] == ("enabled", "available")', workflow)
        self.assertIn("profiles=\" + \",\".join(sorted(expected))", workflow)

        compose = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            "TRANSCRIPTION_ADMITTED_PROFILE_IDS: ${TRANSCRIPTION_ADMITTED_PROFILE_IDS:-",
            compose,
        )
        for name in (
            "ASR_ENABLED",
            "ASR_SERVICE_URL",
            "ASR_SERVICE_TOKEN",
            "ASR_CONNECT_TIMEOUT_SECONDS",
            "ASR_REQUEST_TIMEOUT_SECONDS",
            "ASR_JOB_TIMEOUT_SECONDS",
            "ASR_POLL_INTERVAL_MS",
            "ASR_UPLOAD_PART_BYTES",
            "ASR_EXPECTED_API_VERSION",
            "ASR_FFMPEG_PATH",
            "ASR_MEDIA_PREP_TIMEOUT_SECONDS",
        ):
            self.assertIn(f"{name}: ${{{name}:-", compose)
        self.assertIn("APP_ONLY_DEPLOY status=success", workflow)
        self.assertNotIn("GPU_SERVICE_TOKEN: ${{ vars.", workflow)
        for production_workflow in (self.workflow, self.emergency_workflow):
            self.assertIn(
                "ASR_ENABLED: ${{ vars.ASR_ENABLED }}",
                production_workflow,
            )
            self.assertIn(
                "TRANSCRIPTION_ADMITTED_PROFILE_IDS: "
                "${{ vars.TRANSCRIPTION_ADMITTED_PROFILE_IDS }}",
                production_workflow,
            )

    def test_production_content_root_is_fixed_and_strictly_verified(self):
        workflow = self.app_only_workflow
        compose = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("${CONTENT_HOST_PATH:-../content}:/app/content", compose)
        self.assertIn(
            "CONTENT_HOST_PATH: ${{ vars.PRODUCTION_CONTENT_ROOT }}", workflow
        )
        self.assertIn('/data/business/ragpincheng/content', workflow)
        self.assertIn("content_root_policy:", workflow)
        self.assertIn("REQUIRE_EMPTY", workflow)
        self.assertIn("PRESERVE_EXISTING", workflow)
        self.assertIn("production content root is unexpectedly non-empty", workflow)
        self.assertIn('--volume "${CONTENT_HOST_PATH}:/app/content"', workflow)
        self.assertIn("pincheng-rag-backend:latest", workflow)
        self.assertNotIn('mkdir -p "${CONTENT_HOST_PATH}"', workflow)
        self.assertIn("content-root-state.txt", workflow)
        self.assertIn("verify_managed_content", workflow)
        self.assertNotIn("CURRENT_SCHEMA_VERSION == 5", workflow)
        self.assertIn("schema={CURRENT_SCHEMA_VERSION}", workflow)
        self.assertIn("content_permission_groups", workflow)
        self.assertIn("content_permission_group_items", workflow)
        self.assertIn('("member", "普通成员"): []', workflow)
        self.assertIn('("bim_engineer", "BIM工程师"): ["organize"]', workflow)
        self.assertIn('("content_owner", "资料负责人"): ["review"]', workflow)
        self.assertIn('("system_admin", "系统管理员")', workflow)
        self.assertIn("MANAGED_CONTENT status=verified", workflow)
        self.assertIn("QDRANT_BEFORE_PATH", workflow)
        self.assertIn("points_count", workflow)
        self.assertIn("http://localhost/admin", workflow)
        for production_workflow in (
            self.workflow,
            self.emergency_workflow,
            self.app_only_workflow,
        ):
            self.assertIn(
                "CONTENT_HOST_PATH: ${{ vars.PRODUCTION_CONTENT_ROOT }}",
                production_workflow,
            )
            self.assertIn(
                "CONTENT_MANAGEMENT_ENABLED: ${{ vars.CONTENT_MANAGEMENT_ENABLED }}",
                production_workflow,
            )
            self.assertIn(
                "CONTENT_HEAD_ENFORCEMENT: ${{ vars.CONTENT_HEAD_ENFORCEMENT }}",
                production_workflow,
            )

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
