from __future__ import annotations

import pytest

from api.transcription_publication import TranscriptionPublicationApplicationService
from api.transcription_store import SQLiteTranscriptionStore, StoreConflictError
from api.transcription_markdown import validate_editable_transcript_markdown
from src.transcription.persistence import ManagedMarkdownRef, MarkdownStorageKind, TranscriptSource
from src.transcription.profile import ProfileRegistry
from src.transcription.types import ContractValidationError, PublicationIndexStatus, ReviewStatus
from tests.test_transcription_publication_transaction import persist_candidate
from tests.transcription_fixture_helpers import make_profile, seed_admin_user


def _service(conn, artifacts, profile, tmp_path):
    ticks = iter(range(100, 200))
    return TranscriptionPublicationApplicationService(
        store=SQLiteTranscriptionStore(conn),
        artifacts=artifacts,
        profiles=ProfileRegistry((profile,)),
        docs_root=tmp_path,
        media_title=lambda _media_id: "Fixture video",
        now=lambda: next(ticks),
    )


def test_revision_is_persisted_idempotently_and_rejects_stale_or_unchanged_content(tmp_path):
    profile = make_profile()
    conn, _store, workflow, _port, profile, base = persist_candidate(tmp_path, profile=profile)
    seed_admin_user(conn)
    service = _service(conn, workflow.artifacts, profile, tmp_path)
    markdown = "# 校对稿\r\n\r\n说话人 1 00:00:01\r\n修订内容\r\n"
    key = "123e4567-e89b-42d3-a456-426614174080"

    revision = service.create_revision(
        base.id,
        markdown=markdown,
        base_markdown_sha256=base.markdown_ref.content_sha256,
        edited_by=1,
        request_idempotency_key=key,
    )
    replay = service.create_revision(
        base.id,
        markdown=markdown,
        base_markdown_sha256=base.markdown_ref.content_sha256,
        edited_by=1,
        request_idempotency_key=key,
    )

    assert replay.id == revision.id
    assert revision.source is TranscriptSource.manual
    assert revision.markdown_storage_kind is MarkdownStorageKind.managed_artifact
    assert revision.derived_from_version_id == base.id
    assert revision.edited_by == 1
    assert revision.review_status is ReviewStatus.awaiting_review
    assert workflow.artifacts.load_verified(revision.markdown_ref).decode("utf-8") == markdown.replace("\r\n", "\n")

    with pytest.raises(StoreConflictError, match="edit_idempotency_conflict"):
        service.create_revision(
            base.id,
            markdown="# 校对稿\n\n说话人 1 00:00:02\n另一内容\n",
            base_markdown_sha256=base.markdown_ref.content_sha256,
            edited_by=1,
            request_idempotency_key=key,
        )
    with pytest.raises(StoreConflictError, match="stale_base_markdown"):
        service.create_revision(
            base.id,
            markdown=markdown,
            base_markdown_sha256="f" * 64,
            edited_by=1,
            request_idempotency_key="123e4567-e89b-42d3-a456-426614174081",
        )
    original = service.preview_markdown(base.id)
    with pytest.raises(StoreConflictError, match="unchanged_markdown"):
        service.create_revision(
            base.id,
            markdown=original,
            base_markdown_sha256=base.markdown_ref.content_sha256,
            edited_by=1,
            request_idempotency_key="123e4567-e89b-42d3-a456-426614174082",
        )
    conn.close()


@pytest.mark.parametrize(
    "markdown,code",
    [
        ("", "empty_transcript_markdown"),
        ("# 只有标题\n", "transcript_turn_required"),
        ("说话人 1 00:61\n正文\n", "invalid_transcript_timestamp"),
        ("说话人 1 00:00:90\n正文\n", "invalid_transcript_timestamp"),
        ("说话人 1 00:00\n\ud800\n", "invalid_markdown_encoding"),
    ],
)
def test_revision_markdown_validation_fails_closed(markdown, code):
    with pytest.raises(ContractValidationError, match=code):
        validate_editable_transcript_markdown(markdown)


def test_revision_markdown_accepts_long_minute_form_and_enforces_byte_limit():
    assert validate_editable_transcript_markdown("说话人 1 90:00\n正文\n")
    with pytest.raises(ContractValidationError, match="transcript_markdown_too_large"):
        validate_editable_transcript_markdown("说话人 1 00:00\n" + "中" * (2 * 1024 * 1024))


def test_reviewed_managed_revision_publishes_without_replacing_head_early(tmp_path, monkeypatch):
    profile = make_profile()
    conn, store, workflow, _port, profile, base = persist_candidate(tmp_path, profile=profile)
    seed_admin_user(conn)
    service = _service(conn, workflow.artifacts, profile, tmp_path)
    monkeypatch.setattr(
        "api.transcription_publication.index_transcript_candidate",
        lambda _doc, on_status: on_status("chunking") or on_status("embedding"),
    )

    base_job = service.publish(base.id)["job"]
    assert base_job is not None
    assert service.run_publication_job(str(base_job["id"])).status is PublicationIndexStatus.done
    assert store.current_head(base.media_id) == base.id

    revision = service.create_revision(
        base.id,
        markdown="# 校对稿\n\n说话人 1 00:00:01\n已修订\n",
        base_markdown_sha256=base.markdown_ref.content_sha256,
        edited_by=1,
        request_idempotency_key="123e4567-e89b-42d3-a456-426614174083",
    )
    with pytest.raises(ContractValidationError, match="manual_revision_review_required"):
        service.publish(revision.id)
    service.review(revision.id, approved=True, reviewed_by=1, review_note="已校对")
    revision_job = service.publish(revision.id)["job"]
    assert revision_job is not None
    assert store.current_head(base.media_id) == base.id
    assert store.load_version(base.id).publication_status.value == "published"

    assert service.run_publication_job(str(revision_job["id"])).status is PublicationIndexStatus.done
    assert store.current_head(base.media_id) == revision.id
    published = store.load_version(revision.id)
    assert published.supersedes_version_id == base.id
    assert store.load_version(base.id).publication_status.value == "published"
    conn.close()


def test_legacy_manual_version_remains_unpublishable(tmp_path):
    profile = make_profile()
    conn, store, workflow, _port, profile, base = persist_candidate(tmp_path, profile=profile)
    legacy = store.register_manual_version(
        version_id="123e4567-e89b-42d3-a456-426614174084",
        media_id=base.media_id,
        markdown_ref=ManagedMarkdownRef("docs/教学视频/legacy.md", "d" * 64, 10),
        initial_review_status=ReviewStatus.awaiting_review,
        now=50,
    )
    service = _service(conn, workflow.artifacts, profile, tmp_path)
    with pytest.raises(ContractValidationError, match="manual_publication_not_connected"):
        service.publish(legacy.id)
    conn.close()
