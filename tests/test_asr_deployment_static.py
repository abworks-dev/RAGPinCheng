from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_windows_asr_layout_and_config_ownership_are_frozen():
    env = read("asr_service/.env.example")
    deploy = read("scripts/deploy-asr.ps1")
    start = read("scripts/start-asr-service.ps1")
    assert r"${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR" in deploy
    assert r"${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR" in deploy
    assert r"config\asr.env" in start
    assert 'Join-Path $PSScriptRoot ".."' not in start
    assert ".env.example" not in start
    assert "ASR_MODEL_LOCAL_FILES_ONLY=true" in env
    assert "7bf452403abd7353a300cd760f7adae7701c92c1" in env
    assert "ASR_SERVICE_TOKEN=" in env
    assert (ROOT / "asr_service" / "requirements-windows.txt").is_file()
    assert not re.search(r"ASR_SERVICE_TOKEN=\S+", env)


def test_manual_workflow_has_safe_defaults_and_immutable_revision():
    workflow = read(".github/workflows/deploy-asr-production.yml")
    assert "workflow_dispatch:" in workflow
    assert "production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert workflow.count("default: false") == 2
    assert workflow.count("shell: powershell") == 2
    assert "shell: pwsh" not in workflow
    assert re.search(r"install_dependencies:.*?default: false", workflow, re.DOTALL)
    assert re.search(r"activate_service:.*?default: false", workflow, re.DOTALL)
    assert "secrets.ASR_SERVICE_TOKEN" in workflow
    assert "inputs.commit_sha" in workflow
    assert "40-character" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow


def test_git_sha_validation_uses_command_scoped_safe_directory():
    deploy = read("scripts/deploy-asr.ps1")
    assert '$safeDirectory = $resolvedSource.Replace("\\", "/")' in deploy
    assert 'git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD' in deploy
    assert "git config --global" not in deploy
    assert deploy.index("[string]::IsNullOrWhiteSpace($actualShaOutput)") < deploy.index("([string]$actualShaOutput).Trim()")


def test_deploy_script_never_downloads_models_or_changes_firewall():
    deploy = read("scripts/deploy-asr.ps1").lower()
    forbidden = (
        "new-netfirewallrule",
        "set-netfirewallrule",
        "netsh advfirewall",
        "huggingface-cli",
        "snapshot_download",
        "modelscope download",
        "git lfs pull",
    )
    assert not any(item in deploy for item in forbidden)
    assert "if ($installdependencies)" in deploy
    assert "requirements-windows.txt" in deploy
    assert "^[0-9a-fA-F]{40}$" in read("scripts/deploy-asr.ps1")


def test_deploy_uses_only_machine_wide_python_311_for_venv_creation():
    deploy = read("scripts/deploy-asr.ps1")
    assert "function Get-MachinePython311" in deploy
    assert r"HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\3.11\InstallPath" in deploy
    assert r"HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath" in deploy
    assert '$env:ProgramW6432' in deploy
    assert r'"Python311\python.exe"' in deploy
    assert "py -3.11" not in deploy
    assert "HKEY_CURRENT_USER" not in deploy
    assert '([string]$versionOutput).Trim() -eq "3.11"' in deploy
    assert '([string]$venvVersion).Trim() -ne "3.11"' in deploy


def test_deploy_archives_stale_and_failed_staging_without_deleting_it():
    deploy = read("scripts/deploy-asr.ps1")
    assert "function Move-StagingToBackup" in deploy
    assert 'Move-StagingToBackup -Path $staging -Reason "stale"' in deploy
    assert 'Move-StagingToBackup -Path $staging -Reason "failed"' in deploy
    assert '"{0}-staging-{1}-{2}"' in deploy
    assert 'throw "Staging directory already exists' not in deploy
    assert "Remove-Item" not in deploy


def test_service_secret_is_not_passed_on_scheduled_task_command_line():
    deploy = read("scripts/deploy-asr.ps1")
    action_line = next(line for line in deploy.splitlines() if "New-ScheduledTaskAction" in line)
    assert "TOKEN" not in action_line.upper()
    assert "asr.env" not in action_line
    assert 'UserId "Administrator"' in deploy
    assert "-LogonType S4U" in deploy


def test_config_acl_preserves_trusted_runner_modify_without_full_control():
    deploy = read("scripts/deploy-asr.ps1")
    assert "/inheritance:r" in deploy
    assert "/grant:r" in deploy
    assert '"*S-1-5-32-544:(OI)(CI)F"' in deploy
    assert '"*S-1-5-18:(OI)(CI)F"' in deploy
    network_service_acl_lines = [
        line for line in deploy.splitlines() if "*S-1-5-20:" in line
    ]
    assert network_service_acl_lines == ['    "*S-1-5-20:(OI)(CI)M" | Out-Null']
    assert "(OI)(CI)F" not in network_service_acl_lines[0]
    assert "if ($InstallDependencies)" in deploy
    assert "if ($ActivateService)" in deploy


def test_gpu_activity_contract_and_ci_are_real_but_dependency_light():
    app = read("gpu_service/app.py")
    ci = read(".github/workflows/ci.yml")
    gpu_section = ci.split("  test-gpu-contract:", 1)[1].split(
        "  validate-migration-config:", 1
    )[0]
    assert '@app.get("/v1/activity"' in app
    assert "verify_token(request)" in app
    assert "gpu_service/tests/test_contract.py" in gpu_section
    assert "requirements-gpu" not in gpu_section
    assert "gpu_service/requirements.txt" not in gpu_section
    assert "|| true" not in gpu_section


def test_root_and_windows_env_templates_are_not_merged():
    backend = read(".env.example")
    windows = read("asr_service/.env.example")
    assert "Ubuntu backend ASR client settings" in backend
    assert "ASR_ENABLED=false" in backend
    assert "ASR_SERVICE_URL=http://${PRIVATE_IPV4}:8200" in backend
    assert "ASR_MODEL_CACHE_ROOT" not in backend
    assert "Windows ASR service configuration only" in windows
    assert "ASR_ENABLED=" not in windows


def test_backend_image_installs_and_deployment_verifies_ffmpeg_tools():
    dockerfile = read("docker/Dockerfile.backend")
    runtime = dockerfile.split("FROM python:3.11-slim", 1)[1]
    system_deps = runtime.split("WORKDIR /app", 1)[0]
    assert re.search(
        r"apt-get install -y --no-install-recommends.*\bcurl\b.*\bffmpeg\b",
        system_deps,
        re.DOTALL,
    )

    deploy = read("scripts/deploy-app.sh")
    media_check = 'echo ">> Verifying backend media tools"'
    assert "compose exec -T backend sh -lc" in deploy
    assert "command -v ffmpeg" in deploy
    assert "command -v ffprobe" in deploy
    assert deploy.index("compose up -d --no-deps backend") < deploy.index(media_check)
    assert deploy.index(media_check) < deploy.index("Waiting for backend health check")
