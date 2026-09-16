"""Pure guards for orthogonal transcription, review, publication, and index states."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .profile import ProfileSnapshot, ReleasePolicy, TranscriptionProfileDefinition
from .types import (
    ContractValidationError,
    ProfileAdmission,
    PublicationIndexStatus,
    PublicationStatus,
    ReviewStatus,
    TranscriptionJobStatus,
    require_exact_enum,
    require_string,
    validate_sha256,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class EffectiveReleasePolicy:
    requires_review: bool
    auto_publish: bool
    auto_index: bool

    def __post_init__(self) -> None:
        for field in ("requires_review", "auto_publish", "auto_index"):
            if type(getattr(self, field)) is not bool:
                raise ContractValidationError("invalid_boolean", f"effective_policy.{field}")


@dataclass(frozen=True, slots=True)
class OrthogonalWorkflowState:
    job_status: TranscriptionJobStatus
    review_status: ReviewStatus
    publication_status: PublicationStatus
    publication_index_status: PublicationIndexStatus

    def __post_init__(self) -> None:
        require_exact_enum(self.job_status, TranscriptionJobStatus, "job_status")
        require_exact_enum(self.review_status, ReviewStatus, "review_status")
        require_exact_enum(self.publication_status, PublicationStatus, "publication_status")
        require_exact_enum(self.publication_index_status, PublicationIndexStatus, "publication_index_status")


def mark_transcription_succeeded(state: OrthogonalWorkflowState) -> OrthogonalWorkflowState:
    if type(state) is not OrthogonalWorkflowState:
        raise ContractValidationError("invalid_workflow_state", "state")
    return replace(state, job_status=TranscriptionJobStatus.succeeded)


def effective_release_policy(
    snapshot: ProfileSnapshot,
    current_profile: TranscriptionProfileDefinition,
) -> EffectiveReleasePolicy:
    if type(snapshot) is not ProfileSnapshot or type(current_profile) is not TranscriptionProfileDefinition:
        raise ContractValidationError("invalid_policy_input", "policy")
    if snapshot.profile_id != current_profile.profile_id or snapshot.provider_key != current_profile.provider_key:
        raise ContractValidationError("profile_snapshot_mismatch", "current_profile")
    current = current_profile.release_policy
    requires_review = snapshot.release_policy.requires_review or current.requires_review
    auto_publish = snapshot.release_policy.auto_publish and current.auto_publish
    auto_index = snapshot.release_policy.auto_index and current.auto_index
    if current_profile.admission in (ProfileAdmission.deprecated, ProfileAdmission.disabled):
        auto_publish = False
        auto_index = False
    return EffectiveReleasePolicy(requires_review, auto_publish, auto_index)


def publication_decision_satisfied(publication_status: PublicationStatus) -> bool:
    """True when a version has been allowed to publish.

    The unified flow no longer keeps a separate review gate: an admin decision
    moves the version to ``pending`` (allowed, waiting for the publish
    command) or ``rejected`` (declined).  Publishing is only permitted from
    those decision states (or a previous failed attempt, which may retry).
    """
    if type(publication_status) is not PublicationStatus:
        raise ContractValidationError("invalid_decision_gate_input", "publication_gate")
    return publication_status in (
        PublicationStatus.pending,
        PublicationStatus.rejected,
        PublicationStatus.publication_failed,
    )


def promote_allowed(
    *,
    effective_policy: EffectiveReleasePolicy,
    current_admission: ProfileAdmission,
    explicit_admin_action: bool,
    publication_status: PublicationStatus,
    index_status: PublicationIndexStatus,
    candidate_version_id: str,
    canonical_sha256: str,
    markdown_sha256: str,
    target_index_id: str,
) -> bool:
    validate_uuid(candidate_version_id, "candidate_version_id")
    validate_sha256(canonical_sha256, "canonical_sha256")
    validate_sha256(markdown_sha256, "markdown_sha256")
    require_string(target_index_id, "target_index_id")
    require_exact_enum(current_admission, ProfileAdmission, "current_admission")
    if type(explicit_admin_action) is not bool:
        raise ContractValidationError("invalid_boolean", "explicit_admin_action")
    if "\r" in target_index_id or "\n" in target_index_id:
        raise ContractValidationError("invalid_target_index_id", "target_index_id")
    if current_admission is ProfileAdmission.disabled:
        return False
    if current_admission is ProfileAdmission.deprecated and not explicit_admin_action:
        return False
    if type(publication_status) is not PublicationStatus or type(index_status) is not PublicationIndexStatus:
        raise ContractValidationError("invalid_state_type", "promotion")
    return publication_status is PublicationStatus.publishing and index_status is PublicationIndexStatus.done
