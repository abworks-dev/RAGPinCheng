from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_lock_is_fail_closed_until_r3_2b():
    metadata = json.loads(read("gpu_service/runtime-lock.json"))
    requirements = read("gpu_service/runtime-lock.txt")
    assert metadata["schema_version"] == 1
    assert metadata["validation_status"] == "unvalidated"
    assert metadata["qualification_run_id"] is None
    assert metadata["source_commit"] is None
    assert metadata["qualified_source_fingerprint"] is None
    assert metadata["qualified_lock_sha256"] is None
    assert [line for line in requirements.splitlines() if line and not line.startswith("#")] == []


def test_known_bad_two_package_pin_is_not_a_production_contract():
    gpu_requirements = read("gpu_service/requirements.txt")
    root_requirements = read("requirements-gpu.txt")
    for source in (gpu_requirements, root_requirements):
        assert "transformers==4.46.3" not in source
        assert "tokenizers==0.20.3" not in source
    assert "runtime-lock" in gpu_requirements
    assert "runtime-lock" in root_requirements


def test_builder_is_d_drive_isolated_exact_and_records_artifacts():
    script = read("scripts/build-gpu-runtime.ps1")
    lock_hash = read("scripts/get-gpu-runtime-lock-hash.ps1")
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
    assert '"source"' in script
    assert "working tree contract does not match" in script
    assert "Validated metadata cannot construct a release without prior candidate qualification" in script
    assert "GPU runtime package index is not approved" in script
    assert "GPU runtime torch index is not approved" in script
    assert '.Replace("`r`n", "`n").Replace("`r", "`n")' in lock_hash


def test_candidate_qualification_is_cuda_only_and_cleans_tasks():
    workflow = read(".github/workflows/repair-gpu-reranker-production.yml")
    script = read("scripts/qualify-gpu-runtime.ps1")
    probe = read("scripts/diagnose_gpu_reranker.py")
    assert "workflow_dispatch:" in workflow
    assert "confirm_qualification:" in workflow
    assert "production-gpu-exclusive" in workflow
    assert "build-gpu-runtime.ps1" in workflow
    assert "qualify-gpu-runtime.ps1" in workflow
    assert "promote-gpu-runtime.ps1" not in workflow
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
    assert "source_inventory_sha256" in script
    assert "repository_commit" in script
    assert "Set-Location -LiteralPath $SourceRoot" in script
    assert 'choices=("fp16", "fp32")' in probe
    assert "torch.cuda.is_available()" in probe
    assert "embed_inference_complete" in probe
    assert "reranker_inference_complete" in probe


def test_automatic_deploy_is_gated_and_gpu_fingerprint_aware():
    workflow = read(".github/workflows/deploy-production.yml")
    deploy = read("scripts/deploy-gpu.ps1")
    assert "PRODUCTION_AUTO_DEPLOY_ENABLED == 'true'" in workflow
    assert "production-gpu-exclusive" in workflow
    assert "production-app-deployment" in workflow
    assert "GPU_MODEL_CACHE_SOURCE" in workflow
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
    assert "merge-base --is-ancestor" in deploy
    assert "get-gpu-runtime-lock-hash.ps1" in deploy


def test_declared_reranker_precisions_are_validated_not_widenable():
    """allowed_reranker_precisions must not be a decorative field: the builder
    validates it against the hardcoded CUDA-only set, so lock metadata can never
    widen the precision whitelist (e.g. by adding a CPU or int8 mode)."""
    metadata = json.loads(read("gpu_service/runtime-lock.json"))
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
    assert "promote-gpu-runtime.ps1" in workflow
    assert "build-gpu-runtime.ps1" not in workflow
    assert "pip install" not in workflow


def test_gpu_service_forbids_cpu_fallback_and_has_precision_controls():
    config = read("gpu_service/config.py")
    models = read("gpu_service/models.py")
    assert "EMBED_USE_FP16" in config
    assert "RERANKER_USE_FP16" in config
    assert "CUDA is required; CPU fallback is disabled" in models
    assert 'return "cpu"' not in models
    assert "use_fp16=EMBED_USE_FP16" in models
    assert "use_fp16=RERANKER_USE_FP16" in models
