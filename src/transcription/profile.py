"""Trusted transcription profiles, registry resolution, and execution snapshots."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias

from .types import (
    CANONICAL_SCHEMA_VERSION,
    ContractValidationError,
    NormalizerConfig,
    ProfileAdmission,
    ProfileQualification,
    TranscriptionInputRef,
    canonical_json_bytes,
    reject_unknown_fields,
    require_exact_enum,
    require_int,
    require_string,
    sha256_hex,
    validate_language,
    validate_profile_id,
    validate_provider_key,
    validate_schema_version,
    validate_sha256,
    validate_version,
)


class ProfileOperation(Enum):
    new_attempt = "new_attempt"
    retry = "retry"
    continue_existing = "continue_existing"
    publish_existing = "publish_existing"


class ProfileResolutionReason(Enum):
    profile_not_registered = "profile_not_registered"
    profile_disabled = "profile_disabled"
    profile_deprecated_for_operation = "profile_deprecated_for_operation"


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    requires_review: bool
    auto_publish: bool
    auto_index: bool

    def __post_init__(self) -> None:
        for field in ("requires_review", "auto_publish", "auto_index"):
            if type(getattr(self, field)) is not bool:
                raise ContractValidationError("invalid_boolean", f"release_policy.{field}")

    def to_json_dict(self) -> dict[str, bool]:
        return {
            "requires_review": self.requires_review,
            "auto_publish": self.auto_publish,
            "auto_index": self.auto_index,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ReleasePolicy":
        obj = reject_unknown_fields(data, {"requires_review", "auto_publish", "auto_index"}, "release_policy")
        return cls(obj["requires_review"], obj["auto_publish"], obj["auto_index"])


def derive_release_policy(qualification: ProfileQualification, requested: ReleasePolicy) -> ReleasePolicy:
    require_exact_enum(qualification, ProfileQualification, "qualification")
    if type(requested) is not ReleasePolicy:
        raise ContractValidationError("invalid_release_policy", "release_policy")
    if qualification is ProfileQualification.experimental:
        mandatory = ReleasePolicy(True, False, False)
        if requested != mandatory:
            raise ContractValidationError("experimental_policy_conflict", "release_policy")
        return mandatory
    return requested


@dataclass(frozen=True, slots=True)
class FakeAlphaConfig:
    config_kind: str = "fake-alpha"
    config_version: str = "1"
    punctuation_mode: str = "preserve"

    def __post_init__(self) -> None:
        if self.config_kind != "fake-alpha" or self.config_version != "1":
            raise ContractValidationError("unsupported_provider_config", "provider_config")
        if self.punctuation_mode not in ("preserve", "plain"):
            raise ContractValidationError("invalid_provider_config", "punctuation_mode")

    def to_json_dict(self) -> dict[str, Any]:
        return {"config_kind": self.config_kind, "config_version": self.config_version, "punctuation_mode": self.punctuation_mode}


@dataclass(frozen=True, slots=True)
class FakeBetaConfig:
    config_kind: str = "fake-beta"
    config_version: str = "1"
    segment_style: str = "balanced"

    def __post_init__(self) -> None:
        if self.config_kind != "fake-beta" or self.config_version != "1":
            raise ContractValidationError("unsupported_provider_config", "provider_config")
        if self.segment_style not in ("balanced", "short"):
            raise ContractValidationError("invalid_provider_config", "segment_style")

    def to_json_dict(self) -> dict[str, Any]:
        return {"config_kind": self.config_kind, "config_version": self.config_version, "segment_style": self.segment_style}


@dataclass(frozen=True, slots=True)
class FakeGammaConfig:
    config_kind: str = "fake-gamma"
    config_version: str = "1"
    confidence_mode: str = "none"

    def __post_init__(self) -> None:
        if self.config_kind != "fake-gamma" or self.config_version != "1":
            raise ContractValidationError("unsupported_provider_config", "provider_config")
        if self.confidence_mode not in ("none", "fixed"):
            raise ContractValidationError("invalid_provider_config", "confidence_mode")

    def to_json_dict(self) -> dict[str, Any]:
        return {"config_kind": self.config_kind, "config_version": self.config_version, "confidence_mode": self.confidence_mode}


ProviderTrustedConfig: TypeAlias = FakeAlphaConfig | FakeBetaConfig | FakeGammaConfig
_PROVIDER_CONFIG_TYPES = (FakeAlphaConfig, FakeBetaConfig, FakeGammaConfig)


def provider_config_from_json(data: object) -> ProviderTrustedConfig:
    if type(data) is not dict:
        raise ContractValidationError("invalid_object", "provider_config")
    kind = data.get("config_kind")
    version = data.get("config_version")
    if kind == "fake-alpha" and version == "1":
        obj = reject_unknown_fields(data, {"config_kind", "config_version", "punctuation_mode"}, "provider_config")
        return FakeAlphaConfig(obj["config_kind"], obj["config_version"], obj["punctuation_mode"])
    if kind == "fake-beta" and version == "1":
        obj = reject_unknown_fields(data, {"config_kind", "config_version", "segment_style"}, "provider_config")
        return FakeBetaConfig(obj["config_kind"], obj["config_version"], obj["segment_style"])
    if kind == "fake-gamma" and version == "1":
        obj = reject_unknown_fields(data, {"config_kind", "config_version", "confidence_mode"}, "provider_config")
        return FakeGammaConfig(obj["config_kind"], obj["config_version"], obj["confidence_mode"])
    raise ContractValidationError("unsupported_provider_config", "provider_config")


def _validate_provider_config(value: object, provider_key: str) -> ProviderTrustedConfig:
    if type(value) not in _PROVIDER_CONFIG_TYPES:
        raise ContractValidationError("unregistered_provider_config", "provider_config")
    if value.config_kind != provider_key:
        raise ContractValidationError("provider_config_mismatch", "provider_config.config_kind")
    return value


def _execution_payload_dict(
    *,
    profile_id: str,
    provider_key: str,
    profile_definition_version: str,
    provider_adapter_version: str,
    provider_config: ProviderTrustedConfig,
    normalizer_config: NormalizerConfig,
    canonical_schema_version: str,
    normalizer_version: str,
    formatter_version: str,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "provider_key": provider_key,
        "profile_definition_version": profile_definition_version,
        "provider_adapter_version": provider_adapter_version,
        "provider_config": provider_config.to_json_dict(),
        "normalizer_config": normalizer_config.to_json_dict(),
        "canonical_schema_version": canonical_schema_version,
        "normalizer_version": normalizer_version,
        "formatter_version": formatter_version,
    }


def compute_config_hash(**kwargs: Any) -> str:
    return sha256_hex(canonical_json_bytes(_execution_payload_dict(**kwargs)))


@dataclass(frozen=True, slots=True)
class TranscriptionProfileDefinition:
    profile_id: str
    display_name: str
    description: str
    provider_key: str
    provider_config: ProviderTrustedConfig
    normalizer_config: NormalizerConfig
    qualification: ProfileQualification
    admission: ProfileAdmission
    release_policy: ReleasePolicy
    profile_definition_version: str
    config_hash: str
    provider_adapter_version: str
    canonical_schema_version: str
    normalizer_version: str
    formatter_version: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_profile_id(self.profile_id)
        require_string(self.display_name, "display_name")
        require_string(self.description, "description", nonempty=False)
        if "\n" in self.display_name or "\r" in self.display_name:
            raise ContractValidationError("multiline_display_name", "display_name")
        validate_provider_key(self.provider_key)
        _validate_provider_config(self.provider_config, self.provider_key)
        if type(self.normalizer_config) is not NormalizerConfig:
            raise ContractValidationError("invalid_normalizer_config", "normalizer_config")
        require_exact_enum(self.qualification, ProfileQualification, "qualification")
        require_exact_enum(self.admission, ProfileAdmission, "admission")
        derived = derive_release_policy(self.qualification, self.release_policy)
        if derived != self.release_policy:
            raise ContractValidationError("release_policy_not_derived", "release_policy")
        validate_version(self.profile_definition_version, "profile_definition_version")
        validate_sha256(self.config_hash, "config_hash")
        validate_version(self.provider_adapter_version, "provider_adapter_version")
        validate_schema_version(self.canonical_schema_version, "canonical_schema_version")
        validate_version(self.normalizer_version, "normalizer_version")
        validate_version(self.formatter_version, "formatter_version")
        if type(self.evidence_refs) is not tuple:
            raise ContractValidationError("mutable_collection", "evidence_refs")
        for item in self.evidence_refs:
            require_string(item, "evidence_refs")
        expected = compute_config_hash(
            profile_id=self.profile_id,
            provider_key=self.provider_key,
            profile_definition_version=self.profile_definition_version,
            provider_adapter_version=self.provider_adapter_version,
            provider_config=self.provider_config,
            normalizer_config=self.normalizer_config,
            canonical_schema_version=self.canonical_schema_version,
            normalizer_version=self.normalizer_version,
            formatter_version=self.formatter_version,
        )
        if not hmac.compare_digest(expected, self.config_hash):
            raise ContractValidationError("config_hash_mismatch", "config_hash")

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        display_name: str,
        description: str,
        provider_key: str,
        provider_config: ProviderTrustedConfig,
        normalizer_config: NormalizerConfig,
        qualification: ProfileQualification,
        admission: ProfileAdmission,
        release_policy: ReleasePolicy,
        profile_definition_version: str = "1",
        provider_adapter_version: str = "1",
        canonical_schema_version: str = CANONICAL_SCHEMA_VERSION,
        normalizer_version: str = "1",
        formatter_version: str = "1",
        evidence_refs: tuple[str, ...] = (),
    ) -> "TranscriptionProfileDefinition":
        config_hash = compute_config_hash(
            profile_id=profile_id,
            provider_key=provider_key,
            profile_definition_version=profile_definition_version,
            provider_adapter_version=provider_adapter_version,
            provider_config=provider_config,
            normalizer_config=normalizer_config,
            canonical_schema_version=canonical_schema_version,
            normalizer_version=normalizer_version,
            formatter_version=formatter_version,
        )
        return cls(
            profile_id,
            display_name,
            description,
            provider_key,
            provider_config,
            normalizer_config,
            qualification,
            admission,
            release_policy,
            profile_definition_version,
            config_hash,
            provider_adapter_version,
            canonical_schema_version,
            normalizer_version,
            formatter_version,
            evidence_refs,
        )

    def execution_payload_dict(self) -> dict[str, Any]:
        return _execution_payload_dict(
            profile_id=self.profile_id,
            provider_key=self.provider_key,
            profile_definition_version=self.profile_definition_version,
            provider_adapter_version=self.provider_adapter_version,
            provider_config=self.provider_config,
            normalizer_config=self.normalizer_config,
            canonical_schema_version=self.canonical_schema_version,
            normalizer_version=self.normalizer_version,
            formatter_version=self.formatter_version,
        )


@dataclass(frozen=True, slots=True)
class StartTranscriptionRequest:
    profile_id: str

    def __post_init__(self) -> None:
        validate_profile_id(self.profile_id)

    def to_json_dict(self) -> dict[str, str]:
        return {"profile_id": self.profile_id}

    @classmethod
    def from_json_dict(cls, data: object) -> "StartTranscriptionRequest":
        obj = reject_unknown_fields(data, {"profile_id"}, "start_request")
        return cls(obj["profile_id"])


@dataclass(frozen=True, slots=True)
class ProfileResolutionFailure:
    profile_id: str
    operation: ProfileOperation
    reason_code: ProfileResolutionReason

    def __post_init__(self) -> None:
        validate_profile_id(self.profile_id)
        require_exact_enum(self.operation, ProfileOperation, "operation")
        require_exact_enum(self.reason_code, ProfileResolutionReason, "reason_code")


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile: TranscriptionProfileDefinition
    operation: ProfileOperation
    requires_running_original_attempt: bool = False
    requires_explicit_admin: bool = False
    force_manual_release: bool = False

    def __post_init__(self) -> None:
        if type(self.profile) is not TranscriptionProfileDefinition:
            raise ContractValidationError("invalid_profile", "profile")
        require_exact_enum(self.operation, ProfileOperation, "operation")
        for field in ("requires_running_original_attempt", "requires_explicit_admin", "force_manual_release"):
            if type(getattr(self, field)) is not bool:
                raise ContractValidationError("invalid_boolean", field)


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    definitions: tuple[TranscriptionProfileDefinition, ...]

    def __post_init__(self) -> None:
        if type(self.definitions) is not tuple:
            raise ContractValidationError("mutable_collection", "definitions")
        previous = ""
        for definition in self.definitions:
            if type(definition) is not TranscriptionProfileDefinition:
                raise ContractValidationError("invalid_profile", "definitions")
            if definition.profile_id <= previous:
                raise ContractValidationError("profiles_not_sorted_unique", "definitions")
            previous = definition.profile_id

    def resolve_profile(self, profile_id: str, operation: ProfileOperation) -> ResolvedProfile | ProfileResolutionFailure:
        validate_profile_id(profile_id)
        require_exact_enum(operation, ProfileOperation, "operation")
        definition = next((item for item in self.definitions if item.profile_id == profile_id), None)
        if definition is None:
            return ProfileResolutionFailure(profile_id, operation, ProfileResolutionReason.profile_not_registered)
        if definition.admission is ProfileAdmission.disabled:
            return ProfileResolutionFailure(profile_id, operation, ProfileResolutionReason.profile_disabled)
        if definition.admission is ProfileAdmission.deprecated:
            if operation in (ProfileOperation.new_attempt, ProfileOperation.retry):
                return ProfileResolutionFailure(profile_id, operation, ProfileResolutionReason.profile_deprecated_for_operation)
            if operation is ProfileOperation.continue_existing:
                return ResolvedProfile(definition, operation, requires_running_original_attempt=True)
            return ResolvedProfile(
                definition,
                operation,
                requires_explicit_admin=True,
                force_manual_release=True,
            )
        return ResolvedProfile(definition, operation)


def resolve_profile(
    registry: ProfileRegistry,
    profile_id: str,
    operation: ProfileOperation,
) -> ResolvedProfile | ProfileResolutionFailure:
    if type(registry) is not ProfileRegistry:
        raise ContractValidationError("invalid_registry", "registry")
    return registry.resolve_profile(profile_id, operation)


def _fingerprint_object(
    input_ref: TranscriptionInputRef,
    *,
    profile_id: str,
    provider_key: str,
    profile_definition_version: str,
    provider_adapter_version: str,
    language: str,
    timeout_ms: int,
    provider_config: ProviderTrustedConfig,
    normalizer_config: NormalizerConfig,
    canonical_schema_version: str,
    normalizer_version: str,
    formatter_version: str,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "provider_key": provider_key,
        "profile_definition_version": profile_definition_version,
        "provider_adapter_version": provider_adapter_version,
        "input_sha256": input_ref.content_sha256,
        "input_kind": input_ref.input_kind,
        "input_size_bytes": input_ref.size_bytes,
        "input_duration_ms": input_ref.duration_ms,
        "language": language,
        "timeout_ms": timeout_ms,
        "provider_config_kind": provider_config.config_kind,
        "provider_config_version": provider_config.config_version,
        "provider_config": provider_config.to_json_dict(),
        "normalizer_config": normalizer_config.to_json_dict(),
        "canonical_schema_version": canonical_schema_version,
        "normalizer_version": normalizer_version,
        "formatter_version": formatter_version,
    }


def compute_execution_fingerprint(input_ref: TranscriptionInputRef, **kwargs: Any) -> str:
    if type(input_ref) is not TranscriptionInputRef:
        raise ContractValidationError("invalid_input_ref", "input_ref")
    return sha256_hex(canonical_json_bytes(_fingerprint_object(input_ref, **kwargs)))


@dataclass(frozen=True, slots=True)
class TranscriptionExecutionConfig:
    profile_id: str
    provider_key: str
    profile_definition_version: str
    provider_adapter_version: str
    language: str
    timeout_ms: int
    provider_config: ProviderTrustedConfig
    normalizer_config: NormalizerConfig
    canonical_schema_version: str
    normalizer_version: str
    formatter_version: str
    execution_fingerprint: str

    def __post_init__(self) -> None:
        validate_profile_id(self.profile_id)
        validate_provider_key(self.provider_key)
        validate_version(self.profile_definition_version, "profile_definition_version")
        validate_version(self.provider_adapter_version, "provider_adapter_version")
        validate_language(self.language)
        require_int(self.timeout_ms, "timeout_ms", positive=True)
        _validate_provider_config(self.provider_config, self.provider_key)
        if type(self.normalizer_config) is not NormalizerConfig:
            raise ContractValidationError("invalid_normalizer_config", "normalizer_config")
        validate_schema_version(self.canonical_schema_version, "canonical_schema_version")
        validate_version(self.normalizer_version, "normalizer_version")
        validate_version(self.formatter_version, "formatter_version")
        validate_sha256(self.execution_fingerprint, "execution_fingerprint")

    @classmethod
    def create(
        cls,
        profile: TranscriptionProfileDefinition,
        input_ref: TranscriptionInputRef,
        *,
        language: str,
        timeout_ms: int,
    ) -> "TranscriptionExecutionConfig":
        if type(profile) is not TranscriptionProfileDefinition:
            raise ContractValidationError("invalid_profile", "profile")
        kwargs = {
            "profile_id": profile.profile_id,
            "provider_key": profile.provider_key,
            "profile_definition_version": profile.profile_definition_version,
            "provider_adapter_version": profile.provider_adapter_version,
            "language": language,
            "timeout_ms": timeout_ms,
            "provider_config": profile.provider_config,
            "normalizer_config": profile.normalizer_config,
            "canonical_schema_version": profile.canonical_schema_version,
            "normalizer_version": profile.normalizer_version,
            "formatter_version": profile.formatter_version,
        }
        fingerprint = compute_execution_fingerprint(input_ref, **kwargs)
        return cls(**kwargs, execution_fingerprint=fingerprint)

    def fingerprint_kwargs(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "provider_key": self.provider_key,
            "profile_definition_version": self.profile_definition_version,
            "provider_adapter_version": self.provider_adapter_version,
            "language": self.language,
            "timeout_ms": self.timeout_ms,
            "provider_config": self.provider_config,
            "normalizer_config": self.normalizer_config,
            "canonical_schema_version": self.canonical_schema_version,
            "normalizer_version": self.normalizer_version,
            "formatter_version": self.formatter_version,
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            **{key: value for key, value in self.fingerprint_kwargs().items() if key not in ("provider_config", "normalizer_config")},
            "provider_config": self.provider_config.to_json_dict(),
            "normalizer_config": self.normalizer_config.to_json_dict(),
            "execution_fingerprint": self.execution_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    profile_id: str
    provider_key: str
    profile_definition_version: str
    config_hash: str
    qualification: ProfileQualification
    admission: ProfileAdmission
    release_policy: ReleasePolicy
    provider_adapter_version: str
    canonical_schema_version: str
    normalizer_version: str
    formatter_version: str
    execution_fingerprint: str

    def __post_init__(self) -> None:
        validate_profile_id(self.profile_id)
        validate_provider_key(self.provider_key)
        validate_version(self.profile_definition_version, "profile_definition_version")
        validate_sha256(self.config_hash, "config_hash")
        require_exact_enum(self.qualification, ProfileQualification, "qualification")
        require_exact_enum(self.admission, ProfileAdmission, "admission")
        derive_release_policy(self.qualification, self.release_policy)
        validate_version(self.provider_adapter_version, "provider_adapter_version")
        validate_schema_version(self.canonical_schema_version, "canonical_schema_version")
        validate_version(self.normalizer_version, "normalizer_version")
        validate_version(self.formatter_version, "formatter_version")
        validate_sha256(self.execution_fingerprint, "execution_fingerprint")

    @classmethod
    def create(
        cls,
        profile: TranscriptionProfileDefinition,
        execution: TranscriptionExecutionConfig,
    ) -> "ProfileSnapshot":
        if type(profile) is not TranscriptionProfileDefinition or type(execution) is not TranscriptionExecutionConfig:
            raise ContractValidationError("invalid_snapshot_source", "profile_snapshot")
        return cls(
            profile.profile_id,
            profile.provider_key,
            profile.profile_definition_version,
            profile.config_hash,
            profile.qualification,
            profile.admission,
            profile.release_policy,
            profile.provider_adapter_version,
            profile.canonical_schema_version,
            profile.normalizer_version,
            profile.formatter_version,
            execution.execution_fingerprint,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "provider_key": self.provider_key,
            "profile_definition_version": self.profile_definition_version,
            "config_hash": self.config_hash,
            "qualification": self.qualification.value,
            "admission": self.admission.value,
            "release_policy": self.release_policy.to_json_dict(),
            "provider_adapter_version": self.provider_adapter_version,
            "canonical_schema_version": self.canonical_schema_version,
            "normalizer_version": self.normalizer_version,
            "formatter_version": self.formatter_version,
            "execution_fingerprint": self.execution_fingerprint,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "ProfileSnapshot":
        allowed = {
            "profile_id", "provider_key", "profile_definition_version", "config_hash", "qualification", "admission",
            "release_policy", "provider_adapter_version", "canonical_schema_version", "normalizer_version",
            "formatter_version", "execution_fingerprint",
        }
        obj = reject_unknown_fields(data, allowed, "profile_snapshot")
        try:
            qualification = ProfileQualification(obj["qualification"])
            admission = ProfileAdmission(obj["admission"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_profile_state", "profile_snapshot") from exc
        return cls(
            obj["profile_id"], obj["provider_key"], obj["profile_definition_version"], obj["config_hash"],
            qualification, admission, ReleasePolicy.from_json_dict(obj["release_policy"]),
            obj["provider_adapter_version"], obj["canonical_schema_version"], obj["normalizer_version"],
            obj["formatter_version"], obj["execution_fingerprint"],
        )


def validate_execution_consistency(
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
    snapshot: ProfileSnapshot,
) -> None:
    if type(input_ref) is not TranscriptionInputRef:
        raise ContractValidationError("invalid_input_ref", "input_ref")
    if type(execution) is not TranscriptionExecutionConfig:
        raise ContractValidationError("invalid_execution_config", "execution_config")
    if type(snapshot) is not ProfileSnapshot:
        raise ContractValidationError("invalid_profile_snapshot", "profile_snapshot")
    pairs = (
        (execution.profile_id, snapshot.profile_id),
        (execution.provider_key, snapshot.provider_key),
        (execution.profile_definition_version, snapshot.profile_definition_version),
        (execution.provider_adapter_version, snapshot.provider_adapter_version),
        (execution.canonical_schema_version, snapshot.canonical_schema_version),
        (execution.normalizer_version, snapshot.normalizer_version),
        (execution.formatter_version, snapshot.formatter_version),
        (execution.execution_fingerprint, snapshot.execution_fingerprint),
    )
    if any(left != right for left, right in pairs):
        raise ContractValidationError("execution_snapshot_mismatch", "profile_snapshot")
    expected = compute_execution_fingerprint(input_ref, **execution.fingerprint_kwargs())
    if not hmac.compare_digest(expected, execution.execution_fingerprint):
        raise ContractValidationError("execution_fingerprint_mismatch", "execution_fingerprint")
