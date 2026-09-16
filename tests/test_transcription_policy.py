from dataclasses import replace
import pytest

from src.transcription.policy import (
    EffectiveReleasePolicy, OrthogonalWorkflowState, effective_release_policy,
    mark_transcription_succeeded, promote_allowed, publication_decision_satisfied,
)
from src.transcription.types import (
    ProfileAdmission, ProfileQualification, PublicationIndexStatus, PublicationStatus,
    ReviewStatus, TranscriptionJobStatus,
)
from tests.transcription_fixture_helpers import CANDIDATE_VERSION_ID, make_execution_bundle, make_profile

SHA="a"*64


def test_transcription_success_changes_only_job_status():
    state=OrthogonalWorkflowState(TranscriptionJobStatus.running,ReviewStatus.awaiting_review,
        PublicationStatus.pending,PublicationIndexStatus.pending)
    result=mark_transcription_succeeded(state)
    assert result == replace(state,job_status=TranscriptionJobStatus.succeeded)


def test_effective_policy_is_stricter_and_disabled_blocks_release():
    _,profile,execution,snapshot=make_execution_bundle(profile=make_profile(qualification=ProfileQualification.experimental))
    policy=effective_release_policy(snapshot,profile)
    assert policy==EffectiveReleasePolicy(True,False,False)
    disabled=replace(profile,admission=ProfileAdmission.disabled)
    assert effective_release_policy(snapshot,disabled)==policy


def test_publication_decision_gate_covers_unified_states():
    # The unified flow allows publishing from pending, rejected and
    # publication_failed (a failed attempt may retry), but never from a
    # never-decided/unknown or already-active state.
    assert publication_decision_satisfied(PublicationStatus.pending)
    assert publication_decision_satisfied(PublicationStatus.rejected)
    assert publication_decision_satisfied(PublicationStatus.publication_failed)
    assert not publication_decision_satisfied(PublicationStatus.publishing)
    assert not publication_decision_satisfied(PublicationStatus.published)


def kwargs():
    return dict(
        effective_policy=EffectiveReleasePolicy(True,False,False),
        current_admission=ProfileAdmission.enabled,explicit_admin_action=True,
        publication_status=PublicationStatus.publishing,index_status=PublicationIndexStatus.done,
        candidate_version_id=CANDIDATE_VERSION_ID,canonical_sha256=SHA,
        markdown_sha256="b"*64,target_index_id="candidate-index")


def test_promote_guard_allows_only_complete_candidate_flow():
    assert promote_allowed(**kwargs())
    variants={
        "current_admission":[ProfileAdmission.disabled],
        "publication_status":[PublicationStatus.pending,PublicationStatus.published],
        "index_status":[PublicationIndexStatus.pending,PublicationIndexStatus.parsing,PublicationIndexStatus.chunking,PublicationIndexStatus.embedding,PublicationIndexStatus.failed],
    }
    for field,values in variants.items():
        for value in values:
            data=kwargs(); data[field]=value
            assert not promote_allowed(**data), (field,value)


@pytest.mark.parametrize("field,value", [("candidate_version_id","bad"),("canonical_sha256","A"*64),("markdown_sha256","x"),("target_index_id","bad\nindex")])
def test_promote_guard_rejects_malformed_controlled_identifiers(field,value):
    from src.transcription.types import ContractValidationError
    data=kwargs(); data[field]=value
    with pytest.raises(ContractValidationError): promote_allowed(**data)