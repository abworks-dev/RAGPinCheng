from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_windows_asr_layout_and_config_ownership_are_frozen():
    env = read("asr_service/.env.example")
    deploy = read("scripts/deploy-asr.ps1")
    start = read("scripts/start-asr-service.ps1")
    assert r"D:\Services\RAGPinCheng-ASR" in deploy
    assert r"D:\ServiceData\RAGPinCheng-ASR" in deploy
    assert r"config\asr.env" in start
    assert 'Join-Path $PSScriptRoot ".."' not in start
    assert ".env.example" not in start
    assert "ASR_MODEL_LOCAL_FILES_ONLY=true" in env
    assert "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT=" in env
    assert "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH=" in env
    assert "7bf452403abd7353a300cd760f7adae7701c92c1" in env
    assert "ASR_SERVICE_TOKEN=" in env
    assert (ROOT / "asr_service" / "requirements-windows.txt").is_file()
    windows_requirements = read("asr_service/requirements-windows.txt").lower()
    assert "faster-whisper" not in windows_requirements
    assert "ctranslate2" not in windows_requirements
    assert not re.search(r"ASR_SERVICE_TOKEN=\S+", env)


def test_manual_workflow_has_safe_defaults_and_immutable_revision():
    workflow = read(".github/workflows/deploy-asr-production.yml")
    assert "workflow_dispatch:" in workflow
    assert "production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert workflow.count("timeout-minutes: 60") == 1
    assert workflow.count("default: false") == 2
    assert workflow.count("shell: powershell") == 2
    assert "shell: pwsh" not in workflow
    assert re.search(r"install_dependencies:.*?default: false", workflow, re.DOTALL)
    assert re.search(r"activate_service:.*?default: false", workflow, re.DOTALL)
    assert "secrets.ASR_SERVICE_TOKEN" in workflow
    assert "ASR_DEPENDENCY_PROXY: ${{ secrets.ASR_DEPENDENCY_PROXY }}" in workflow
    assert "HTTP_PROXY:" not in workflow
    assert "HTTPS_PROXY:" not in workflow
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


def test_start_script_does_not_treat_uvicorn_stderr_as_a_terminating_error():
    start = read("scripts/start-asr-service.ps1")
    invocation = (
        "& $python -m uvicorn asr_service.app:create_app --factory "
        "--host $env:ASR_SERVICE_HOST --port $env:ASR_SERVICE_PORT *>> $logFile"
    )
    assert '$savedErrorActionPreference = $ErrorActionPreference' in start
    assert '$ErrorActionPreference = "Continue"' in start
    assert invocation in start
    assert "$uvicornExitCode = $LASTEXITCODE" in start
    assert "$ErrorActionPreference = $savedErrorActionPreference" in start
    assert "exit $uvicornExitCode" in start


def test_activation_secrets_are_written_only_to_the_protected_config():
    deploy = read("scripts/deploy-asr.ps1")
    assert "function Set-ProtectedConfigValue" in deploy
    assert (
        'Set-ProtectedConfigValue -Name "ASR_SERVICE_TOKEN" '
        "-Value $env:ASR_SERVICE_TOKEN"
    ) in deploy
    assert (
        'Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_TOKEN" '
        "-Value $env:BGE_PRIORITY_PROBE_TOKEN"
    ) in deploy
    assert (
        'Set-ProtectedConfigValue -Name "BGE_PRIORITY_PROBE_URL" '
        '-Value "http://192.168.11.11:8100/v1/activity"'
    ) in deploy
    assert 'Write-Host "$Name=' not in deploy
    assert 'Write-Output "$Name=' not in deploy


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
    assert "ASR_SERVICE_URL=http://192.168.11.11:8200" in backend
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

def test_dependency_proxy_is_scoped_to_pip_installation_and_restored():
    deploy = read("scripts/deploy-asr.ps1")
    install_guard = deploy.index("if ($InstallDependencies)")
    proxy_read = deploy.index("$env:ASR_DEPENDENCY_PROXY", install_guard)
    torch_install = deploy.index("pip install --index-url", proxy_read)
    requirements_install = deploy.index("pip install -r", torch_install)
    proxy_restore = deploy.index("foreach ($name in $savedProxyEnvironment.Keys)", requirements_install)
    activation_guard = deploy.index("if ($ActivateService)", proxy_restore)

    assert install_guard < proxy_read < torch_install < requirements_install < proxy_restore < activation_guard
    assert "ASR_DEPENDENCY_PROXY is required when InstallDependencies is enabled" in deploy
    assert "ASR_DEPENDENCY_PROXY must be an absolute HTTP(S) URL" in deploy
    assert '$env:HTTP_PROXY = $dependencyProxy' in deploy
    assert '$env:HTTPS_PROXY = $dependencyProxy' in deploy
    assert '$env:NO_PROXY = "127.0.0.1,localhost,192.168.11.11,192.168.11.12"' in deploy
    assert "[System.Environment]::SetEnvironmentVariable(" in deploy
    assert "[System.EnvironmentVariableTarget]::Process" in deploy
    assert deploy.count("ASR_DEPENDENCY_PROXY") == 3


def test_running_asr_is_verified_and_stopped_before_application_swap():
    deploy = read("scripts/deploy-asr.ps1")
    swap = deploy.index('Move-Item -LiteralPath $appRoot -Destination $backup')
    hot_update = deploy.index("$serviceWasRunning = Stop-OwnedAsrService")
    task_guard = deploy.index("function Assert-TaskIsOurs")
    listener_guard = deploy.index("function Get-VerifiedAsrListenerIds")

    assert task_guard < hot_update < swap
    assert listener_guard < hot_update < swap
    assert '[string]$Task.Principal.UserId -ne "Administrator"' in deploy
    assert '[string]$Task.Principal.LogonType -ne "S4U"' in deploy
    assert '[string]$process.ExecutablePath -ne $basePython' in deploy
    assert '[string]$process.CommandLine -ne $expectedCommandLine' in deploy
    assert "Refusing to stop an unexpected process listening on TCP 8200" in deploy
    assert deploy.index("Assert-TaskIsOurs -Task $task", task_guard) < deploy.index(
        "Stop-ScheduledTask -TaskName $taskName", task_guard
    )
    assert deploy.index("$listenerIds = @(Get-VerifiedAsrListenerIds)") < deploy.index(
        "Stop-Process -Id $processId -Force"
    )
    assert "Get-Process | Stop-Process" not in deploy
    assert "Get-Process -Name" not in deploy


def test_hot_update_waits_for_release_and_verifies_restarted_service():
    deploy = read("scripts/deploy-asr.ps1")
    stop_call = deploy.index("$serviceWasRunning = Stop-OwnedAsrService")
    swap = deploy.index('Move-Item -LiteralPath $appRoot -Destination $backup')
    start_call = deploy.index("Register-AndStartAsrTask", swap)
    health_call = deploy.index("Wait-AsrHealthy", start_call)
    verifier_call = deploy.index(
        '& (Join-Path $scriptRoot "verify-asr-service.ps1")', health_call
    )

    assert "function Wait-AsrPortReleased" in deploy
    assert "TCP port 8200 remained listening" in deploy
    assert stop_call < swap < start_call < health_call < verifier_call
    assert 'health.api_version -eq "asr-service/1"' in deploy
    assert "ASR service did not become healthy within 10 minutes" in deploy


def test_hot_update_failure_preserves_original_error_and_restores_backup():
    deploy = read("scripts/deploy-asr.ps1")
    catch_block = deploy[deploy.index("} catch {", deploy.index("$backup = $null")) :]

    assert "$original = $_" in catch_block
    assert "if ($newAppInstalled" in catch_block
    assert "Unable to archive the failed ASR application" in catch_block
    assert "Move-Item -LiteralPath $backup -Destination $appRoot" in catch_block
    assert "Unable to restore the previous ASR application" in catch_block
    assert "$serviceWasRunning" in catch_block
    assert "Unable to restart the previous ASR service after rollback" in catch_block
    assert "throw $original" in catch_block
    assert catch_block.index("throw $original") > catch_block.index(
        "Unable to restart the previous ASR service after rollback"
    )
    assert "Remove-Item" not in deploy


def test_inactive_deploy_refuses_to_replace_a_running_service():
    deploy = read("scripts/deploy-asr.ps1")
    activation_branch = deploy.index("if ($ActivateService)", deploy.index("$backup = $null"))
    inactive_branch = deploy.index("Stop-OwnedAsrService -RequireStopped", activation_branch)
    swap = deploy.index('Move-Item -LiteralPath $appRoot -Destination $backup')

    assert activation_branch < inactive_branch < swap
    assert (
        "RAGPinCheng-ASR is running; deploy again with ActivateService=true "
        "for a verified hot update"
    ) in deploy


def test_faster_whisper_qualification_workflow_is_manual_immutable_and_gated():
    workflow = read(".github/workflows/qualify-faster-whisper-production.yml")
    build_job = workflow.split("  build-internal-wheel:", 1)[1].split(
        "\n  qualify:", 1
    )[0]
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "timeout-minutes: 180" in workflow
    assert workflow.count("default: false") == 2
    assert "execute_qualification must be explicitly enabled" in workflow
    assert "prepare_synthetic_samples:" in workflow
    assert "if: ${{ inputs.prepare_synthetic_samples }}" in workflow
    assert "prepare-faster-whisper-qualification-samples.ps1" in workflow
    assert "commit_sha must equal the workflow dispatch revision" in workflow
    assert "qualification must be dispatched from master" in workflow
    assert 'github.ref }}" -ne "refs/heads/master"' in workflow
    assert "secrets.ASR_DEPENDENCY_PROXY" in workflow
    assert "secrets.ASR_MODEL_DOWNLOAD_PROXY" in workflow
    assert "secrets.GPU_SERVICE_TOKEN" in workflow
    assert "secrets.ASR_SERVICE_TOKEN" not in workflow
    assert "activate_service" not in workflow.lower()
    assert "operation:" not in workflow
    assert "runs-on: ubuntu-latest" in build_job
    assert "timeout-minutes: 30" in build_job
    assert "scripts/build_internal_jieba_wheel.py build" in build_job
    assert "actions/upload-artifact@v4" in build_job
    assert "jieba-internal-wheel-${{ github.run_id }}" in build_job
    assert "secrets." not in build_job
    assert "production-asr" not in build_job
    assert "ASR_SERVICE_TOKEN" not in build_job
    assert "ASR_MODEL_DOWNLOAD_PROXY" not in build_job
    assert "GPU_SERVICE_TOKEN" not in build_job
    assert "actions/download-artifact@v4" in workflow
    assert "InternalWheelBundlePath" in workflow
    assert "needs: build-internal-wheel" in workflow
    assert "DependencyDiagnosticPath" in workflow
    assert workflow.count("dependency-diagnostic.json") == 3
    assert "Dependency stage:" in workflow
    assert "Dependency diagnosis:" in workflow
    assert "Affected requirement:" in workflow


def test_faster_whisper_synthetic_sample_preparation_is_fixed_and_gated():
    workflow = read(".github/workflows/qualify-faster-whisper-production.yml")
    script = read("scripts/prepare-faster-whisper-qualification-samples.ps1")
    template = json.loads(
        read("asr_service/faster-whisper-qualification-manifest.example.json")
    )
    lowered = script.lower()

    assert workflow.count("prepare_synthetic_samples:") == 1
    assert "sample_path:" not in workflow
    assert "sample_text:" not in workflow
    assert "voice_name:" not in workflow
    assert "model_id:" not in workflow
    assert "\n      revision:" not in workflow

    assert "System.Speech.Synthesis.SpeechSynthesizer" in script
    assert 'Culture.Name -eq "zh-CN"' in script
    assert "SpeechAudioFormatInfo" in script
    assert "16000" in script
    assert "AudioBitsPerSample]::Sixteen" in script
    assert "AudioChannel]::Mono" in script
    assert (
        r"D:\ServiceData\RAGPinCheng-ASR\qualification\faster-whisper"
        in script
    )
    assert (
        "Prepared fixed eight-sample non-sensitive Windows TTS qualification set"
        in script
    )
    assert "--validate-manifest-only" in script
    assert script.count("Invoke-ManifestValidation") >= 4
    assert script.count("Assert-FixedManifestIdentity") >= 4
    assert "faster-whisper-qualification-manifest.example.json" in script
    assert "Existing qualification input directory is not a valid fixed sample set" in script
    assert "Existing qualification input directory is non-empty" in script
    assert "empty-input-root-before-promotion" in script
    assert "Move-Item -LiteralPath $StagingRoot -Destination $InputRoot" in script
    assert "Add-DeterministicBackgroundNoise" in script
    assert 'New-Object "System.Collections.Generic.List[object]"' in script
    assert script.isascii()
    assert (
        "Get-Content -LiteralPath $template -Raw -Encoding UTF8 | ConvertFrom-Json"
        in script
    )

    expected_ids = (
        "bim-terms",
        "clear-zh",
        "mixed-zh-en",
        "negative-control-1",
        "negative-control-2",
        "negative-control-3",
        "noisy-bim-zh",
        "standard-codes",
    )
    assert tuple(sample["id"] for sample in template["samples"]) == expected_ids
    assert script.count("is_internal_recording = $false") == 1
    assert script.count("contains_customer_data = $false") == 1
    assert script.count("self_made = $true") == 1
    assert "Remove-Item" not in script
    assert "Start-ScheduledTask" not in script
    assert "Stop-ScheduledTask" not in script
    assert "Register-ScheduledTask" not in script
    assert "New-NetFirewallRule" not in script
    assert "Set-NetFirewallRule" not in script
    assert "Remove-NetFirewallRule" not in script
    assert "netsh advfirewall" not in lowered
    assert "ASR_ENABLED" not in script
    assert "prod.env" not in lowered
    assert "ASR_SERVICE_TOKEN" not in script
    assert "GPU_SERVICE_TOKEN" not in script
    assert "HTTP_PROXY" not in script
    assert "HTTPS_PROXY" not in script
    assert "Qdrant" not in script
    assert "app.sqlite" not in script


def test_faster_whisper_dependency_diagnosis_is_manual_fixed_and_sanitized():
    workflow = read(
        ".github/workflows/diagnose-faster-whisper-dependencies-production.yml"
    )
    script = read("scripts/diagnose-faster-whisper-dependencies.ps1")
    lowered = script.lower()

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "timeout-minutes: 30" in workflow
    assert workflow.count("default: false") == 1
    assert "execute_diagnosis must be explicitly enabled" in workflow
    assert "commit_sha must equal the workflow dispatch revision" in workflow
    assert "diagnosis must be dispatched from master" in workflow
    assert "secrets.ASR_DEPENDENCY_PROXY" in workflow
    assert "secrets.ASR_MODEL_DOWNLOAD_PROXY" not in workflow
    assert "secrets.GPU_SERVICE_TOKEN" not in workflow
    assert "secrets.ASR_SERVICE_TOKEN" not in workflow
    assert "source_run_id:" not in workflow
    assert "model_id:" not in workflow
    assert "revision:" not in workflow

    assert '$SourceRunId = "30955067671"' in script
    assert "production-freeze.txt" in script
    assert "pip-download.log" in script
    assert "qualification-requirements.txt" in script
    assert "Convert-ToSanitizedResolverEvidence" in script
    assert "binary_distribution_unavailable" in script
    assert "version_constraint_conflict" in script
    assert "No matching binary distribution found for" in script
    assert 'D:\\private\\python.exe : ERROR: No matching distribution found for jieba' in script
    assert "diagnosis_kind = $DiagnosisKind" in script
    assert "affected_requirement = $AffectedRequirement" in script
    assert "focused_probe_executed = $FocusedProbeExecuted" in script
    assert "focused_probe_exit_code = $FocusedProbeExitCode" in script
    assert "focused-binary-probe.log" in script
    assert "Focused binary-only requirement probe succeeded" in script
    assert "$evidence.ProbeConstraint" in script
    assert "exit 0" in script
    assert "Assert-SanitizerSelfTest" in script
    assert "Assert-ProductionFreeze" in script
    assert "Assert-FixedCombinedRequirements" in script
    assert "Source production freeze contains a non-registry constraint" in script
    assert "Source combined requirements do not match the fixed contract" in script
    assert "faster-whisper-r3-dependency-diagnostic/1" in script
    assert "--dry-run" in script
    assert "--ignore-installed" in script
    assert "--only-binary=:all:" in script
    assert "--no-cache-dir" in script
    assert "resolver-replay.log" in script
    assert "conflict_details_insufficient" in script
    assert "resolution_succeeded_unexpectedly" in script
    assert "production_services_modified = $false" in script
    assert 'profile_admission = "disabled"' in script
    assert script.isascii()

    assert "Remove-Item" not in script
    assert "Start-ScheduledTask" not in script
    assert "Stop-ScheduledTask" not in script
    assert "Register-ScheduledTask" not in script
    assert "Unregister-ScheduledTask" not in script
    assert "New-NetFirewallRule" not in script
    assert "Set-NetFirewallRule" not in script
    assert "Remove-NetFirewallRule" not in script
    assert "netsh advfirewall" not in lowered
    assert "Start-Process" not in script
    assert "ASR_ENABLED" not in script
    assert "prod.env" not in lowered
    assert "ASR_MODEL_DOWNLOAD_PROXY" not in script
    assert "GPU_SERVICE_TOKEN" not in script
    assert "ASR_SERVICE_TOKEN" not in script
    assert "Qdrant" not in script
    assert "app.sqlite" not in script


def test_faster_whisper_qualification_is_isolated_from_production_mutations():
    script = read("scripts/qualify-faster-whisper-production.ps1")
    lowered = script.lower()
    assert r"D:\Services\RAGPinCheng-ASR\qualification\faster-whisper" in script
    assert (
        r"D:\ServiceData\RAGPinCheng-ASR\qualification\faster-whisper\inputs"
        in script
    )
    assert "$TempPort = 18200" in script
    assert "127.0.0.1:$TempPort" in script
    assert "Get-ScheduledTask" in script
    assert "Get-NetFirewallRule" in script
    assert "Start-ScheduledTask" not in script
    assert "Stop-ScheduledTask" not in script
    assert "Register-ScheduledTask" not in script
    assert "Unregister-ScheduledTask" not in script
    assert "New-NetFirewallRule" not in script
    assert "Set-NetFirewallRule" not in script
    assert "Remove-NetFirewallRule" not in script
    assert "netsh advfirewall" not in lowered
    assert "Remove-Item" not in script
    assert "ASR_ENABLED" not in script
    assert "prod.env" not in lowered


def test_faster_whisper_qualification_uses_exact_process_and_proxy_boundaries():
    script = read("scripts/qualify-faster-whisper-production.ps1")
    assert 'git -c "safe.directory=$SafeDirectory" -C $ResolvedSource rev-parse HEAD' in script
    assert "function Stop-OwnedProcess" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Refusing to terminate a process that is not owned" in script
    assert "Get-Process | Stop-Process" not in script
    assert "Get-Process -Name" not in script
    assert "function Set-ScopedProxy" in script
    assert "function Clear-ScopedProxy" in script
    assert script.count("Set-ScopedProxy -Proxy $env:ASR_DEPENDENCY_PROXY") == 1
    assert script.count("Set-ScopedProxy -Proxy $env:ASR_MODEL_DOWNLOAD_PROXY") == 1
    assert script.count("Clear-ScopedProxy") >= 3
    assert '$env:NO_PROXY = "127.0.0.1,localhost,192.168.11.11,192.168.11.12"' in script
    assert "ASR_SERVICE_TOKEN: " not in script
    assert 'Write-Host "$TemporaryToken"' not in script
    assert 'Write-Output "$TemporaryToken"' not in script


def test_faster_whisper_qualification_freezes_dependencies_model_and_gates():
    script = read("scripts/qualify-faster-whisper-production.ps1")
    model = read("scripts/prepare_faster_whisper_model.py")
    runner = read("scripts/run_faster_whisper_qualification.py")
    assert "torch==2.7.0+cu128" in script
    assert "torchaudio==2.7.0+cu128" in script
    assert "requirements-windows.txt" in script
    assert "requirements-faster-whisper.txt" in script
    assert '$RequirementsSource = $ResolvedSource.Replace("\\", "/")' in script
    assert "-r $RequirementsSource/asr_service/requirements-windows.txt" in script
    assert "-r $RequirementsSource/asr_service/requirements-faster-whisper.txt" in script
    assert "-r $ResolvedSource\\asr_service\\" not in script
    assert "--only-binary=:all:" in script
    assert "InternalWheelBundlePath" in script
    assert "build_internal_jieba_wheel.py" in script
    assert '"validate"' in script
    assert '"--find-links", $ResolvedInternalWheelBundle' in script
    assert "internal://jieba/0.42.1/$wheelSha256" in script
    assert "internal_wheel_manifest_sha256" in script
    assert "Controlled internal wheel changed before wheelhouse recording" in script
    assert "Controlled internal wheel was not resolved into the wheelhouse" in script
    diagnostic_section = script.split(
        "function Get-NormalizedPackageName", 1
    )[1].split("function Write-SanitizedSummary", 1)[0]
    assert "function Convert-ToSanitizedDependencyFailure" in diagnostic_section
    assert "function Assert-DependencySanitizerSelfTest" in diagnostic_section
    assert "function Write-SanitizedDependencyFailure" in diagnostic_section
    assert "faster-whisper-r3-dependency-failure/1" in diagnostic_section
    assert "binary_distribution_unavailable" in diagnostic_section
    assert "version_constraint_conflict" in diagnostic_section
    assert "network_or_index_failure" in diagnostic_section
    assert "evidence_insufficient" in diagnostic_section
    assert "dependency_stage = [string]$diagnosis.Stage" in diagnostic_section
    assert "affected_requirement = [string]$diagnosis.Requirement" in diagnostic_section
    assert 'profile_admission = "disabled"' in diagnostic_section
    assert "production_services_modified = $false" in diagnostic_section
    assert "conflict_lines" not in diagnostic_section
    assert "log_path =" not in diagnostic_section.lower()
    assert "production_freeze_sha256" not in diagnostic_section
    assert "Write-SanitizedDependencyFailure" in script.split("} catch {", 1)[-1]
    for stage in (
        "production_freeze",
        "production_pip_check",
        "qualification_venv",
        "pip_download",
        "wheel_manifest",
        "pip_install",
        "qualification_pip_check",
        "qualification_freeze",
        "module_origin_verification",
        "license_audit",
    ):
        assert f'$DependencyFailureStage = "{stage}"' in script
    assert "--no-index" in script
    assert "pip\", \"check" in script
    assert "license-matrix.json" in script
    assert "qualification-module-origins.txt" in script
    assert "module escaped qualification venv" in script
    assert "e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31" in model
    assert "1617884929" in model.replace("_", "")
    assert "snapshot_download" in model
    assert "local_files_only=True" in read("asr_service/engines/faster_whisper.py")
    assert "CLEAR_CER_LIMIT = 0.10" in runner
    assert "BIM_NOISE_CER_LIMIT = 0.15" in runner
    assert "TERM_RECALL_LIMIT = 0.70" in runner
    assert "CODE_RECALL_LIMIT = 0.95" in runner
    assert "TIMESTAMP_P95_LIMIT_MS = 1_500" in runner
    assert "RTF_LIMIT = 0.60" in runner


def test_faster_whisper_qualification_drives_the_existing_result_flow():
    runner = read("scripts/run_faster_whisper_qualification.py")
    required = (
        "HttpxAsrServiceClient",
        "RemoteAsrProvider",
        "ProviderRuntimePorts",
        "execute_transcription",
        "CanonicalTranscript",
        "format_transcript",
        "_parse_transcript_turns",
    )
    assert all(name in runner for name in required)
    assert "WhisperModel" not in runner
    assert ".transcribe(" not in runner
    assert "FASTER_WHISPER_PROFILE_ID" in runner
