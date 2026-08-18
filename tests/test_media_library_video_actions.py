from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import pytest
from fastapi import HTTPException

from api.auth import CurrentUser
from api.routes_admin import upload_media
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
    for now, stage in enumerate(TranscriptionJobStage, start=53):
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
