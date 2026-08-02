from dataclasses import replace
import pytest

from src.transcription.policy import (
    EffectiveReleasePolicy, OrthogonalWorkflowState, effective_release_policy,
    mark_transcription_succeeded, promote_allowed, review_gate_satisfied,
)
from src.transcription.types import (
    ProfileAdmission, ProfileQualification, PublicationIndexStatus, PublicationStatus,
    ReviewStatus, TranscriptionJobStatus,
)
from tests.transcription_fixture_helpers import CANDIDATE_VERSION_ID, make_execution_bundle, make_profile

SHA="a"*64


def test_transcription_success_changes_only_job_status():
    state=OrthogonalWorkflowState(TranscriptionJobStatus.running,ReviewStatus.awaiting_review,
        PublicationStatus.not_published,PublicationIndexStatus.pending)
    result=mark_transcription_succeeded(state)
    assert result == replace(state,job_status=TranscriptionJobStatus.succeeded)


def test_effective_policy_is_stricter_and_disabled_blocks_release():
    _,profile,execution,snapshot=make_execution_bundle(profile=make_profile(qualification=ProfileQualification.experimental))
    policy=effective_release_policy(snapshot,profile)
    assert policy==EffectiveReleasePolicy(True,False,False)
    disabled=replace(profile,admission=ProfileAdmission.disabled)
    assert effective_release_policy(snapshot,disabled)==policy


def test_review_gate_is_explicit():
    manual=EffectiveReleasePolicy(True,False,False)
    free=EffectiveReleasePolicy(False,False,False)
    assert review_gate_satisfied(ReviewStatus.review_approved,manual)
    assert not review_gate_satisfied(ReviewStatus.awaiting_review,manual)
    assert review_gate_satisfied(ReviewStatus.not_required,free)


def kwargs():
    return dict(review_status=ReviewStatus.review_approved,
        effective_policy=EffectiveReleasePolicy(True,False,False),
        current_admission=ProfileAdmission.enabled,explicit_admin_action=True,
        publication_status=PublicationStatus.publishing,index_status=PublicationIndexStatus.done,
        candidate_version_id=CANDIDATE_VERSION_ID,canonical_sha256=SHA,
        markdown_sha256="b"*64,target_index_id="candidate-index")


def test_promote_guard_allows_only_complete_candidate_flow():
    assert promote_allowed(**kwargs())
    variants={
        "review_status":[ReviewStatus.awaiting_review,ReviewStatus.review_rejected],
        "current_admission":[ProfileAdmission.disabled],
        "publication_status":[PublicationStatus.not_published,PublicationStatus.published],
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
