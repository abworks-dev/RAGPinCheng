from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_asr_from_ubuntu.py"
SPEC = importlib.util.spec_from_file_location("verify_asr_from_ubuntu", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def valid_env(token: str = "shared-token") -> str:
    return "\n".join(
        (
            "ASR_ENABLED=false",
            "ASR_SERVICE_URL=http://asr.example.invalid:8200",
            f"ASR_SERVICE_TOKEN={token}",
            "OTHER_KEY=allowed",
        )
    )


class FakeResponse:
    def __init__(self, payload: object, status: int = 200):
        self.status = status
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def fake_opener(request, *, timeout):
    assert timeout == 10.0
    if request.full_url.endswith("/health"):
        assert "Authorization" not in request.headers
        return FakeResponse({"status": "ok", "api_version": "asr-service/1"})
    assert request.full_url.endswith("/v1/capabilities")
    assert request.headers["Authorization"] == "Bearer shared-token"
    return FakeResponse(
        {
            "api_version": "asr-service/1",
            "service_profiles": ["funasr-sensevoice-small-v1"],
            "max_upload_part_bytes": 8388608,
            "max_input_bytes": 2147483648,
        }
    )


def test_ubuntu_verifier_keeps_backend_disabled_and_validates_cross_node(tmp_path: Path):
    env_file = tmp_path / "prod.env"
    env_file.write_text(valid_env(), encoding="utf-8")
    result = MODULE.verify(
        env_file,
        "http://asr.example.invalid:8200",
        "shared-token",
        opener=fake_opener,
    )
    assert result == {
        "status": "verified",
        "api_version": "asr-service/1",
        "service_profiles": ["funasr-sensevoice-small-v1"],
        "ubuntu_asr_enabled": False,
        "token_match": True,
    }


def test_ubuntu_verifier_accepts_exact_faster_whisper_then_sensevoice_contract(
    tmp_path: Path,
):
    def dual_profile_opener(request, *, timeout):
        if request.full_url.endswith("/health"):
            return FakeResponse({"status": "ok", "api_version": "asr-service/1"})
        return FakeResponse(
            {
                "api_version": "asr-service/1",
                "service_profiles": [
                    "faster-whisper-large-v3-turbo-v1",
                    "funasr-sensevoice-small-v1",
                ],
                "max_upload_part_bytes": 8388608,
                "max_input_bytes": 2147483648,
            }
        )

    env_file = tmp_path / "prod.env"
    env_file.write_text(valid_env(), encoding="utf-8")
    expected_profiles = (
        MODULE.FASTER_WHISPER_PROFILE,
        MODULE.SENSEVOICE_PROFILE,
    )
    result = MODULE.verify(
        env_file,
        "http://asr.example.invalid:8200",
        "shared-token",
        expected_profiles=expected_profiles,
        opener=dual_profile_opener,
    )
    assert result["service_profiles"] == list(expected_profiles)


@pytest.mark.parametrize(
    ("content", "message"),
    (
        (valid_env().replace("ASR_ENABLED=false", "ASR_ENABLED=true"), "must remain false"),
        (
            valid_env().replace("asr.example.invalid:8200", "127.0.0.1:8200"),
            "configured endpoint",
        ),
        (valid_env("wrong-token"), "does not match"),
    ),
)
def test_ubuntu_backend_boundary_fails_closed(content: str, message: str):
    values = MODULE.parse_required_env(content)
    with pytest.raises(RuntimeError, match=message):
        MODULE.validate_backend_boundary(
            values, "shared-token", "http://asr.example.invalid:8200"
        )


def test_ubuntu_prod_env_requires_each_client_key_exactly_once():
    with pytest.raises(RuntimeError, match="exactly once"):
        MODULE.parse_required_env(valid_env() + "\nASR_ENABLED=false\n")
    with pytest.raises(RuntimeError, match="exactly once"):
        MODULE.parse_required_env(
            "\n".join(
                line for line in valid_env().splitlines() if not line.startswith("ASR_SERVICE_TOKEN")
            )
        )


@pytest.mark.parametrize(
    "payload",
    (
        {"status": "disabled", "api_version": "asr-service/1"},
        {"status": "ok", "api_version": "asr-service/2"},
        {"status": "ok", "api_version": "asr-service/1", "extra": None},
    ),
)
def test_health_contract_rejects_disabled_wrong_version_and_unknown_fields(payload):
    with pytest.raises(RuntimeError):
        MODULE.validate_health(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "api_version": "asr-service/1",
            "service_profiles": [],
            "max_upload_part_bytes": 1,
            "max_input_bytes": 1,
        },
        {
            "api_version": "asr-service/1",
            "service_profiles": ["funasr-sensevoice-small-v1", "unexpected"],
            "max_upload_part_bytes": 1,
            "max_input_bytes": 1,
        },
        {
            "api_version": "asr-service/1",
            "service_profiles": ["funasr-sensevoice-small-v1"],
            "max_upload_part_bytes": True,
            "max_input_bytes": 1,
        },
    ),
)
def test_capabilities_contract_rejects_wrong_profiles_and_bool_limits(payload):
    with pytest.raises(RuntimeError):
        MODULE.validate_capabilities(payload, (MODULE.SENSEVOICE_PROFILE,))


@pytest.mark.parametrize(
    "profiles",
    (
        (MODULE.FASTER_WHISPER_PROFILE,),
        (MODULE.SENSEVOICE_PROFILE, MODULE.FASTER_WHISPER_PROFILE),
        ("unexpected",),
    ),
)
def test_capabilities_contract_rejects_unapproved_expected_profile_sets(profiles):
    payload = {
        "api_version": "asr-service/1",
        "service_profiles": list(profiles),
        "max_upload_part_bytes": 1,
        "max_input_bytes": 1,
    }
    with pytest.raises(RuntimeError, match="invalid expected"):
        MODULE.validate_capabilities(payload, profiles)


def test_activation_workflow_is_manual_safe_by_default_and_cross_node_gated():
    workflow = read(".github/workflows/activate-asr-production.yml")
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "default: preflight" in workflow
    assert "commit_sha must equal the workflow dispatch revision" in workflow
    assert '"${{ github.sha }}"' in workflow
    assert "environment: production-asr" in workflow
    assert "group: production-gpu-exclusive" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, ubuntu, production, app]" in workflow
    assert "needs.verify-ubuntu.result != 'success'" in workflow
    assert "Mode = \"Rollback\"" in workflow
    assert "ASR_SERVICE_TOKEN: ${{ secrets.ASR_SERVICE_TOKEN }}" in workflow
    assert "BGE_PRIORITY_PROBE_TOKEN: ${{ secrets.GPU_SERVICE_TOKEN }}" in workflow
    assert "ASR_DEPENDENCY_PROXY" in workflow
    assert "if: ${{ inputs.operation != 'rollback' }}" in workflow
    assert 'Join-Path $env:PRODUCTION_ASR_BACKUP_ROOT "$activationId\\activate-asr-production.ps1"' in workflow
    rollback_job = workflow.split("rollback-after-cross-node-failure:", 1)[1]
    assert "actions/checkout" not in rollback_job
    assert "ASR_ENABLED=true" not in workflow
    assert "deploy-app.sh" not in workflow
    assert "docker compose" not in workflow


def test_activation_script_uses_fixed_firewall_and_fail_closed_rollback():
    script = read("scripts/activate-asr-production.ps1")
    lowered = script.lower()
    assert '$allowedRemoteAddress = $env:PRODUCTION_APP_NODE_IP' in script
    assert '$firewallRuleName = "RAGPinCheng-ASR-8200-from-Ubuntu"' in script
    assert "-LocalPort 8200" in script
    assert "-RemoteAddress $allowedRemoteAddress" in script
    assert "Get-EnabledInboundAllowRulesForAsr8200" in script
    assert "Test-FirewallRuleAppliesToAsrProcess" in script
    assert "Get-NetFirewallApplicationFilter" in script
    assert "Get-NetFirewallServiceFilter" in script
    assert '$program -ne $venvPython' in script
    assert '$package -notin @("Any", "*")' in script
    assert '$service -notin @("Any", "*")' in script
    assert "An enabled inbound Allow rule already covers TCP 8200" in script
    assert "foreach ($entry in @($LocalPort))" in script
    assert "ASR_SERVICE_ENABLED=true" in script
    assert "ASR_SERVICE_ENABLED=false" in script
    assert '$env:GPU_SERVICE_ACTIVITY_URL' in script
    assert '"http://127.0.0.1:8100/v1/activity"' not in script
    assert "-AllowInjectedProbeToken" in script
    assert "Injected BGE priority probe token must be one line" in script
    assert "Invoke-ActivationRollback" in script
    assert "Copy-Item -LiteralPath $PSCommandPath -Destination $rollbackScriptPath" in script
    assert '$state.activation_id -ne $ActivationId' in script
    assert "$state.commit_sha -ne $CommitSha" not in script
    assert "Stop-ScheduledTask" in script
    assert "Unregister-ScheduledTask" in script
    assert "Remove-NetFirewallRule -Name $firewallRuleName" in script
    assert "function Stop-VerifiedAsrListeners" in script
    assert "sys._base_executable" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "ForEach-Object { $_.OwningProcess }" in script
    assert "$connections.OwningProcess" not in script
    assert (
        '\'"{0}" -m uvicorn asr_service.app:create_app --factory '
        "--host 0.0.0.0 --port 8200' -f"
    ) in script
    assert "Refusing to stop an unexpected process listening on TCP 8200" in script
    assert "Stop-Process -Id $processId -Force" in script
    assert script.index("Remove-NetFirewallRule -Name $firewallRuleName") < script.index(
        "Stop-VerifiedAsrListeners", script.index("function Invoke-ActivationRollback")
    )
    assert "model_cache_available=" in script
    assert "Register-ScheduledTask" in script
    assert "Start-ScheduledTask" in script
    assert "[string]$actions[0].Arguments -ne $expectedTaskArguments" in script
    assert "snapshot_download" not in lowered
    assert "pip install" not in lowered
    assert "/v1/jobs" not in lowered
    assert "qdrant" not in lowered
    assert "sqlite" not in lowered
    assert "remove-item" not in lowered
    assert "local-subnet" not in lowered
    assert "0.0.0.0/0" not in lowered
    assert 'Write-Warning "Automatic activation rollback failed:' in script


def test_local_verifier_has_unique_enabled_profile_and_gpu_assertions():
    script = read("scripts/verify-asr-service.ps1")
    assert 'ASR_SERVICE_ENABLED"] -ne "true"' in script
    assert '$health.status -ne "ok"' in script
    assert '"funasr-sensevoice-small-v1"' in script
    assert '"faster-whisper-large-v3-turbo-v1"' in script
    assert '($profiles -join "`n") -ne ($ExpectedProfiles -join "`n")' in script
    assert "ASR_FASTER_WHISPER_MODEL_CACHE_ROOT" in script
    assert "ASR_FASTER_WHISPER_MODEL_MANIFEST_PATH" in script
    assert (
        'Invoke-RestMethod -Method Get -Uri "$AsrUrl/v1/capabilities" '
        "-Headers $asrHeaders -TimeoutSec 120"
    ) in script
    assert "Assert-ExactPropertyNames" in script
    assert '$activity.api_version -ne $expectedGpuVersion' in script
    assert "-not $activity.model_loaded" in script
    assert "$activity.inflight_requests -lt 0" in script
    assert "$activity.asr_chunk_allowed -isnot [bool]" in script


def test_ubuntu_verifier_never_outputs_or_serializes_token():
    script = read("scripts/verify_asr_from_ubuntu.py")
    assert '"token_match": True' in script
    result_block = script.split('return {\n        "status": "verified"', 1)[1].split(
        "\n    }", 1
    )[0]
    assert "configured_token" not in result_block
    assert "expected_token" not in result_block
    assert "print(expected_token" not in script
    assert "print(configured_token" not in script


def test_candidate_promotion_is_manual_identity_bound_and_cross_node_gated():
    workflow = read(".github/workflows/promote-asr-candidate-production.yml")

    assert "workflow_dispatch:" in workflow
    assert "default: preflight" in workflow
    assert "candidate_manifest_sha256" in workflow
    assert "candidate_id" in workflow
    assert "group: production-gpu-exclusive" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, ubuntu, production, app]" in workflow
    assert "needs.verify-ubuntu.result != 'success'" in workflow
    assert "Roll back failed candidate promotion" in workflow
    assert "--expected-profile faster-whisper-large-v3-turbo-v1" in workflow
    assert "--expected-profile funasr-sensevoice-small-v1" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow


def test_candidate_promotion_preserves_legacy_release_and_rolls_back_fail_closed():
    script = read("scripts/promote-asr-candidate.ps1")
    lowered = script.lower()

    assert 'ValidateSet("Preflight", "Promote", "Rollback")' in script
    assert "Read-AsrReleaseManifest" in script
    assert "ASR candidate Python environment identity mismatch" in script
    assert "Assert-TaskDefinition" in script
    assert "Refusing to modify an unexpected RAGPinCheng-ASR" in script
    assert "Refusing to stop an unexpected process listening on TCP 8200" in script
    assert "candidate-asr.env.before" in script
    assert "active.json.before" in script
    assert "previous_task_arguments" in script
    assert "$legacyRootlessTaskArguments" in script
    assert '-ProgramRoot "{1}" -DataRoot "{2}"\' -f' in script
    assert "@($legacyRootlessTaskArguments, $legacyTaskArguments)" in script
    assert "[string]$actions[0].Arguments -notin $ExpectedArguments" in script
    assert "previous_candidate_id" in script
    assert 'Join-Path $ProgramRoot "bootstrap"' in script
    assert '"-UseActiveRelease"' not in script
    assert "-UseActiveRelease' -f" in script
    assert "previous_bootstrap_script_present" in script
    assert "previous_bootstrap_helper_present" in script
    assert "unexpected active release transition" in script
    assert "Candidate rollback backup set is incomplete" in script
    assert "previous active release identity mismatch" in script
    assert "previous Scheduled Task identity mismatch" in script
    assert "unexpected active release manifest" in script
    assert "previous active release state missing" in script
    assert "[string]$state.commit_sha -ne $CommitSha.ToLowerInvariant()" in script
    assert "Read-AsrReleaseManifest" in script.split("function Invoke-CandidateRollback", 1)[1]
    assert "Copy-Item -LiteralPath $candidateConfigBackup" in script
    assert "Register-AndStartTask -Arguments ([string]$state.previous_task_arguments)" in script
    assert "Automatic candidate rollback failed" in script
    assert "Assert-AtomicFileReplaceSupported" in script
    assert "Write-AtomicTextWithBackup" in script
    assert "candidate-asr.env.atomic-before" in script
    assert "[IO.File]::Replace($temporary, $Path, $BackupPath, $true)" in script
    assert "[IO.File]::Replace($temporary, $Path, $null" not in script
    assert script.index("Stop-OwnedService -Context $previous") < script.index(
        "Write-AsrJsonAtomic -Path $activeStatePath"
    )
    assert script.index("Write-AsrJsonAtomic -Path $activeStatePath") < script.index(
        "Register-AndStartTask -Arguments $candidateArguments"
    )
    task_action = next(
        line for line in script.splitlines() if "New-ScheduledTaskAction" in line
    )
    assert "TOKEN" not in task_action.upper()
    assert "remove-item" not in lowered
    assert "snapshot_download" not in lowered
    assert "pip install" not in lowered


def test_legacy_asr_recovery_is_manual_sha_bound_owned_and_rollback_safe():
    workflow = read(".github/workflows/recover-asr-legacy-production.yml")
    script = read("scripts/recover-asr-legacy-production.ps1")
    lowered = (workflow + script).lower()

    assert "workflow_dispatch:" in workflow
    assert "default: false" in workflow
    assert "commit_sha must equal the dispatch revision" in workflow
    assert '"refs/heads/master"' in workflow
    assert "group: production-gpu-exclusive" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "environment: production-asr" in workflow
    assert "ASR_SERVICE_TOKEN" not in workflow
    assert "GPU_SERVICE_TOKEN" not in workflow
    assert "asr-legacy-recovery-${{ github.run_id }}" in workflow

    assert '"asr-legacy-recovery/1"' in script
    assert "Legacy ASR recovery requires no active candidate release" in script
    assert "$legacyRootlessTaskArguments" in script
    assert "$legacyTaskArguments" in script
    assert '"legacy-explicit-roots"' in script
    assert "Refusing to recover an unexpected RAGPinCheng-ASR" in script
    assert "Refusing to modify an unexpected process listening on TCP 8200" in script
    assert "sys._base_executable" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Register-ScheduledTask" in script
    assert "Start-ScheduledTask" in script
    assert "Stop-ScheduledTask" in script
    assert "Stop-Process -Id $processId -Force" in script
    assert "Wait-LegacyAsrHealthy" in script
    assert 'ExpectedProfiles @("funasr-sensevoice-small-v1")' in script
    assert "original_task_action_kind" in script
    assert "original_task_state" in script
    assert 'Write-RecoveryState -Status "rolled-back"' in script
    assert "Automatic legacy ASR recovery rollback failed" in script
    assert "ConvertTo-Json" in script
    assert "ASR_SERVICE_TOKEN" not in script.split("$state =", 1)[1]
    assert "BGE_PRIORITY_PROBE_TOKEN" not in script.split("$state =", 1)[1]
    for forbidden in (
        "active.json -destination",
        "new-netfirewallrule",
        "remove-netfirewallrule",
        "pip install",
        "snapshot_download",
        "unregister-scheduledtask",
    ):
        assert forbidden not in lowered


def test_asr_startup_diagnostic_is_manual_read_only_and_sanitized():
    workflow = read(".github/workflows/diagnose-asr-service-production.yml")
    script = read("scripts/diagnose-asr-service-production.ps1")
    lowered = (workflow + script).lower()

    assert "workflow_dispatch:" in workflow
    assert "default: false" in workflow
    assert "commit_sha must equal the dispatch revision" in workflow
    assert "group: production-gpu-exclusive" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "asr-startup-diagnostic-${{ github.run_id }}" in workflow
    assert "asr-production-startup-diagnostic/1" in script
    assert "Get-ScheduledTaskInfo" in script
    assert "Get-NetTCPConnection -LocalPort 8200 -State Listen" in script
    assert '"http://127.0.0.1:8200/health"' in script
    assert "ConvertTo-SafeDiagnosticLine" in script
    assert "sanitized_log_lines" in script
    assert "task_root_binding_available" in script
    assert "machine_program_root_present" in script
    assert "startup_preflight_status" in script
    assert "config_required_missing_count" in script
    assert "startup_log_updated_after_last_run" in script
    assert "task_root_binding_missing" in script
    assert "production_services_modified = $false" in script
    assert "ASR_SERVICE_TOKEN" not in workflow
    assert "GPU_SERVICE_TOKEN" not in workflow
    for forbidden in (
        "stop-scheduledtask",
        "start-scheduledtask",
        "register-scheduledtask",
        "unregister-scheduledtask",
        "stop-process",
        "remove-item",
        "move-item",
        "copy-item",
        "set-content",
        "new-netfirewallrule",
        "remove-netfirewallrule",
    ):
        assert forbidden not in lowered
