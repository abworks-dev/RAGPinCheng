"""Upload lifecycle regressions using only temporary SQLite/storage and fake media."""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from api import content_store, routes_admin, routes_content
from api.auth import CurrentUser
from api.db import connect
from tests.test_content_library_api import _auth, content_api  # noqa: F401


@pytest.fixture(autouse=True)
def no_external_upload_work(monkeypatch):
    # A regression must not accidentally enqueue real indexing/transcription work.
    def forbidden(*_args, **_kwargs):
        pytest.fail("unexpected external publication work")

    async def forbidden_media(**_kwargs):
        pytest.fail("unexpected real media upload")

    monkeypatch.setattr(routes_content, "enqueue_content_publication", forbidden)
    monkeypatch.setattr(routes_admin, "upload_media", forbidden_media)


def _post_upload(content_api, filenames, **data):
    client, sessions, _queued, _db_path = content_api
    return client.post(
        "/api/admin/content/uploads",
        data={"category_id": "cat-03", **data},
        files=[("files", (name, f"synthetic {name}".encode(), "application/octet-stream"))
               for name in filenames],
        **_auth(sessions, "admin", csrf=True),
    )


def _task(content_api, batch_id):
    client, sessions, _queued, _db_path = content_api
    response = client.get(
        f"/api/admin/content/upload-tasks/{batch_id}", **_auth(sessions, "admin")
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("failed_index", [0, 1])
def test_document_storage_failure_is_safe_and_preserves_other_files(
    content_api, monkeypatch, failed_index
):
    storage = routes_content._storage
    original = storage.ingest_upload
    calls = 0

    async def fail_one(upload, **kwargs):
        nonlocal calls
        current = calls
        calls += 1
        if current == failed_index:
            raise OSError("sensitive storage path /private/customer")
        return await original(upload, **kwargs)

    monkeypatch.setattr(storage, "ingest_upload", fail_one)
    response = _post_upload(content_api, ["first.md", "second.md", "third.md"])
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert [entry["status"] for entry in entries] == [
        "skipped" if index == failed_index else "accepted" for index in range(3)
    ]
    assert entries[failed_index]["reason_code"] == "content_storage_unavailable"
    assert "sensitive" not in response.text and "/private" not in response.text
    task = _task(content_api, response.json()["batch_id"])
    assert (task["status"], task["accepted_files"], task["skipped_files"]) == (
        "partial_success", 2, 1
    )
    assert task["total_uploaded_bytes"] == sum(
        len(f"synthetic {entry['filename']}".encode())
        for entry in entries if entry["status"] == "accepted"
    )
    with connect(content_api[3]) as conn:
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 2
        assert conn.execute("SELECT status FROM upload_batches").fetchone()[0] == "ready_for_review"


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, sqlite3.IntegrityError])
def test_document_database_error_rolls_back_before_failure_bookkeeping(
    content_api, monkeypatch, error_type
):
    original = routes_content.register_uploaded_document
    record = routes_content.record_upload_batch_entry

    def fail_middle(conn, **kwargs):
        if kwargs["original_filename"] == "second.md":
            conn.execute("UPDATE upload_batches SET error_summary='sensitive db state'")
            raise error_type("sensitive database exception /private/app.sqlite")
        return original(conn, **kwargs)

    def verify_rollback(conn, **kwargs):
        if kwargs["status"] == "skipped":
            assert not conn.in_transaction
            assert conn.execute("SELECT error_summary FROM upload_batches").fetchone()[0] is None
        return record(conn, **kwargs)

    monkeypatch.setattr(routes_content, "register_uploaded_document", fail_middle)
    monkeypatch.setattr(routes_content, "record_upload_batch_entry", verify_rollback)
    response = _post_upload(content_api, ["first.md", "second.md", "third.md"])
    assert response.status_code == 200, response.text
    assert [entry["status"] for entry in response.json()["entries"]] == [
        "accepted", "skipped", "accepted"
    ]
    assert response.json()["entries"][1]["reason_code"] == "content_database_unavailable"
    assert "sensitive" not in response.text and "/private" not in response.text
    task = _task(content_api, response.json()["batch_id"])
    assert (task["status"], task["accepted_files"], task["skipped_files"]) == (
        "partial_success", 2, 1
    )
    assert task["entries"][1]["failure_code"] == "content_database_unavailable"
    assert len(list(routes_content._storage.objects_root.glob("*/*"))) == 2


@pytest.mark.parametrize("after_commit", [False, True])
def test_bookkeeping_failure_never_returns_success_or_duplicate_skipped_outcome(
    content_api, monkeypatch, after_commit
):
    original = routes_content.record_upload_batch_entry

    def fail_second(conn, **kwargs):
        if kwargs["sequence"] == 2:
            if after_commit:
                original(conn, **kwargs)
            else:
                conn.execute("UPDATE upload_batches SET error_summary='sensitive uncommitted'")
            raise sqlite3.OperationalError("sensitive bookkeeping failure")
        return original(conn, **kwargs)

    monkeypatch.setattr(routes_content, "record_upload_batch_entry", fail_second)
    response = _post_upload(content_api, ["first.md", "second.md", "third.md"])
    assert response.status_code == 503, response.text
    assert "sensitive" not in response.text
    with connect(content_api[3]) as conn:
        batch = conn.execute("SELECT * FROM upload_batches").fetchone()
        assert batch["status"] == "failed"
        assert batch["accepted_files"] == (2 if after_commit else 1)
        assert batch["skipped_files"] == 0
        assert "sensitive" not in batch["error_summary"]
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 2
        rows = conn.execute("SELECT sequence,status FROM upload_batch_entries ORDER BY sequence").fetchall()
        assert [tuple(row) for row in rows] == [(1, "accepted")] + (
            [(2, "accepted")] if after_commit else []
        )
        batch_id = batch["id"]
    assert _task(content_api, batch_id)["status"] == "failed"


@pytest.mark.parametrize("failed_phase", ["registration", "enqueue"])
def test_publication_failure_keeps_durable_accepted_upload_outcome(
    content_api, monkeypatch, failed_phase
):
    if failed_phase == "registration":
        def fail_registration(conn, *_args, **_kwargs):
            conn.execute("UPDATE upload_batches SET error_summary='sensitive uncommitted publication'")
            raise ValueError("sensitive publication registration")

        monkeypatch.setattr(routes_content, "create_publication_job", fail_registration)
    else:
        def fail_enqueue(*_args, **_kwargs):
            raise RuntimeError("sensitive publication queue")

        monkeypatch.setattr(routes_content, "enqueue_content_publication", fail_enqueue)
    response = _post_upload(content_api, ["first.md", "second.md"], publish=["true", "false"])
    assert response.status_code == 503, response.text
    assert "sensitive" not in response.text
    with connect(content_api[3]) as conn:
        batch = conn.execute("SELECT * FROM upload_batches").fetchone()
        assert (batch["status"], batch["accepted_files"], batch["skipped_files"]) == ("failed", 1, 0)
        assert "sensitive" not in batch["error_summary"]
        entries = conn.execute("SELECT * FROM upload_batch_entries").fetchall()
        assert len(entries) == 1 and entries[0]["status"] == "accepted"
        assert entries[0]["item_id"] and entries[0]["version_id"]
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM content_items WHERE archived_at IS NULL").fetchone()[0] == 1
        batch_id = batch["id"]
    assert _task(content_api, batch_id)["status"] == "failed"


@pytest.mark.parametrize("filenames", [
    ["first.md", "second.md"],
    ["first.mp4", "second.mp4"],
    ["first.md", "second.mp4"],
    ["first.mp4", "second.md"],
])
def test_all_upload_kinds_finalize_only_after_last_outcome(content_api, monkeypatch, filenames):
    observations = []

    def observe(conn):
        row = conn.execute("SELECT * FROM upload_batches").fetchone()
        task, _entries = content_store.get_upload_task(
            conn, row["id"], user_id=row["created_by"], is_admin=True
        )
        observations.append((row["status"], row["accepted_files"], task["task_status"]))

    original = routes_content.register_uploaded_document

    def register(conn, **kwargs):
        observe(conn)
        result = original(conn, **kwargs)
        # Creating one document must not independently mark a counted web batch ready.
        observe(conn)
        return result

    async def media(**kwargs):
        observe(kwargs["conn"])
        # The admission adapter is mocked; no media storage/network/queue is touched.
        return SimpleNamespace(media_id=None, transcription_job_id=None)

    monkeypatch.setattr(routes_content, "register_uploaded_document", register)
    monkeypatch.setattr(routes_admin, "upload_media", media)
    response = _post_upload(content_api, filenames)
    assert response.status_code == 200, response.text
    assert all(entry["status"] == "accepted" for entry in response.json()["entries"])
    assert observations and all(status == "staging" and task_status == "processing"
                                for status, _accepted, task_status in observations)
    assert any(accepted == 1 for _status, accepted, _task_status in observations)
    task = _task(content_api, response.json()["batch_id"])
    assert (task["status"], task["accepted_files"], task["skipped_files"]) == ("completed", 2, 0)
    with connect(content_api[3]) as conn:
        assert conn.execute("SELECT status FROM upload_batches").fetchone()[0] == "ready_for_review"


def test_revision_does_not_finalize_batch_before_remaining_uploads(content_api, monkeypatch):
    first = _post_upload(content_api, ["guide.md"]).json()["entries"][0]
    original = routes_content.create_content_revision
    observed = []

    def revision(conn, item_id, **kwargs):
        result = original(conn, item_id, **kwargs)
        row = conn.execute("SELECT * FROM upload_batches ORDER BY rowid DESC LIMIT 1").fetchone()
        observed.append((row["status"], row["accepted_files"], row["skipped_files"]))
        return result

    monkeypatch.setattr(routes_content, "create_content_revision", revision)
    response = _post_upload(
        content_api, ["guide.md", "other.md"],
        conflict_actions=[json.dumps({
            "strategy": "update", "item_id": first["item_id"],
            "expected_version_id": first["version_id"],
        }), json.dumps({"strategy": "create"})],
    )
    assert response.status_code == 200, response.text
    assert observed == [("staging", 0, 0)]
    assert response.json()["entries"][0]["resolution"] == "updated"
    task = _task(content_api, response.json()["batch_id"])
    assert (task["status"], task["accepted_files"], task["skipped_files"]) == ("completed", 2, 0)


def test_outcome_and_aggregate_update_roll_back_atomically_on_real_sql_failure(content_api):
    with connect(content_api[3]) as conn:
        user_id = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        batch_id = content_store.create_web_batch(
            conn, actor_user_id=user_id, target_category_id="cat-03", total_files=2
        )
        conn.execute("""CREATE TRIGGER fail_upload_counts BEFORE UPDATE ON upload_batches
                        WHEN NEW.accepted_files > 0
                        BEGIN SELECT RAISE(ABORT, 'synthetic aggregate failure'); END""")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="synthetic aggregate failure"):
            content_store.record_upload_batch_entry(
                conn, batch_id=batch_id, sequence=1, filename="synthetic.md",
                relative_path=None, size_bytes=12, status="accepted",
            )
        assert not conn.in_transaction
        assert conn.execute("SELECT count(*) FROM upload_batch_entries").fetchone()[0] == 0
        row = conn.execute("SELECT * FROM upload_batches WHERE id=?", (batch_id,)).fetchone()
        assert (row["status"], row["accepted_files"], row["total_uploaded_bytes"]) == ("staging", 0, 0)
        # Abort reconciliation remains possible after the failed atomic outcome.
        content_store.finalize_upload_batch(conn, batch_id, failed=True)
        assert conn.execute("SELECT status FROM upload_batches WHERE id=?", (batch_id,)).fetchone()[0] == "failed"


def test_all_skipped_batch_is_terminal_and_has_exact_counts(content_api):
    response = _post_upload(content_api, ["first.exe", "second.exe"])
    assert response.status_code == 200, response.text
    task = _task(content_api, response.json()["batch_id"])
    assert (task["status"], task["accepted_files"], task["skipped_files"]) == ("failed", 0, 2)
    assert task["total_uploaded_bytes"] == 0
    with connect(content_api[3]) as conn:
        row = conn.execute("SELECT status,error_summary FROM upload_batches").fetchone()
        assert tuple(row) == ("failed", "没有可接收的文件")


def test_incomplete_ready_legacy_row_stays_processing_unless_explicitly_failed(content_api):
    with connect(content_api[3]) as conn:
        user_id = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        batch_id = content_store.create_web_batch(
            conn, actor_user_id=user_id, target_category_id="cat-03", total_files=3
        )
        conn.execute("UPDATE upload_batches SET status='ready_for_review' WHERE id=?", (batch_id,))
        conn.commit()
    assert _task(content_api, batch_id)["status"] == "processing"
    with connect(content_api[3]) as conn:
        content_store.finalize_upload_batch(conn, batch_id, failed=True)
    assert _task(content_api, batch_id)["status"] == "failed"


def test_cancellation_finalizes_only_current_batch_and_preserves_earlier_success(content_api, monkeypatch):
    original = routes_content._storage.ingest_upload
    calls = 0

    async def cancel_second(upload, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()
        return await original(upload, **kwargs)

    monkeypatch.setattr(routes_content._storage, "ingest_upload", cancel_second)
    conn = connect(content_api[3])
    uploads = [UploadFile(filename=name, file=io.BytesIO(b"synthetic " + name.encode()))
               for name in ("first.md", "second.md")]
    try:
        user_id = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        unrelated = content_store.create_web_batch(
            conn, actor_user_id=user_id, target_category_id="cat-03", total_files=2
        )
        user = CurrentUser(user_id, "admin", "synthetic admin", "admin", "unused")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(routes_content.upload_managed_documents(
                files=uploads, category_id="cat-03", relative_paths=None, upload_mode="files",
                allow_folder_merge=False, video_scheme_id=None, video_idempotency_keys=None,
                publish=None, conflict_actions=None, user=user, conn=conn,
            ))
        batches = conn.execute("SELECT * FROM upload_batches ORDER BY rowid").fetchall()
        assert batches[0]["id"] == unrelated and batches[0]["status"] == "staging"
        assert batches[1]["status"] == "failed"
        assert (batches[1]["accepted_files"], batches[1]["skipped_files"]) == (1, 0)
        assert conn.execute("SELECT count(*) FROM content_versions").fetchone()[0] == 1
    finally:
        conn.close()
        for upload in uploads:
            upload.file.close()
