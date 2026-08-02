import math
from pathlib import Path
from dataclasses import dataclass
import pytest

from src.transcription.types import (
    ArtifactKind, ArtifactReference, CANONICAL_SCHEMA_VERSION, ContractValidationError,
    ProfileAdmission, ProfileQualification, ProviderAvailability, PublicationIndexStatus,
    PublicationStatus, ReviewStatus, TranscriptWarningCode, TranscriptionJobStage,
    TranscriptionJobStatus, canonical_json_bytes, validate_artifact_id, validate_json_native,
    validate_language, validate_profile_id, validate_provider_key, validate_schema_version,
    validate_sha256, validate_uuid,
)

VALID_UUID = "123e4567-e89b-12d3-a456-426614174000"
SHA = "a" * 64


def test_profile_and_workflow_state_domains_are_nominally_distinct():
    domains = [ProfileQualification, ProfileAdmission, ProviderAvailability,
               TranscriptionJobStatus, TranscriptionJobStage, ReviewStatus,
               PublicationStatus, PublicationIndexStatus]
    assert len({id(item) for item in domains}) == len(domains)
    assert ProfileQualification.experimental != ProfileAdmission.enabled
    assert TranscriptionJobStatus.succeeded != ReviewStatus.review_approved


@pytest.mark.parametrize("value", [True, False, 1.0, -1])
def test_artifact_size_rejects_non_exact_nonnegative_int(value):
    with pytest.raises(ContractValidationError):
        ArtifactReference("diag", ArtifactKind.provider_diagnostic, SHA, value)


def test_uuid_acceptance_is_canonical_lowercase_non_nil_only():
    assert validate_uuid(VALID_UUID) == VALID_UUID
    for value in [VALID_UUID.upper(), VALID_UUID.replace("-", ""), "00000000-0000-0000-0000-000000000000", "{" + VALID_UUID + "}"]:
        with pytest.raises(ContractValidationError):
            validate_uuid(value)


@pytest.mark.parametrize("fn,valid,invalid", [
    (validate_profile_id, "abc", ["ab", "Abc", "a--b", "a/b", "https://x"]),
    (validate_provider_key, "ab", ["a", "Ab", "a_b", "a/b"]),
    (validate_artifact_id, "diag.part_1", ["Diag", "/tmp/x", "https://x", "a..b"]),
    (validate_language, "und", ["ZH-cn", "zh-cn", "zh-CN-x-private", "", "zh_CN"]),
    (validate_schema_version, CANONICAL_SCHEMA_VERSION, ["canonical-transcript/1.0", "canonical-transcript/2", "1"]),
    (validate_sha256, SHA, [SHA.upper(), "a" * 63, "g" * 64]),
])
def test_frozen_string_acceptance_sets(fn, valid, invalid):
    assert fn(valid) == valid
    for value in invalid:
        with pytest.raises(ContractValidationError):
            fn(value)


@pytest.mark.parametrize("language", ["zh", "zho", "zh-CN", "zh-Hans", "zh-Hans-CN", "en-US", "es-419"])
def test_language_frozen_grammar_positive(language):
    assert validate_language(language) == language


def test_artifact_kind_is_finite_and_unknown_kind_is_rejected():
    assert {x.value for x in ArtifactKind} == {
        "provider_diagnostic", "provider_timing", "provider_vad", "provider_tokens", "provider_confidence"
    }
    with pytest.raises(ValueError):
        ArtifactKind("raw")


def test_json_native_is_recursive_and_rejects_python_private_values():
    validate_json_native({"a": [1, 1.5, True, None, "x"]})
    @dataclass
    class Dummy: value: int = 1
    for value in [Path("x"), {1, 2}, (1, 2), b"x", Dummy(), math.nan, math.inf, ArtifactKind.provider_vad]:
        with pytest.raises(ContractValidationError):
            validate_json_native({"nested": [value]})


def test_canonical_json_bytes_are_stable_and_utf8_without_bom():
    left = canonical_json_bytes({"b": "中文", "a": 1})
    right = canonical_json_bytes({"a": 1, "b": "中文"})
    assert left == right == b'{"a":1,"b":"\xe4\xb8\xad\xe6\x96\x87"}'
    assert not left.startswith(b"\xef\xbb\xbf")
