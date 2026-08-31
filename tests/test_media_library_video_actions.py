from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import pytest
from fastapi import HTTPException

from api.auth import CurrentUser
from api.routes_admin import (
    _media_action_state,
    _media_current_phase,
    delete_failed_media_asset,
    preflight_media_upload,
    upload_media,
)
from api.schemas import MediaUploadPreflightItem, MediaUploadPreflightRequest
from api.transcription_store import StoreConflictError
from src.transcription.formatter import format_transcript
from src.transcription.normalizer import normalize_candidate
from src.transcription.profile import ProfileSnapshot, TranscriptionExecutionConfig
from src.transcription.types import TranscriptionJobStage
from src.transcription.workflow import build_pending_job
from tests.test_transcription_publication_transaction import begin, persist_candidate
from tests.transcription_fixture_helpers import (
    INDEX_JOB_ID,
    VERSION_ID,
    make_candidate,
    make_input_ref,
    seed_admin_user,
)


METADATA_REVISION_ID = "123e4567-e89b-42d3-a456-426614174150"
METADATA_VERSION_ID = "123e4567-e89b-42d3-a456-426614174151"
METADATA_REQUEST_ID = "123e4567-e89b-42d3-a456-426614174152"
METADATA_INDEX_ID = "123e4567-e89b-42d3-a456-426614174153"
REPLACEMENT_ID = "123e4567-e89b-42d3-a456-426614174160"
REPLACEMENT_MEDIA_ID = "123e4567-e89b-42d3-a456-426614174161"
REPLACEMENT_REQUEST_ID = "123e4567-e89b-42d3-a456-426614174162"
REPLACEMENT_JOB_ID = "123e4567-e89b-42d3-a456-426614174163"
REPLACEMENT_JOB_REQUEST_ID = "123e4567-e89b-42d3-a456-426614174164"
REPLACEMENT_VERSION_ID = "123e4567-e89b-42d3-a456-426614174165"
REPLACEMENT_INDEX_ID = "123e4567-e89b-42d3-a456-426614174166"


def test_media_action_projection_matches_mutation_preconditions():
    active, active_disabled = _media_action_state(
        status="transcribing",
        job_status="running",
        review_status=None,
        publication_status=None,
        publication_index_status=None,
    )
    assert "cancel_transcription" in active
    assert "retry_transcription" not in active
    assert "retry_transcription" in active_disabled

    transient, _ = _media_action_state(
        status="failed",
        job_status="failed",
        job_failure_classification="transient",
        review_status=None,
        publication_status=None,
        publication_index_status=None,
    )
    permanent, permanent_disabled = _media_action_state(
        status="failed",
        job_status="failed",
        job_failure_classification="permanent",
        review_status=None,
        publication_status=None,
        publication_index_status=None,
    )
    assert "retry_transcription" in transient
    assert "delete_failed" in transient
    assert "retry_transcription" not in permanent
    assert "retry_transcription" in permanent_disabled

    failed_upload, _ = _media_action_state(
        status="failed",
        job_status=None,
        review_status=None,
        publication_status=None,
        publication_index_status=None,
    )
    assert "delete_failed" in failed_upload

    indexing, indexing_disabled = _media_action_state(
        status="transcript_ready",
        job_status="succeeded",
        review_status="review_approved",
        publication_status="not_published",
        publication_index_status="embedding",
    )
    assert "publish_transcript" not in indexing
    assert indexing_disabled["publish_transcript"] == "转录稿专属索引正在处理"

    replacing, replacing_disabled = _media_action_state(
        status="ready",
        job_status="succeeded",
        review_status="review_approved",
        publication_status="published",
        publication_index_status="done",
        replacement_status="pending",
    )
    assert "replace_media" not in replacing and "archive_media" not in replacing
    assert replacing_disabled["replace_media"] == "视频替换任务正在处理"

    failed_replacement, failed_replacement_disabled = _media_action_state(
        status="failed",
        job_status=None,
        review_status=None,
        publication_status=None,
        publication_index_status=None,
        replacement_status="pending",
    )
    assert "delete_failed" not in failed_replacement
    assert failed_replacement_disabled["delete_failed"] == "视频替换任务正在处理，不能清理"


@pytest.mark.parametrize(
    ("expected", "values"),
    [
        ("failed", dict(status="failed", job_status=None, review_status=None, publication_status=None, publication_index_status=None)),
        ("failed", dict(status="transcript_ready", job_status="succeeded", review_status="review_approved", publication_status="publication_failed", publication_index_status="failed")),
        ("index", dict(status="indexing", job_status="succeeded", review_status="review_approved", publication_status="publishing", publication_index_status="embedding")),
        ("publication", dict(status="transcript_ready", job_status="succeeded", review_status="review_approved", publication_status="publishing", publication_index_status=None)),
        ("ready", dict(status="ready", job_status="succeeded", review_status="review_approved", publication_status="published", publication_index_status=None)),
        ("review", dict(status="transcript_ready", job_status="succeeded", review_status="awaiting_review", publication_status="not_published", publication_index_status=None)),
        ("transcription", dict(status="uploaded", job_status="cancelled", review_status=None, publication_status=None, publication_index_status=None)),
        ("upload", dict(status="uploaded", job_status=None, review_status=None, publication_status=None, publication_index_status=None)),
    ],
)
def test_media_phase_projection(expected, values):
    assert _media_current_phase(**values) == expected


def _published_base(tmp_path):
    conn, store, workflow, _port, profile, base = persist_candidate(tmp_path)
    seed_admin_user(conn)
    begin(workflow, profile)
    workflow.run_publication_index(index_job_id=INDEX_JOB_ID, now=41)
    workflow.promote(
        version_id=VERSION_ID,
        index_job_id=INDEX_JOB_ID,
        current_profile=profile,
        explicit_admin_action=False,
        now=42,
    )
    return conn, store, workflow, profile, base


def _metadata_candidate(store, base, *, now: int = 50):
    return store.register_metadata_revision(
        revision_id=METADATA_REVISION_ID,
        version_id=METADATA_VERSION_ID,
        media_id=base.media_id,
        base_version_id=base.id,
        markdown_ref=base.markdown_ref,
        proposed_title="更新后的培训视频",
        proposed_original_filename="updated-training.mp4",
        requested_by=1,
        request_idempotency_key=METADATA_REQUEST_ID,
        now=now,
    )


def _insert_replacement_media(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO media_assets(
               media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
               transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            REPLACEMENT_MEDIA_ID,
            "候选培训视频",
            "replacement.mp4",
            "replacement/candidate.mp4",
            "video/mp4",
            4096,
            "a" * 64,
            None,
            "generated",
            "uploaded",
            1,
            50,
            50,
            None,
        ),
    )
    conn.commit()


def _replacement_candidate(conn, store, workflow, profile, base):
    _insert_replacement_media(conn)
    source_item_id = conn.execute(
        "SELECT id FROM content_items WHERE media_id=?", (base.media_id,)
    ).fetchone()[0]
    store.register_replacement(
        replacement_id=REPLACEMENT_ID,
        source_media_id=base.media_id,
        candidate_media_id=REPLACEMENT_MEDIA_ID,
        profile_id=profile.profile_id,
        request_idempotency_key=REPLACEMENT_REQUEST_ID,
        requested_by=1,
        now=51,
    )
    input_ref = make_input_ref(media_id=REPLACEMENT_MEDIA_ID)
    execution = TranscriptionExecutionConfig.create(
        profile, input_ref, language="zh-CN", timeout_ms=5_000
    )
    snapshot = ProfileSnapshot.create(profile, execution)
    job = build_pending_job(
        job_id=REPLACEMENT_JOB_ID,
        request_idempotency_key=REPLACEMENT_JOB_REQUEST_ID,
        attempt_number=1,
        input_ref=input_ref,
        execution=execution,
        snapshot=snapshot,
        created_at=52,
    )
    store.create_job(job)
    running = job
    for now, stage in enumerate(
        (
            TranscriptionJobStage.validating_input,
            TranscriptionJobStage.transcribing,
            TranscriptionJobStage.normalizing,
            TranscriptionJobStage.formatting,
        ),
        start=53,
    ):
        running = store.mark_running(
            job.id, stage, expected_updated_at=running.updated_at, now=now
        )
    canonical = normalize_candidate(
        input_ref,
        make_candidate(profile.provider_key),
        snapshot,
        execution,
    )
    candidate = workflow.persist_success(
        job_id=job.id,
        version_id=REPLACEMENT_VERSION_ID,
        canonical=canonical,
        markdown_bytes=format_transcript(canonical, title="候选培训视频"),
        now=70,
    )
    return source_item_id, candidate


def test_failed_replacement_candidate_cannot_be_cleaned_while_replacement_is_active(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    conn, store, _workflow, profile, base = _published_base(tmp_path)
    try:
        _insert_replacement_media(conn)
        store.register_replacement(
            replacement_id=REPLACEMENT_ID,
            source_media_id=base.media_id,
            candidate_media_id=REPLACEMENT_MEDIA_ID,
            profile_id=profile.profile_id,
            request_idempotency_key=REPLACEMENT_REQUEST_ID,
            requested_by=1,
            now=51,
        )
        conn.execute(
            "UPDATE media_assets SET status='failed',error='synthetic' WHERE media_id=?",
            (REPLACEMENT_MEDIA_ID,),
        )
        conn.commit()
        media_root = tmp_path / "media"
        candidate_dir = media_root / REPLACEMENT_MEDIA_ID
        candidate_dir.mkdir(parents=True)
        (candidate_dir / "candidate.mp4").write_bytes(b"synthetic")
        monkeypatch.setattr(routes_admin, "MEDIA_DIR", media_root)

        with pytest.raises(HTTPException) as caught:
            delete_failed_media_asset(REPLACEMENT_MEDIA_ID, None, conn)

        assert caught.value.status_code == 409
        assert caught.value.detail == "视频替换任务正在处理，不能清理"
        assert candidate_dir.exists()
        assert conn.execute(
            "SELECT status FROM media_replacements WHERE id=?", (REPLACEMENT_ID,)
        ).fetchone()[0] == "pending"
    finally:
        conn.close()


def test_metadata_activation_is_atomic_and_preserves_the_old_head_until_success(tmp_path):
    conn, store, workflow, profile, base = _published_base(tmp_path)
    candidate = _metadata_candidate(store, base)
    assert store.current_head(base.media_id) == base.id
    assert tuple(conn.execute(
        "SELECT title,original_filename FROM media_assets WHERE media_id=?", (base.media_id,)
    ).fetchone()) == ("Fixture video", "fixture.mp4")

    store.review_version(
        candidate.id, approved=True, reviewed_by=1, review_note="名称已确认", now=51
    )
    workflow.begin_publication(
        version_id=candidate.id,
        index_job_id=METADATA_INDEX_ID,
        current_profile=None,
        explicit_admin_action=True,
        attempt_number=1,
        now=52,
    )
    workflow.run_publication_index(index_job_id=METADATA_INDEX_ID, now=53)
    conn.execute(
        """CREATE TRIGGER fail_metadata_activation BEFORE UPDATE OF title ON media_assets
           WHEN NEW.title='更新后的培训视频'
           BEGIN SELECT RAISE(ABORT, 'injected metadata activation failure'); END"""
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata activation failure"):
        workflow.promote(
            version_id=candidate.id,
            index_job_id=METADATA_INDEX_ID,
            current_profile=None,
            explicit_admin_action=True,
            now=54,
        )
    assert store.current_head(base.media_id) == base.id
    assert tuple(conn.execute(
        "SELECT title,original_filename FROM media_assets WHERE media_id=?", (base.media_id,)
    ).fetchone()) == ("Fixture video", "fixture.mp4")
    assert conn.execute(
        "SELECT title FROM content_items WHERE media_id=?", (base.media_id,)
    ).fetchone()[0] == "Fixture video"
    assert conn.execute(
        "SELECT status FROM media_metadata_revisions WHERE id=?", (METADATA_REVISION_ID,)
    ).fetchone()[0] == "pending"

    conn.execute("DROP TRIGGER fail_metadata_activation")
    conn.commit()
    published = workflow.promote(
        version_id=candidate.id,
        index_job_id=METADATA_INDEX_ID,
        current_profile=None,
        explicit_admin_action=True,
        now=55,
    )
    assert store.current_head(base.media_id) == candidate.id
    assert published.supersedes_version_id == base.id
    assert tuple(conn.execute(
        "SELECT title,original_filename FROM media_assets WHERE media_id=?", (base.media_id,)
    ).fetchone()) == ("更新后的培训视频", "updated-training.mp4")
    assert conn.execute(
        "SELECT title FROM content_items WHERE media_id=?", (base.media_id,)
    ).fetchone()[0] == "更新后的培训视频"
    assert conn.execute(
        "SELECT status FROM media_metadata_revisions WHERE id=?", (METADATA_REVISION_ID,)
    ).fetchone()[0] == "activated"
    assert store.load_version(base.id).publication_status.value == "published"
    conn.close()


def test_replacement_activation_rolls_back_completely_then_switches_catalog_atomically(tmp_path):
    conn, store, workflow, profile, base = _published_base(tmp_path)
    source_item_id, candidate = _replacement_candidate(
        conn, store, workflow, profile, base
    )
    workflow.begin_publication(
        version_id=candidate.id,
        index_job_id=REPLACEMENT_INDEX_ID,
        current_profile=profile,
        explicit_admin_action=False,
        attempt_number=1,
        now=71,
    )
    workflow.run_publication_index(index_job_id=REPLACEMENT_INDEX_ID, now=72)
    conn.execute(
        f"""CREATE TRIGGER fail_replacement_catalog BEFORE UPDATE OF media_id ON content_items
            WHEN OLD.id='{source_item_id}'
            BEGIN SELECT RAISE(ABORT, 'injected replacement activation failure'); END"""
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected replacement activation failure"):
        workflow.promote(
            version_id=candidate.id,
            index_job_id=REPLACEMENT_INDEX_ID,
            current_profile=profile,
            explicit_admin_action=False,
            now=73,
        )
    assert store.current_head(base.media_id) == base.id
    assert store.current_head(REPLACEMENT_MEDIA_ID) is None
    assert conn.execute(
        "SELECT media_id FROM content_items WHERE id=?", (source_item_id,)
    ).fetchone()[0] == base.media_id
    assert conn.execute(
        "SELECT status FROM media_assets WHERE media_id=?", (base.media_id,)
    ).fetchone()[0] != "archived"
    assert conn.execute(
        "SELECT status FROM media_replacements WHERE id=?", (REPLACEMENT_ID,)
    ).fetchone()[0] == "pending"

    conn.execute("DROP TRIGGER fail_replacement_catalog")
    conn.commit()
    published = workflow.promote(
        version_id=candidate.id,
        index_job_id=REPLACEMENT_INDEX_ID,
        current_profile=profile,
        explicit_admin_action=False,
        now=74,
    )
    assert store.current_head(base.media_id) is None
    assert store.current_head(REPLACEMENT_MEDIA_ID) == candidate.id
    assert published.supersedes_version_id == base.id
    assert tuple(conn.execute(
        "SELECT media_id,title FROM content_items WHERE id=?", (source_item_id,)
    ).fetchone()) == (REPLACEMENT_MEDIA_ID, "候选培训视频")
    assert conn.execute(
        "SELECT status FROM media_assets WHERE media_id=?", (base.media_id,)
    ).fetchone()[0] == "archived"
    assert conn.execute(
        "SELECT status FROM media_replacements WHERE id=?", (REPLACEMENT_ID,)
    ).fetchone()[0] == "activated"
    assert store.load_version(base.id).publication_status.value == "published"
    conn.close()


def test_replacement_is_rejected_while_the_source_has_an_active_publication(tmp_path):
    conn, store, workflow, profile, base = _published_base(tmp_path)
    candidate = _metadata_candidate(store, base)
    store.review_version(
        candidate.id, approved=True, reviewed_by=1, review_note="准备发布", now=51
    )
    workflow.begin_publication(
        version_id=candidate.id,
        index_job_id=METADATA_INDEX_ID,
        current_profile=None,
        explicit_admin_action=True,
        attempt_number=1,
        now=52,
    )
    _insert_replacement_media(conn)

    with pytest.raises(StoreConflictError, match="replacement_source_publishing"):
        store.register_replacement(
            replacement_id=REPLACEMENT_ID,
            source_media_id=base.media_id,
            candidate_media_id=REPLACEMENT_MEDIA_ID,
            profile_id=profile.profile_id,
            request_idempotency_key=REPLACEMENT_REQUEST_ID,
            requested_by=1,
            now=53,
        )
    assert store.current_head(base.media_id) == base.id
    assert conn.execute("SELECT count(*) FROM media_replacements").fetchone()[0] == 0
    conn.close()


def test_replacement_upload_replays_before_active_state_checks_and_binds_the_source(
    tmp_path, monkeypatch
):
    import api.routes_admin as routes_admin

    conn, store, workflow, profile, base = _published_base(tmp_path)
    _source_item_id, candidate = _replacement_candidate(
        conn, store, workflow, profile, base
    )
    video_bytes = b"replacement-video-bytes"
    conn.execute(
        """UPDATE media_assets SET title=?,original_filename=?,file_size=?,sha256=?
           WHERE media_id=?""",
        (
            "Fixture video",
            "replacement.mp4",
            len(video_bytes),
            hashlib.sha256(video_bytes).hexdigest(),
            candidate.media_id,
        ),
    )
    conn.execute(
        "UPDATE transcription_jobs SET created_by=? WHERE id=?",
        (1, REPLACEMENT_JOB_ID),
    )
    conn.commit()

    class Video:
        filename = "replacement.mp4"

        def __init__(self):
            self.sent = False

        async def read(self, _size=-1):
            if self.sent:
                return b""
            self.sent = True
            return video_bytes

    def unexpected_service_build():
        raise AssertionError("idempotent replay must not resolve current ASR configuration")

    monkeypatch.setattr(routes_admin, "ASR_ENABLED", False)
    monkeypatch.setattr(routes_admin, "ASR_SERVICE_TOKEN", "")
    monkeypatch.setattr(routes_admin, "build_transcription_service", unexpected_service_build)
    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")
    replayed = asyncio.run(
        upload_media(
            Video(),
            "Fixture video",
            None,
            profile.profile_id,
            REPLACEMENT_JOB_REQUEST_ID,
            admin,
            conn,
            replacement_source_media_id=base.media_id,
        )
    )
    assert replayed.media_id == candidate.media_id
    assert replayed.transcription_job_id == REPLACEMENT_JOB_ID
    assert conn.execute("SELECT count(*) FROM media_assets").fetchone()[0] == 2

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            upload_media(
                Video(),
                "Fixture video",
                None,
                profile.profile_id,
                REPLACEMENT_JOB_REQUEST_ID,
                admin,
                conn,
                replacement_source_media_id="123e4567-e89b-42d3-a456-426614174199",
            )
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "upload_idempotency_conflict"
    conn.close()


def test_media_upload_preflight_detects_directory_scoped_title_and_filename_conflicts(tmp_path):
    conn, _store, _workflow, _profile, base = _published_base(tmp_path)
    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")

    result = preflight_media_upload(
        MediaUploadPreflightRequest(
            category_id="cat-05",
            items=[
                MediaUploadPreflightItem(
                    client_id="title-match",
                    title=" fixture VIDEO ",
                    original_filename="another.mp4",
                ),
                MediaUploadPreflightItem(
                    client_id="filename-match",
                    title="另一个标题",
                    original_filename="FIXTURE.MP4",
                ),
            ],
        ),
        admin,
        conn,
    )

    assert [entry.status for entry in result.entries] == ["conflict", "conflict"]
    assert result.entries[0].conflicts[0].media_id == base.media_id
    assert result.entries[0].conflicts[0].title_matches is True
    assert result.entries[0].conflicts[0].filename_matches is False
    assert result.entries[1].conflicts[0].filename_matches is True
    assert result.entries[0].suggested_title == "fixture VIDEO (1)"
    assert result.entries[0].suggested_filename == "another (1).mp4"
    conn.close()


def test_media_upload_preflight_reserves_names_for_unpublished_media(tmp_path):
    conn, _store, _workflow, _profile, _base = _published_base(tmp_path)
    conn.execute(
        """INSERT INTO media_assets(
               media_id,title,original_filename,storage_rel_path,mime_type,file_size,sha256,
               transcript_source_path,transcript_origin,status,created_by,created_at,updated_at,error,
               target_category_id
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "123e4567-e89b-42d3-a456-426614174190",
            "处理中视频",
            "processing.mp4",
            "processing/original.mp4",
            "video/mp4",
            10,
            "a" * 64,
            None,
            "generated",
            "uploaded",
            1,
            60,
            60,
            None,
            "cat-05",
        ),
    )
    conn.commit()
    admin = CurrentUser(1, "admin", "Admin", "admin", "csrf")

    result = preflight_media_upload(
        MediaUploadPreflightRequest(
            category_id="cat-05",
            items=[MediaUploadPreflightItem(
                client_id="pending-match",
                title="处理中视频",
                original_filename="new.mp4",
            )],
        ),
        admin,
        conn,
    )

    conflict = result.entries[0].conflicts[0]
    assert result.entries[0].status == "conflict"
    assert conflict.item_id is None
    assert conflict.version_id is None
    conn.close()
