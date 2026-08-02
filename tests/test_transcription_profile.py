from dataclasses import FrozenInstanceError, replace
import inspect
import pytest

from src.transcription.profile import (
    FakeAlphaConfig, ProfileOperation, ProfileRegistry, ProfileResolutionFailure,
    ReleasePolicy, ResolvedProfile, StartTranscriptionRequest, TranscriptionExecutionConfig,
    TranscriptionProfileDefinition, derive_release_policy, provider_config_from_json, resolve_profile,
)
from src.transcription.types import ContractValidationError, ProfileAdmission, ProfileQualification, ProviderAvailability
from tests.transcription_fixture_helpers import make_execution_bundle, make_profile


def test_profile_axes_reject_cross_domain_enums_and_raw_strings():
    with pytest.raises(ContractValidationError):
        make_profile(qualification=ProfileAdmission.enabled)
    with pytest.raises(ContractValidationError):
        make_profile(admission=ProviderAvailability.available)
    with pytest.raises(ContractValidationError):
        make_profile(qualification="experimental")


def test_experimental_policy_is_mandatory_on_construct_and_load():
    expected = ReleasePolicy(True, False, False)
    assert derive_release_policy(ProfileQualification.experimental, expected) == expected
    for bad in [ReleasePolicy(False, False, False), ReleasePolicy(True, True, False), ReleasePolicy(True, False, True)]:
        with pytest.raises(ContractValidationError):
            derive_release_policy(ProfileQualification.experimental, bad)
        with pytest.raises(ContractValidationError):
            make_profile(qualification=ProfileQualification.experimental, release_policy=bad)


def test_start_request_only_accepts_profile_id():
    request = StartTranscriptionRequest.from_json_dict({"profile_id": "fake-alpha-standard"})
    assert request.to_json_dict() == {"profile_id": "fake-alpha-standard"}
    for extra in ["config", "url", "model_path", "hotwords", "decoder", "auto_publish"]:
        with pytest.raises(ContractValidationError):
            StartTranscriptionRequest.from_json_dict({"profile_id": "fake-alpha-standard", extra: "x"})
    assert list(inspect.signature(resolve_profile).parameters) == ["registry", "profile_id", "operation"]


def test_registry_operation_matrix_is_complete():
    enabled = make_profile(profile_id="enabled-profile")
    disabled = make_profile(profile_id="disabled-profile", admission=ProfileAdmission.disabled)
    deprecated = make_profile(profile_id="deprecated-profile", admission=ProfileAdmission.deprecated)
    registry = ProfileRegistry(tuple(sorted((enabled, disabled, deprecated), key=lambda x: x.profile_id)))
    for op in ProfileOperation:
        assert isinstance(registry.resolve_profile("missing-profile", op), ProfileResolutionFailure)
        assert isinstance(registry.resolve_profile("disabled-profile", op), ProfileResolutionFailure)
        assert isinstance(registry.resolve_profile("enabled-profile", op), ResolvedProfile)
    for op in (ProfileOperation.new_attempt, ProfileOperation.retry):
        assert isinstance(registry.resolve_profile("deprecated-profile", op), ProfileResolutionFailure)
    continued = registry.resolve_profile("deprecated-profile", ProfileOperation.continue_existing)
    published = registry.resolve_profile("deprecated-profile", ProfileOperation.publish_existing)
    assert isinstance(continued, ResolvedProfile) and continued.requires_running_original_attempt
    assert isinstance(published, ResolvedProfile) and published.requires_explicit_admin and published.force_manual_release


def test_registry_and_execution_are_deeply_immutable_and_fingerprinted():
    input_ref, profile, execution, snapshot = make_execution_bundle()
    assert isinstance(profile.provider_config, FakeAlphaConfig)
    before = execution.to_json_dict()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        execution.language = "en"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        profile.evidence_refs += ("x",)
    assert execution.to_json_dict() == before
    assert snapshot.execution_fingerprint == execution.execution_fingerprint
    changed = replace(input_ref, duration_ms=input_ref.duration_ms + 1)
    from src.transcription.profile import validate_execution_consistency
    with pytest.raises(ContractValidationError):
        validate_execution_consistency(changed, execution, snapshot)


def test_provider_config_registry_is_strict_and_versioned():
    assert provider_config_from_json({"config_kind": "fake-alpha", "config_version": "1", "punctuation_mode": "preserve"}) == FakeAlphaConfig()
    for data in [
        {"config_kind": "unknown", "config_version": "1"},
        {"config_kind": "fake-alpha", "config_version": "2", "punctuation_mode": "preserve"},
        {"config_kind": "fake-alpha", "config_version": "1", "punctuation_mode": "preserve", "extra": 1},
    ]:
        with pytest.raises(ContractValidationError):
            provider_config_from_json(data)
