from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_lock_is_validated_only_by_matching_cuda_qualification():
    metadata = json.loads(read("services/gpu_service/runtime-lock.json"))
    requirements = read("services/gpu_service/runtime-lock.txt")
    assert metadata["schema_version"] == 1
    assert metadata["validation_status"] == "validated"
    assert metadata["qualification_run_id"] == "31271874609"
    assert metadata["source_commit"] == "3ff8c076c4637ab156281dbab7f6b3feac966685"
    assert metadata["qualified_source_fingerprint"] == (
        "9b147c448b9b22d15e41f8eae7409c5417c291fec0f8f3d67b47ad6a8bab2e79"
    )
    assert metadata["qualified_lock_sha256"] == (
        "fa16678de682e389e0f5ca89b180b2c033404e5e077ff539b552f8cde0430f1a"
    )
    assert metadata["torch_wheel_sha256"] == (
        "c52c4b869742f00b12cb34521d1381be6119fa46244791704b00cc4a3cb06850"
    )
    lock_lines = [
        line for line in requirements.splitlines() if line and not line.startswith("#")
    ]
    assert lock_lines
    assert all(line.count("==") == 1 for line in lock_lines)
    pins = dict(line.split("==", 1) for line in lock_lines)
    assert pins["torch"] == "2.7.0+cu128"
    assert pins["FlagEmbedding"] == "1.4.0"
    transformers_version = tuple(
        int(part) for part in pins["transformers"].split(".")[:2]
    )
    tokenizers_version = tuple(int(part) for part in pins["tokenizers"].split(".")[:2])
    assert (4, 56) <= transformers_version < (5, 0)
    assert (0, 22) <= tokenizers_version < (0, 23)
    for rejected in (
        "transformers==4.46.3",
        "tokenizers==0.20.3",
        "transformers==4.55.4",
        "tokenizers==0.21.4",
    ):
        assert rejected not in lock_lines


def test_known_bad_two_package_pin_is_not_a_production_contract():
    gpu_requirements = read("services/gpu_service/requirements.txt")
    root_requirements = read("requirements-gpu.txt")
    for source in (gpu_requirements, root_requirements):
        assert "transformers==4.46.3" not in source
        assert "tokenizers==0.20.3" not in source
        # Both files must carry the same bounds; a <0.22 tokenizers ceiling
        # silently caps transformers at 4.55.4, which cannot load BGE-M3 under
        # FlagEmbedding 1.4.0.
        assert "transformers>=4.56,<5" in source
        assert "tokenizers>=0.22,<0.23" in source
    assert "runtime-lock" in gpu_requirements
    assert "runtime-lock" in root_requirements


def test_candidate_resolver_is_manual_d_drive_isolated_and_evidence_only():
    workflow = read(".github/workflows/resolve-gpu-runtime-candidate.yml")
    script = read("scripts/resolve-gpu-runtime.ps1")
    lowered = script.lower()

    assert "workflow_dispatch:" in workflow
    assert "confirm_resolution:" in workflow
    assert "suspend_production_service:" in workflow
    assert "default: false" in workflow
    assert "production-gpu-exclusive" in workflow
    assert "runs-on: [self-hosted, windows, production, gpu]" in workflow
    assert "timeout-minutes: 60" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "runtime-lock-candidate.txt" in workflow
    assert "resolver-report.json" in workflow
    assert "preflight.json" in workflow
    assert "qualify-gpu-runtime.ps1" not in workflow
    assert "promote-gpu-runtime.ps1" not in workflow
    assert "resolve-gpu-model-cache-source.ps1" in workflow
    assert "TORCH_WHEEL_SEED_ROOT" in workflow
    assert "PRODUCTION_GPU_PYTHON_PATH" in workflow
    assert "-BasePython $env:GPU_BASE_PYTHON" in workflow
    assert "resolve-gpu-runtime-maintenance.ps1" in workflow
    assert "GPU_SERVICE_TOKEN" in workflow
    maintenance = read("scripts/resolve-gpu-runtime-maintenance.ps1")
    assert "current-release.json" in maintenance
    assert "Refusing to stop an unexpected process listening on TCP 8100" in maintenance
    assert "GPU_RESOLVER_OWNER pid=" in maintenance
    assert "[string]::Equals([string]$process.ExecutablePath, $BasePython" in maintenance
    assert "finally" in maintenance
    assert "promote-gpu-runtime.ps1" in maintenance
    assert "GPU_RESOLVER_MAINTENANCE status=restored" in maintenance
    legacy = read("scripts/restore-gpu-legacy-release.ps1")
    assert "gpu_service\\app.py" in legacy
    assert "GPU_LEGACY_RESTORE status=healthy" in legacy
    assert "start-gpu-legacy-service.ps1" in legacy
    assert "Stop-ScheduledTask -TaskName $taskName" in legacy
    assert "Unregister-ScheduledTask -TaskName $taskName -Confirm:$false" in legacy
    assert "Existing GPU scheduled task did not stop within 30 seconds" in legacy
    assert "Get-NetTCPConnection -LocalPort 8100 -State Listen" in legacy
    assert "TCP 8100 is still occupied after stopping the existing GPU scheduled task" in legacy
    register_line = next(
        line for line in legacy.splitlines() if line.startswith("Register-ScheduledTask ")
    )
    assert "-TaskName $taskName" in register_line
    assert "-Force" not in register_line

    assert 'StartsWith("D:\\"' in script
    assert '"-m", "venv"' in script
    assert "--system-site-packages" not in script
    assert '"torch==2.7.0+cu128"' in script
    assert '"FlagEmbedding==1.4.0"' in script
    assert '"transformers>=4.56,<5"' in script
    assert '"tokenizers>=0.22,<0.23"' in script
    # The resolver must reject a drifting FlagEmbedding even if the constraint
    # is ever loosened, and must refuse the proven-broken pairs outright.
    assert 'not the approved exact candidate 1.4.0' in script
    assert '$packageMap["transformers"] -eq "4.55.4"' in script
    assert '$packageMap["tokenizers"] -eq "0.21.4"' in script
    assert '@("-m", "pip", "check")' in script
    assert "pip freeze" in script
    assert "HF_HUB_OFFLINE" in script
    assert "TRANSFORMERS_OFFLINE" in script
    assert "GPU_RUNTIME_RESOLVER stage=install_verified_cuda_torch_wheel" in script
    assert '"--no-index", "--no-deps", [string]$torchSeed.path' in script
    assert '"--index-url", $approvedPackageIndex' in script
    assert "get-gpu-torch-wheel-seed.ps1" in script
    assert "manual_verified_wheel" in script
    assert "torch_wheel_sha256" in script
    assert "--trusted-host" not in script
    assert "PIP_CERT" not in script
    assert "function Write-SanitizedLogTail" in script
    assert "Get-Content -LiteralPath $Path -Tail 120" in script
    assert "[REDACTED]" in script
    assert "[TRUNCATED]" in script
    assert 'Write-Host "GPU_RUNTIME_RESOLVER_DIAGNOSTIC $safeLine"' in script
    assert "Get-NetTCPConnection -LocalPort 8100 -State Listen" in script
    assert 'Get-ScheduledTask -TaskName "RAGPinCheng-GPU"' in script
    assert "Stop-ScheduledTask" not in script
    assert "Register-ScheduledTask" not in script
    assert "Unregister-ScheduledTask" not in script
    assert "Remove-Item" not in script
    assert "new-netfirewallrule" not in lowered
    assert "BGEM3FlagModel" not in script
    assert "FlagReranker" not in script


def test_manual_torch_wheel_seed_is_fail_closed_and_has_no_network_behavior():
    verifier = read("scripts/get-gpu-torch-wheel-seed.ps1")
    manifest = read("scripts/new-gpu-torch-wheel-seed-manifest.ps1")
    assert "runtime/" in read(".gitignore").splitlines()
    for script in (verifier, manifest):
        assert "torch-2.7.0+cu128-cp310-cp310-win_amd64.whl" in script
        assert "https://download.pytorch.org/whl/cu128" in script
        assert "c52c4b869742f00b12cb34521d1381be6119fa46244791704b00cc4a3cb06850" in script
        assert "Get-FileHash" in script
        assert "ReparsePoint" in script
    assert "Invoke-WebRequest" not in manifest
    assert "pip install" not in manifest
    assert "--trusted-host" not in verifier


def test_gpu_model_cache_source_discovery_is_bounded_and_offline_only():
    script = read("scripts/resolve-gpu-model-cache-source.ps1")
    lowered = script.lower()

    assert "models--BAAI--bge-m3" in script
    assert "models--BAAI--bge-reranker-v2-m3" in script
    assert r'Join-Path $RepositoryPath "services\gpu_service\.cache\huggingface"' in script
    assert 'Get-ChildItem -LiteralPath "C:\\Users" -Directory' in script
    assert "does not contain both required offline model snapshots" in script
    assert "auto-discovery found no complete offline cache" in script
    assert "auto-discovery is ambiguous" in script
    assert "snapshot_download" not in script
    assert "invoke-webrequest" not in lowered
    assert "invoke-restmethod" not in lowered
    assert "pip install" not in lowered
    assert "Get-Content" not in script
    assert ".env" not in script
    assert "Remove-Item" not in script


def test_gpu_model_cache_repair_workflow_is_bounded_and_immutable():
    workflow = read(".github/workflows/repair-gpu-model-cache-production.yml")
    script = read("scripts/repair-gpu-model-cache.ps1")

    assert "production-gpu-exclusive" in workflow
    assert '"refs/heads/master"' in workflow
    assert "confirm_maintenance" in workflow
    assert "model-cache-repair\\${{ github.run_id }}" in workflow
    assert "Repair target already exists" in script
    assert "Repair source is outside approved roots" in script
    assert "Model cache path contains a reparse point" in script
    assert "Get-FileHash" in script
    assert "Remove-Item -LiteralPath $target -Recurse -Force" in script


def test_builder_is_d_drive_isolated_exact_and_records_artifacts():
    script = read("scripts/build-gpu-runtime.ps1")
    lock_hash = read("scripts/get-gpu-runtime-lock-hash.ps1")
    assert 'validation_status -notin @("candidate", "validated")' in script
    assert "GPU runtime lock is not eligible for candidate construction" in script
    assert "RequalifyValidated" in script
    assert "mode=requalify-validated" in script
    assert 'StartsWith("D:\\\\"' not in script
    assert 'StartsWith("D:\\"' in script
    assert "-m venv" in script
    assert "--system-site-packages" not in script
    assert "name==version" in script
    assert "pip wheel" in script
    assert script.count("--no-deps") >= 2
    assert "--no-index --find-links" in script
    assert "wheelhouse.sha256.json" in script
    assert "pip check" in script
    assert "pip-freeze.txt" in script
    assert "snapshot_gpu_runtime.py" in script
    assert "robocopy.exe" in script
    assert "source-files.sha256.json" in script
    assert "ConvertTo-Json -InputObject @($sourceInventory)" in script
    assert '"source"' in script
    assert "working tree contract does not match" in script
    assert "Validated metadata has no managed qualification root to import" in script
    assert "-not $RequalifyValidated" in script
    assert "requires exactly one matching qualified release" in script
    assert "Managed qualification release does not match validated metadata" in script
    assert "Copy-Item -LiteralPath $entry.FullName" in script
    assert "$needsQualificationImport" in script
    assert "$expectedReleasePaths.runtime_python" in script
    assert "$expectedReleasePaths.model_cache" in script
    assert "$expectedReleasePaths.source_root" in script
    assert "qualified_precisions" in script
    assert "GPU runtime package index is not approved" in script
    assert "GPU runtime torch index is not approved" in script
    assert "TorchWheelSeedRoot" in script
    assert "runtime-lock-without-torch.txt" in script
    assert "torch_wheel_sha256" in script
    assert "--extra-index-url" not in script
    assert '.Replace("`r`n", "`n").Replace("`r", "`n")' in lock_hash


def test_candidate_qualification_is_cuda_only_and_cleans_tasks():
    workflow = read(".github/workflows/repair-gpu-reranker-production.yml")
    script = read("scripts/qualify-gpu-runtime.ps1")
    probe = read("scripts/diagnose_gpu_reranker.py")
    assert "workflow_dispatch:" in workflow
    assert "confirm_qualification:" in workflow
    assert "suspend_production_service:" in workflow
    assert "production-gpu-exclusive" in workflow
    assert "build-gpu-runtime.ps1" in workflow
    assert "-RequalifyValidated" in workflow
    assert "qualify-gpu-runtime.ps1" in workflow
    assert "promote-gpu-runtime.ps1" in workflow
    assert "Stop-ScheduledTask" in workflow
    assert "GPU_PRODUCTION_SUSPEND status=stopped" in workflow
    assert "GPU_PRODUCTION_RESTORE status=complete" in workflow
    assert "GPU_SERVICE_TOKEN" in workflow
    assert "resolve-gpu-model-cache-source.ps1" in workflow
    assert "TORCH_WHEEL_SEED_ROOT" in workflow
    assert "TorchWheelSeedRoot" in workflow
    assert "RUNTIME_ROOT: ${{ vars.PRODUCTION_RUNTIME_ROOT }}\\qualification\\${{ github.run_id }}" in workflow
    assert "if: ${{ always() }}" in workflow
    assert 'GPU_CANDIDATE_RELEASE_ROOT=$releaseRoot' in workflow
    assert "$env:HTTP_PROXY = $env:DEPLOY_HTTP_PROXY" in workflow
    assert "--trusted-host" not in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "qualification.json" in workflow
    assert "runtime-manifest.json" in workflow
    assert "source-files.sha256.json" in workflow
    assert "wheelhouse.sha256.json" in workflow
    # Failure diagnostics must leave the host: without these, the actual Python
    # traceback is only readable by logging into the production machine.
    for precision in ("fp16", "fp32"):
        for log in ("stages.log", "stdout.log", "stderr.log"):
            assert f"qualification\\{precision}\\{log}" in workflow
    assert "pip install" not in workflow
    assert "--system-site-packages" not in workflow
    assert '@("fp16", "fp32")' in script
    assert 'device = "cuda"' in script
    assert "cpu" not in script.lower()
    assert "New-ScheduledTaskPrincipal" in script
    assert '-LogonType S4U' in script
    assert "Unregister-ScheduledTask" in script
    assert "ReadToEndAsync" in script
    assert "QualificationRunId" in script
    assert "torch_wheel_sha256" in script
    assert "source_inventory_sha256" in script
    assert "ConvertFrom-Json -InputObject (" in script
    assert "expected_length=" in script
    assert "actual_sha256=" in script
    assert "repository_commit" in script
    assert "Set-Location -LiteralPath $SourceRoot" in script
    assert 'choices=("fp16", "fp32")' in probe
    assert "torch.cuda.is_available()" in probe
    assert "embed_inference_complete" in probe
    assert "reranker_inference_complete" in probe


def test_qualification_requires_every_approved_precision_to_complete():
    """Stopping at the first working precision would ship a release where the
    other approved precision was never executed.  Both must reach stage=complete,
    and both results must be recorded.  `reranker_precision` must stay a scalar:
    promote-gpu-runtime.ps1 and start-gpu-service.ps1 consume it as one value
    (start derives RERANKER_USE_FP16 from `-eq "fp16"`), so an array there would
    silently corrupt the production precision."""
    script = read("scripts/qualify-gpu-runtime.ps1")
    promote = read("scripts/promote-gpu-runtime.ps1")
    start = read("scripts/start-gpu-service.ps1")

    # The first-success short circuit must be gone.
    assert "$selectedPrecision = $precision" not in script
    assert "qualified_precisions" in script
    assert "requested_precisions" in script
    assert "$qualifiedPrecisions += $precision" in script
    assert "requires every approved CUDA reranker precision to complete" in script

    # Per-precision evidence is still recorded for both attempts.
    assert "precision = $precision" in script
    assert "exit_code = $exitCode" in script
    assert "completed = [bool]$completed" in script

    # Downstream consumers still read a single scalar precision.
    assert 'reranker_precision = $selectedPrecision' in script
    assert '$qualification.reranker_precision -notin @("fp16", "fp32")' in promote
    assert '$qualifiedPrecisions[0] -ne "fp16"' in promote
    assert '$qualifiedPrecisions[1] -ne "fp32"' in promote
    assert '$qualification.reranker_precision -notin @("fp16", "fp32")' in start
    assert '$manifest.reranker_precision -eq "fp16"' in start


def test_gpu_deploy_script_is_fingerprint_aware():
    deploy = read("scripts/deploy-gpu.ps1")
    assert "get-gpu-runtime-fingerprint.ps1" in deploy
    assert "status=unchanged health=ok" in deploy
    assert "runtime_source_fingerprint" in deploy
    assert "runtime_lock_sha256" in deploy
    assert "does not match the unchanged runtime identity" in deploy
    assert "current GPU release manifest does not match its pointer" in deploy
    assert 'validation_status -ne "validated"' in deploy
    assert "qualification_run_id" in deploy
    assert "qualified_source_fingerprint" in deploy
    assert "qualified_lock_sha256" in deploy
    assert "torch_wheel_sha256" in deploy
    assert "merge-base --is-ancestor" in deploy
    assert "get-gpu-runtime-lock-hash.ps1" in deploy


def test_runtime_snapshot_contains_service_namespace_and_legacy_shim():
    build = read("scripts/build-gpu-runtime.ps1")
    for path in (
        "services/__init__.py",
        "services/gpu_service/__init__.py",
        "services/gpu_service/app.py",
        "gpu_service/__init__.py",
    ):
        assert f'"{path}"' in build


def test_declared_reranker_precisions_are_validated_not_widenable():
    """allowed_reranker_precisions must not be a decorative field: the builder
    validates it against the hardcoded CUDA-only set, so lock metadata can never
    widen the precision whitelist (e.g. by adding a CPU or int8 mode)."""
    metadata = json.loads(read("services/gpu_service/runtime-lock.json"))
    build = read("scripts/build-gpu-runtime.ps1")
    assert metadata["allowed_reranker_precisions"] == ["fp16", "fp32"]
    assert "allowed_reranker_precisions" in build
    assert "approved CUDA reranker precisions" in build


def test_runtime_scripts_signal_success_without_leaking_native_exit_codes():
    """build-gpu-runtime.ps1 copies the model cache with robocopy, which returns 1
    on a normal successful copy.  Because the builder and promoter report failure
    by throwing, a caller that inspected the inherited $LASTEXITCODE would treat a
    fully successful build as failed and silently skip promotion.  Both scripts
    must therefore end in an explicit `exit 0`, and the caller must reset
    $LASTEXITCODE before every invocation."""
    build = read("scripts/build-gpu-runtime.ps1")
    promote = read("scripts/promote-gpu-runtime.ps1")
    deploy = read("scripts/deploy-gpu.ps1")

    # robocopy's success-with-copies exit code must stay tolerated, and must not
    # be able to escape the builder as an apparent failure.
    assert "robocopy.exe" in build
    assert "-gt 7" in build
    assert build.rstrip().endswith("exit 0")
    assert "return\n    }" not in build
    assert promote.rstrip().endswith("exit 0")
    assert 'throw "GPU runtime promotion did not complete"' in promote

    # The caller must not gate on a stale exit code from an earlier command.
    assert "function Invoke-RuntimeScript" in deploy
    assert deploy.count("$global:LASTEXITCODE = 0") >= 2
    assert "Invoke-RuntimeScript -Failure \"GPU runtime construction failed\"" in deploy
    assert "Invoke-RuntimeScript -Failure \"GPU runtime promotion failed\"" in deploy
    # The old fragile pattern (bare call then LASTEXITCODE check) must be gone.
    assert 'if ($LASTEXITCODE -ne 0) { throw "GPU runtime construction failed" }' not in deploy
    assert 'if ($LASTEXITCODE -ne 0) { throw "GPU runtime promotion failed" }' not in deploy


def test_promotion_only_consumes_prequalified_immutable_release():
    promote = read("scripts/promote-gpu-runtime.ps1")
    start = read("scripts/start-gpu-service.ps1")
    fingerprint = read("scripts/get-gpu-runtime-fingerprint.ps1")
    assert "qualify-gpu-runtime.ps1" not in promote
    assert "source-files.sha256.json" in promote
    assert "source-files.sha256.json" in start
    assert "GPU release dependency lock failed integrity validation" in promote
    assert "GPU release dependency lock failed integrity validation" in start
    assert "torch_wheel_sha256" in promote
    assert "torch_wheel_sha256" in start
    assert "Set-Location -LiteralPath $sourceRoot" in start
    assert "current repository" not in start
    assert "scripts/get-gpu-runtime-lock-hash.ps1" in fingerprint
    assert "GPU model-info does not identify the promoted CUDA release" in promote
    assert promote.count("-TimeoutSec 30") >= 2


def test_recovery_only_repromotes_the_recorded_validated_release():
    workflow = read(".github/workflows/recover-gpu-service-production.yml")
    assert "workflow_dispatch:" in workflow
    assert "confirm_recovery:" in workflow
    assert "current-release.json" in workflow
    assert "git merge --ff-only $env:DEPLOY_COMMIT_SHA" in workflow
    assert "promote-gpu-runtime.ps1" in workflow
    assert "build-gpu-runtime.ps1" not in workflow
    assert "pip install" not in workflow


def test_gpu_service_forbids_cpu_fallback_and_has_precision_controls():
    config = read("services/gpu_service/config.py")
    models = read("services/gpu_service/models.py")
    assert "EMBED_USE_FP16" in config
    assert "RERANKER_USE_FP16" in config
    assert "CUDA is required; CPU fallback is disabled" in models
    assert 'return "cpu"' not in models
    assert "use_fp16=EMBED_USE_FP16" in models
    assert "use_fp16=RERANKER_USE_FP16" in models
