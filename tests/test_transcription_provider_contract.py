from dataclasses import fields
import pytest

import src.transcription.pipeline as pipeline
from src.transcription.canonical import CanonicalTranscript
from src.transcription.provider_protocol import (
    ProviderCandidate, ProviderErrorCode, ProviderFailure, ProviderFailureClassification,
)
from src.transcription.types import ContractValidationError
from tests.transcription_fixture_helpers import FAKE_PROVIDER_TYPES, make_execution_bundle


@pytest.mark.parametrize("provider_type", FAKE_PROVIDER_TYPES)
def test_all_three_fake_providers_use_same_pipeline_contract(provider_type):
    i,p,e,s=make_execution_bundle(provider_type().provider_key)
    result=pipeline.execute_transcription(provider_type(),i,e,profile_snapshot=s)
    assert isinstance(result,CanonicalTranscript)
    assert result.profile_snapshot.provider_key==provider_type().provider_key


@pytest.mark.parametrize("provider_type", FAKE_PROVIDER_TYPES)
@pytest.mark.parametrize("behavior,code", [
    ("transient_failure",ProviderErrorCode.transient_provider_error),
    ("permanent_failure",ProviderErrorCode.permanent_provider_error),
    ("timeout_failure",ProviderErrorCode.provider_timeout),
    ("raise_timeout",ProviderErrorCode.provider_timeout),
    ("raise_transient",ProviderErrorCode.transient_provider_error),
    ("raise_permanent",ProviderErrorCode.permanent_provider_error),
    ("raise_unknown",ProviderErrorCode.provider_contract_violation),
    ("invalid_member",ProviderErrorCode.provider_contract_violation),
    ("invalid_failure",ProviderErrorCode.provider_contract_violation),
    ("invalid_candidate",ProviderErrorCode.invalid_provider_output),
    ("mutate_input",ProviderErrorCode.execution_config_mutated),
    ("mutate_execution",ProviderErrorCode.execution_config_mutated),
])
def test_fake_failure_matrix_is_normalized_by_pipeline(provider_type,behavior,code):
    i,p,e,s=make_execution_bundle(provider_type().provider_key)
    result=pipeline.execute_transcription(provider_type(behavior),i,e,profile_snapshot=s)
    assert isinstance(result,ProviderFailure) and result.error_code is code


def test_failure_never_calls_normalizer(monkeypatch):
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called; called=True; raise AssertionError
    monkeypatch.setattr(pipeline,"normalize_candidate",forbidden)
    i,p,e,s=make_execution_bundle()
    result=pipeline.execute_transcription(FAKE_PROVIDER_TYPES[0]("permanent_failure"),i,e,profile_snapshot=s)
    assert isinstance(result,ProviderFailure) and not called


def test_candidate_has_no_warning_or_identity_escape_fields():
    assert [f.name for f in fields(ProviderCandidate)] == ["provider_key","language","duration_ms","segments","artifact_refs"]
    with pytest.raises(TypeError): ProviderCandidate(provider_key="fake-alpha",language="zh-CN",duration_ms=1,segments=(),warnings=())


def test_failure_timeout_and_classification_invariants():
    ProviderFailure("fake-alpha",ProviderErrorCode.provider_timeout,ProviderFailureClassification.transient,100)
    with pytest.raises(ContractValidationError):
        ProviderFailure("fake-alpha",ProviderErrorCode.provider_timeout,ProviderFailureClassification.permanent,100)
    with pytest.raises(ContractValidationError):
        ProviderFailure("fake-alpha",ProviderErrorCode.permanent_provider_error,ProviderFailureClassification.permanent,100)
