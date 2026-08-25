#!/usr/bin/env python3
"""Run the explicit, mutually exclusive Python CI test groups."""

from __future__ import annotations

import subprocess
import sys


GROUPS = {
    "transcription": (
        "tests/test_transcription_admission_config.py",
        "tests/test_transcription_application.py",
        "tests/test_transcription_artifacts.py",
        "tests/test_transcription_canonical.py",
        "tests/test_transcription_db_migrations.py",
        "tests/test_transcription_formatter.py",
        "tests/test_transcription_manual_revision.py",
        "tests/test_transcription_media_input.py",
        "tests/test_transcription_normalizer.py",
        "tests/test_transcription_phase2_manual_regression.py",
        "tests/test_transcription_phase2_static_boundaries.py",
        "tests/test_transcription_phase2_types.py",
        "tests/test_transcription_phase4_api.py",
        "tests/test_transcription_phase4_static_boundaries.py",
        "tests/test_transcription_policy.py",
        "tests/test_transcription_profile.py",
        "tests/test_transcription_provider_contract.py",
        "tests/test_transcription_publication_transaction.py",
        "tests/test_transcription_recovery.py",
        "tests/test_transcription_scheme_runtime.py",
        "tests/test_transcription_static_boundaries.py",
        "tests/test_transcription_store.py",
        "tests/test_transcription_types.py",
        "tests/test_transcription_worker.py",
        "tests/test_transcription_workflow_persistence.py",
        "tests/test_transcript_manual_regression.py",
        "tests/test_production_full_reindex.py",
        "tests/test_production_full_reindex_workflow.py",
    ),
    "phase5": (
        "tests/test_transcription_phase5_application_e2e.py",
        "tests/test_transcription_publication_index_adapter.py",
        "tests/test_transcription_retrieval_visibility.py",
        "tests/test_transcript_retrieval_integration.py",
        "tests/test_transcription_phase5_worker.py",
        "tests/test_transcript_index_metadata.py",
        "tests/test_transcription_phase5_static_boundaries.py",
    ),
    "asr": (
        "tests/test_transcription_asr_service_contract.py",
        "tests/test_transcription_provider_registry.py",
        "tests/test_transcription_profile_catalog.py",
        "tests/test_transcription_remote_provider.py",
        "tests/test_asr_deployment_static.py",
        "tests/test_qwen3_asr_controlled_wheel.py",
        "tests/test_qwen3_asr_qualification_failure_evidence.py",
        "tests/test_qwen3_asr_performance_diagnostic.py",
        "tests/test_gpu_runtime_deployment_static.py",
        "tests/test_deploy_git_safety.py",
        "tests/test_asr_activation.py",
        "tests/test_faster_whisper_model_tls.py",
        "tests/test_service_directory_boundaries.py",
        "services/asr_service/tests",
    ),
    "providers": ("tests/test_providers.py",),
    "gpu": ("services/gpu_service/tests/test_contract.py",),
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in GROUPS:
        print(f"usage: {sys.argv[0]} <{'|'.join(GROUPS)}>", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, "-m", "pytest", *GROUPS[sys.argv[1]], "-v"])


if __name__ == "__main__":
    raise SystemExit(main())
