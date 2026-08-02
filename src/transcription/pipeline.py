"""Single orchestration boundary for provider execution and normalization."""
from __future__ import annotations

import hmac
from typing import Any

from .canonical import CanonicalTranscript
from .normalizer import normalize_candidate
from .profile import (
    ProfileSnapshot,
    TranscriptionExecutionConfig,
    compute_execution_fingerprint,
    validate_execution_consistency,
)
from .provider_protocol import (
    PermanentProviderError,
    ProviderCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderFailureClassification,
    ProviderTimeoutError,
    TranscriptionProvider,
    TransientProviderError,
)
from .types import ContractValidationError, TranscriptionInputRef, canonical_json_bytes


def _failure(
    provider_key: str,
    code: ProviderErrorCode,
    *,
    timeout_ms: int | None = None,
    classification: ProviderFailureClassification | None = None,
) -> ProviderFailure:
    if classification is None:
        classification = (
            ProviderFailureClassification.transient
            if code is ProviderErrorCode.provider_timeout
            else ProviderFailureClassification.permanent
        )
    return ProviderFailure(provider_key, code, classification, timeout_ms)


def _input_bytes(input_ref: TranscriptionInputRef) -> bytes:
    return canonical_json_bytes(input_ref.to_json_dict())


def _execution_bytes(execution: TranscriptionExecutionConfig) -> bytes:
    return canonical_json_bytes(execution.to_json_dict())


def _safe_provider_key(provider: object, fallback: str) -> str:
    try:
        value = getattr(provider, "provider_key")
    except Exception:
        return fallback
    return value if type(value) is str else fallback


def _validate_returned_failure(
    result: ProviderFailure,
    execution: TranscriptionExecutionConfig,
) -> bool:
    try:
        # A frozen dataclass can still be corrupted through low-level mutation.
        # Rebuild from its strict JSON boundary before allowing a Provider-owned
        # failure to leave the pipeline unchanged.
        if ProviderFailure.from_json_dict(result.to_json_dict()) != result:
            return False
        if result.provider_key != execution.provider_key:
            return False
        if result.error_code is ProviderErrorCode.provider_timeout:
            return result.timeout_ms == execution.timeout_ms
        return result.timeout_ms is None
    except Exception:
        return False


def _validate_returned_candidate(result: ProviderCandidate) -> ProviderCandidate | None:
    try:
        # Provider-owned frozen objects can still be corrupted through low-level
        # mutation.  Rebuild the complete nested Candidate through its strict
        # JSON boundary and pass only that detached value to the normalizer.
        return ProviderCandidate.from_json_dict(result.to_json_dict())
    except Exception:
        return None


def _candidate_matches_execution(
    result: ProviderCandidate,
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
) -> bool:
    try:
        return (
            result.provider_key == execution.provider_key
            and result.language == execution.language
            and result.duration_ms == input_ref.duration_ms
        )
    except Exception:
        return False


def execute_transcription(
    provider: TranscriptionProvider,
    input_ref: TranscriptionInputRef,
    execution_config: TranscriptionExecutionConfig,
    *,
    profile_snapshot: ProfileSnapshot,
) -> CanonicalTranscript | ProviderFailure:
    """Call one provider and route Candidate/Failure through the frozen pipeline."""
    provider_key = "unknown"
    try:
        if type(input_ref) is not TranscriptionInputRef:
            return _failure(provider_key, ProviderErrorCode.invalid_input)
        if type(execution_config) is not TranscriptionExecutionConfig:
            return _failure(provider_key, ProviderErrorCode.invalid_input)
        provider_key = execution_config.provider_key
        if type(profile_snapshot) is not ProfileSnapshot:
            return _failure(provider_key, ProviderErrorCode.invalid_input)
        validate_execution_consistency(input_ref, execution_config, profile_snapshot)
        if _safe_provider_key(provider, provider_key) != provider_key:
            return _failure(provider_key, ProviderErrorCode.provider_contract_violation)
        before_input = _input_bytes(input_ref)
        before_execution = _execution_bytes(execution_config)
        before_fingerprint = execution_config.execution_fingerprint
    except Exception:
        return _failure(provider_key, ProviderErrorCode.invalid_input)

    captured: ProviderFailure | None = None
    result: Any = None
    try:
        result = provider.transcribe(input_ref, execution_config)
    except ProviderTimeoutError:
        captured = _failure(
            provider_key,
            ProviderErrorCode.provider_timeout,
            timeout_ms=execution_config.timeout_ms,
        )
    except TransientProviderError:
        captured = _failure(
            provider_key,
            ProviderErrorCode.transient_provider_error,
            classification=ProviderFailureClassification.transient,
        )
    except PermanentProviderError:
        captured = _failure(provider_key, ProviderErrorCode.permanent_provider_error)
    except Exception:
        captured = _failure(provider_key, ProviderErrorCode.provider_contract_violation)

    try:
        after_input = _input_bytes(input_ref)
        after_execution = _execution_bytes(execution_config)
        after_fingerprint = compute_execution_fingerprint(
            input_ref,
            **execution_config.fingerprint_kwargs(),
        )
    except Exception:
        return _failure(provider_key, ProviderErrorCode.execution_config_mutated)

    if (
        not hmac.compare_digest(before_input, after_input)
        or not hmac.compare_digest(before_execution, after_execution)
        or not hmac.compare_digest(before_fingerprint, after_fingerprint)
    ):
        return _failure(provider_key, ProviderErrorCode.execution_config_mutated)

    if captured is not None:
        return captured
    if type(result) is ProviderFailure:
        if _validate_returned_failure(result, execution_config):
            return result
        return _failure(provider_key, ProviderErrorCode.provider_contract_violation)
    if type(result) is not ProviderCandidate:
        return _failure(provider_key, ProviderErrorCode.provider_contract_violation)
    strict_candidate = _validate_returned_candidate(result)
    if strict_candidate is None:
        return _failure(provider_key, ProviderErrorCode.invalid_provider_output)
    if not _candidate_matches_execution(strict_candidate, input_ref, execution_config):
        return _failure(provider_key, ProviderErrorCode.invalid_provider_output)

    try:
        return normalize_candidate(input_ref, strict_candidate, profile_snapshot, execution_config)
    except ContractValidationError:
        return _failure(provider_key, ProviderErrorCode.invalid_provider_output)
    except Exception:
        return _failure(provider_key, ProviderErrorCode.provider_contract_violation)
