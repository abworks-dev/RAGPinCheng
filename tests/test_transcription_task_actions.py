from api.routes_admin import _media_action_state


def actions(*, review_status, publication_status, publication_index_status=None):
    return _media_action_state(
        status="transcript_ready",
        job_status="succeeded",
        job_failure_classification=None,
        review_status=review_status,
        publication_status=publication_status,
        publication_index_status=publication_index_status,
        replacement_status=None,
        storage_kind="managed",
        has_transcript_versions=True,
    )[0]


def test_unpublished_transcripts_can_be_archived_and_approved_transcripts_returned_to_review():
    awaiting = actions(review_status="awaiting_review", publication_status="not_published")
    approved = actions(review_status="review_approved", publication_status="not_published")

    assert "archive_media" in awaiting
    assert "archive_media" in approved
    assert "return_to_review" in approved


def test_publishing_transcripts_keep_destructive_and_review_transitions_protected():
    publishing = actions(
        review_status="review_approved",
        publication_status="publishing",
        publication_index_status="embedding",
    )

    assert "archive_media" not in publishing
    assert "return_to_review" not in publishing
