import sqlite3

import pytest

from api.db import init_db
from api.media_publication_intents import (
    MediaPublicationIntentConflict,
    MediaPublicationIntentStore,
)


def _database(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('admin','Admin','x','admin',1,1)"
    )
    conn.execute(
        """INSERT INTO media_assets(
               media_id,title,original_filename,storage_rel_path,mime_type,file_size,
               transcript_origin,status,created_by,created_at,updated_at
           ) VALUES ('media-1','培训','training.mp4','media/media-1/original.mp4',
                     'video/mp4',10,'generated','uploaded',1,1,1)"""
    )
    conn.commit()
    return conn


def test_schema_contains_media_publication_requests(tmp_path):
    conn = _database(tmp_path)
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(media_publication_requests)")
    }
    assert {
        "id",
        "media_id",
        "request_idempotency_key",
        "status",
        "transcript_version_id",
        "publication_index_job_id",
    } <= columns


def test_create_is_idempotent_and_rejects_another_active_request(tmp_path):
    conn = _database(tmp_path)
    store = MediaPublicationIntentStore(conn)

    created = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-1",
        now=10,
    )
    replayed = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-1",
        now=11,
    )

    assert replayed == created
    assert created.status == "pending_transcription"
    with pytest.raises(MediaPublicationIntentConflict, match="active_request_exists"):
        store.create(
            media_id="media-1",
            requested_by=1,
            request_idempotency_key="request-2",
            now=12,
        )


def test_cancel_only_allows_pre_publication_intents(tmp_path):
    conn = _database(tmp_path)
    store = MediaPublicationIntentStore(conn)
    intent = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-1",
        now=10,
    )

    cancelled = store.cancel(intent.id, now=20)
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at == 20
    # cancel() leaves the transaction open: the caller owns commit/rollback.
    assert conn.in_transaction
    conn.commit()
    assert store.load(intent.id).status == "cancelled"

    # A cancelled intent no longer blocks a fresh publish intent.
    fresh = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-2",
        now=30,
    )
    assert fresh.status == "pending_transcription"


def test_rejects_cancel_of_publishing_intent(tmp_path):
    conn = _database(tmp_path)
    store = MediaPublicationIntentStore(conn)
    intent = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-1",
        now=10,
    )
    intent = store.mark_publishing(
        intent.id,
        transcript_version_id="version-1",
        publication_index_job_id="index-1",
        now=20,
    )
    assert intent.status == "publishing"
    with pytest.raises(MediaPublicationIntentConflict, match="intent_not_cancellable"):
        store.cancel(intent.id, now=30)


def test_publication_transition_links_real_version_and_index_job(tmp_path):
    conn = _database(tmp_path)
    store = MediaPublicationIntentStore(conn)
    intent = store.create(
        media_id="media-1",
        requested_by=1,
        request_idempotency_key="request-1",
        now=10,
    )

    publishing = store.mark_publishing(
        intent.id,
        transcript_version_id="version-1",
        publication_index_job_id="index-1",
        now=20,
    )
    published = store.mark_published(intent.id, now=30)

    assert publishing.status == "publishing"
    assert publishing.transcript_version_id == "version-1"
    assert publishing.publication_index_job_id == "index-1"
    assert published.status == "published"
    assert published.completed_at == 30
