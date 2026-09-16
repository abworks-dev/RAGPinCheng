from api.routes_admin import _media_action_state


def actions(*, publication_status, publication_index_status=None):
    return _media_action_state(
        status="transcript_ready",
        job_status="succeeded",
        job_failure_classification=None,
        publication_status=publication_status,
        publication_index_status=publication_index_status,
        replacement_status=None,
        storage_kind="managed",
        has_transcript_versions=True,
    )[0]


def test_unpublished_transcripts_can_be_archived_and_decidable_transcripts_returned_to_pending():
    pending = actions(publication_status="pending")
    rejected = actions(publication_status="rejected")

    assert "archive_media" in pending
    assert "archive_media" in rejected
    assert "return_to_review" in pending
    assert "return_to_review" in rejected
    assert "reject_transcript" in pending


def test_publishing_transcripts_keep_destructive_and_review_transitions_protected():
    publishing = actions(
        publication_status="publishing",
        publication_index_status="embedding",
    )

    assert "archive_media" not in publishing
    assert "return_to_review" not in publishing
    assert "reject_transcript" not in publishing
