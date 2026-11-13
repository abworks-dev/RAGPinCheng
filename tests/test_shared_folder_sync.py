"""Shared-folder synchronisation: withdrawal, restore, generation guard, cleanup.

These tests exercise the state machine directly against a real SQLite schema so
that the invariants from the approved plan are checked where they are enforced:
the head transaction, the withdrawal table and the nightly scheduler.

Deliberately *not* covered here: real SMB latency, MinerU/embedding throughput
and a full 01:00–06:30 calendar window.  Those need a deployment environment.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api import content_attachments, content_publication, content_trash_cleanup, shared_sync
from api.content_storage import ContentStorage, StoredContentObject
from api.content_store import create_category, create_publication_job, create_web_batch, register_uploaded_document
from api.db import connect, init_db
from api.external_document_import import import_source_documents
from api.external_media import reconcile_source
from api.shared_sync import (
    CANDIDATE_COALESCED,
    CANDIDATE_READY,
    CANDIDATE_WAITING,
    STATE_PURGED,
    STATE_WITHDRAWN,
    SharedSyncLeaseBusy,
    claim_withdrawal,
    withdraw_missing_and_superseded,
    import_ready_entries,
    observe_candidate,
    pending_cleanup_candidates,
    prune_expired_logs,
    purge_withdrawal,
    reap_expired_runs,
    restore_available_entries,
    start_run,
    finish_run,
    withdraw_published_head,
)
from api.shared_sync_scheduler import PHASE_DAY, PHASE_QUIET, PHASE_WINDOW, _in_window, phase_for, sync_source
from src.content_retrieval_visibility import SQLitePublishedContentVisibility
from src.indexing_pipeline import IndexResult


# --------------------------------------------------------------------------- #
# Environment helpers
# --------------------------------------------------------------------------- #
class SyncEnv:
    def __init__(self, conn: sqlite3.Connection, source_id: str, share: Path, db_path: Path,
                 storage: ContentStorage) -> None:
        self.conn = conn
        self.source_id = source_id
        self.share = share
        self.db_path = db_path
        self.storage = storage


@pytest.fixture
def sync_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SyncEnv:
    share = tmp_path / "share"
    share.mkdir()
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)"
        " VALUES (1,'admin','Admin','x','admin',1,1)"
    )
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)"
        " VALUES (2,'reader','Reader','x','user',1,1)"
    )
    root = create_category(
        conn, category_key=None, parent_id=None, display_code="90",
        display_name="共享同步测试", sort_order=900, actor_user_id=1,
    )
    scheme = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()
    assert scheme is not None
    source_id = "source-sync-1"
    conn.execute(
        """INSERT INTO external_media_sources(
               id,name,root_alias,relative_path,target_category_id,default_scheme_id,
               created_at,updated_at,created_by,document_import_mode,scan_interval_seconds
           ) VALUES (?,?,?,?,?,?,?,?,1,'auto',60)""",
        (source_id, "共享同步源", "share", "", root["id"], scheme["id"], 1, 1),
    )
    conn.execute(
        """UPDATE category_nodes SET category_kind='shared_folder', external_source_id=?,
                  version=version+1 WHERE id=?""",
        (source_id, root["id"]),
    )
    conn.commit()

    storage = ContentStorage(tmp_path / "content")
    storage.ensure_layout()
    import api.external_document_import as document_import

    monkeypatch.setattr(document_import, "_storage", storage)
    monkeypatch.setattr(shared_sync, "_storage", storage)
    monkeypatch.setattr(content_trash_cleanup, "_storage", storage)
    monkeypatch.setattr(content_publication, "_storage", storage)
    monkeypatch.setattr(content_trash_cleanup, "PARENTS_DB", tmp_path / "parents.sqlite")
    monkeypatch.setattr(content_publication, "connect", lambda: connect(db_path))
    monkeypatch.setattr(content_publication, "index_managed_content", _fake_index)
    return SyncEnv(conn, source_id, share, db_path, storage)


def _fake_index(_path, _doc_type, _metadata, on_status):
    on_status("uploading")
    on_status("parsing")
    on_status("embedding")
    return IndexResult(parents=1, children=1)


class _NoQdrant:
    def collection_exists(self, _name: str) -> bool:
        return False


def _write_document(path: Path, text: str) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    info = path.stat()
    return info.st_atime_ns, info.st_mtime_ns


def _publish(env: SyncEnv, version_id: str, *, generation: int | None = None,
             entry_id: str | None = None) -> str:
    _publication_id, job_id = create_publication_job(
        env.conn, version_id, actor_user_id=1,
        source_generation=generation, shared_entry_id=entry_id,
    )
    content_publication.run_content_publication(job_id)
    return job_id


def _scan(env: SyncEnv, *, now: int = 100) -> None:
    reconcile_source(env.conn, env.source_id, trigger_type="manual",
                     roots={"share": env.share}, now=now)


def _entry(env: SyncEnv, relative_path: str) -> sqlite3.Row:
    return env.conn.execute(
        "SELECT * FROM external_media_entries WHERE source_id=? AND relative_path=? AND availability<>'superseded'"
        " ORDER BY discovered_at DESC LIMIT 1",
        (env.source_id, relative_path),
    ).fetchone()


def _head(env: SyncEnv, item_id: str) -> str | None:
    row = env.conn.execute(
        "SELECT current_version_id FROM content_item_heads WHERE item_id=?", (item_id,)
    ).fetchone()
    return str(row["current_version_id"]) if row else None


def _visible(env: SyncEnv, version_id: str) -> bool:
    return SQLitePublishedContentVisibility(env.db_path, "strict").snapshot().allows(version_id)


def _publish_document(env: SyncEnv, relative_path: str, text: str) -> tuple[str, str]:
    """Import one shared document inline and publish it, returning (item, version)."""
    _write_document(env.share / relative_path, text)
    _scan(env)
    entry = _entry(env, relative_path)
    result = import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    assert (result.imported, result.failed) == (1, 0)
    entry = _entry(env, relative_path)
    assert entry["import_status"] == "imported"
    item_id = str(entry["content_item_id"])
    version_id = str(entry["content_version_id"])
    _publish(env, version_id, generation=int(entry["sync_generation"]), entry_id=str(entry["id"]))
    assert _head(env, item_id) == version_id
    return item_id, version_id


# --------------------------------------------------------------------------- #
# Withdrawal / restore
# --------------------------------------------------------------------------- #
def test_missing_file_withdraws_head_and_identical_return_restores_it(sync_env: SyncEnv) -> None:
    env = sync_env
    item_id, version_id = _publish_document(env, "规范/设计标准.md", "# 设计标准\n\n正文\n")
    assert _visible(env, version_id)
    path = env.share / "规范" / "设计标准.md"
    atime_ns, mtime_ns = path.stat().st_atime_ns, path.stat().st_mtime_ns

    path.unlink()
    _scan(env, now=200)
    entry = _entry(env, "规范/设计标准.md")
    assert entry["availability"] == "missing"
    assert withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210) == 1

    # The head is gone, so neither retrieval nor an old citation can serve it,
    # while the version, its object and its index artifacts are all kept.
    assert _head(env, item_id) is None
    assert not _visible(env, version_id)
    withdrawal = env.conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE version_id=?", (version_id,)
    ).fetchone()
    assert withdrawal["state"] == STATE_WITHDRAWN
    assert withdrawal["reason"] == "shared_missing"
    assert env.conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "published"
    assert env.conn.execute(
        "SELECT status FROM content_publications WHERE version_id=?", (version_id,)
    ).fetchone()[0] == "withdrawn"
    assert content_attachments.version_withdrawn(env.conn, version_id)
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_index_jobs WHERE version_id=?", (version_id,)
    ).fetchone()[0] == 1

    # Put exactly the same bytes back with the original mtime: the scan
    # recovers the *same* fingerprint, so the head can be rebuilt without
    # re-parsing anything.
    path.write_text("# 设计标准\n\n正文\n", encoding="utf-8")
    os.utime(path, ns=(atime_ns, mtime_ns))
    _scan(env, now=300)
    assert _entry(env, "规范/设计标准.md")["availability"] == "available"
    assert restore_available_entries(env.conn, source_id=env.source_id, now=310) == 1

    assert _head(env, item_id) == version_id
    assert _visible(env, version_id)
    assert env.conn.execute(
        "SELECT state FROM content_head_withdrawals WHERE id=?", (withdrawal["id"],)
    ).fetchone()[0] == "restored"
    assert env.conn.execute(
        "SELECT status FROM content_publications WHERE id=?", (withdrawal["publication_id"],)
    ).fetchone()[0] == "published"
    assert not content_attachments.version_withdrawn(env.conn, version_id)


def test_replacement_withdraws_old_head_and_keeps_new_version_pending(sync_env: SyncEnv) -> None:
    env = sync_env
    item_id, old_version = _publish_document(env, "资料/方案.md", "# 方案\n\n旧版\n")
    path = env.share / "资料" / "方案.md"

    path.write_text("# 方案\n\n新版\n", encoding="utf-8")
    _scan(env, now=200)
    superseded = env.conn.execute(
        "SELECT availability,sync_generation FROM external_media_entries WHERE content_version_id=?",
        (old_version,),
    ).fetchone()
    assert superseded["availability"] == "superseded"

    assert withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210) == 1
    assert _head(env, item_id) is None
    assert not _visible(env, old_version)
    withdrawal = env.conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE version_id=?", (old_version,)
    ).fetchone()
    assert withdrawal["reason"] == "shared_replaced"

    # The replacement re-uses the item so revision history stays together, but
    # it arrives as a *new* pending version instead of silently overwriting the
    # withdrawn one.
    entry = _entry(env, "资料/方案.md")
    assert str(entry["content_item_id"]) == item_id
    result = import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    assert (result.imported, result.failed) == (1, 0)
    entry = _entry(env, "资料/方案.md")
    new_version = str(entry["content_version_id"])
    assert new_version != old_version
    assert env.conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (new_version,)
    ).fetchone()[0] == "pending_publication"
    # Day-time behaviour: registered, not published.
    assert _head(env, item_id) is None


def test_generation_guard_refuses_a_stale_publication_job(sync_env: SyncEnv) -> None:
    env = sync_env
    item_id, old_version = _publish_document(env, "资料/说明.md", "# 说明\n\n一\n")
    path = env.share / "资料" / "说明.md"
    old_generation = int(_entry(env, "资料/说明.md")["sync_generation"])

    path.write_text("# 说明\n\n二\n", encoding="utf-8")
    _scan(env, now=200)
    withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210)
    entry = _entry(env, "资料/说明.md")
    import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    entry = _entry(env, "资料/说明.md")
    new_version = str(entry["content_version_id"])
    new_generation = int(entry["sync_generation"])
    assert new_generation > old_generation

    # A job recorded against the pre-replacement generation must never become
    # the head, even though its version and entry are still valid.
    job_id = _publish(env, new_version, generation=old_generation, entry_id=str(entry["id"]))
    failed = env.conn.execute(
        "SELECT status,error_code FROM content_index_jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert (failed["status"], failed["error_code"]) == ("failed", "shared_entry_superseded")
    assert _head(env, item_id) is None
    assert not _visible(env, new_version)
    assert env.conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (new_version,)
    ).fetchone()[0] == "publication_failed"

    # Retrying with the current generation publishes normally.
    retry = _publish(env, new_version, generation=new_generation, entry_id=str(entry["id"]))
    assert env.conn.execute(
        "SELECT status FROM content_index_jobs WHERE id=?", (retry,)
    ).fetchone()[0] == "done"
    assert _head(env, item_id) == new_version
    assert _visible(env, new_version)
    assert env.conn.execute(
        "SELECT lifecycle_status FROM content_versions WHERE id=?", (old_version,)
    ).fetchone()[0] == "superseded"


def test_restore_is_refused_once_cleanup_claimed(sync_env: SyncEnv) -> None:
    env = sync_env
    item_id, version_id = _publish_document(env, "规范/回滚.md", "# 回滚\n\n正文\n")
    path = env.share / "规范" / "回滚.md"
    atime_ns, mtime_ns = path.stat().st_atime_ns, path.stat().st_mtime_ns
    path.unlink()
    _scan(env, now=200)
    withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210)
    withdrawal = env.conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE version_id=?", (version_id,)
    ).fetchone()

    run_id = start_run(env.conn, env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone(), trigger_type="nightly", now=300)
    assert claim_withdrawal(env.conn, str(withdrawal["id"]), run_id=run_id, now=310)

    # Same bytes are back, but the cleanup already owns the version: restore is
    # refused and the head stays empty.
    path.write_text("# 回滚\n\n正文\n", encoding="utf-8")
    os.utime(path, ns=(atime_ns, mtime_ns))
    _scan(env, now=320)
    assert restore_available_entries(env.conn, source_id=env.source_id, now=330) == 0
    assert _head(env, item_id) is None
    assert not _visible(env, version_id)


# --------------------------------------------------------------------------- #
# Nightly cleanup (GRACE = 0, as approved)
# --------------------------------------------------------------------------- #
def test_purge_deletes_knowledge_base_copy_only(sync_env: SyncEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    env = sync_env
    monkeypatch.setattr(content_trash_cleanup, "_client", lambda: _NoQdrant())
    item_id, version_id = _publish_document(env, "资料/待清理.md", "# 待清理\n\n正文\n")
    object_rel = env.conn.execute(
        "SELECT o.storage_rel_path FROM content_versions v JOIN content_objects o"
        " ON o.sha256=v.object_sha256 WHERE v.id=?", (version_id,)
    ).fetchone()[0]
    object_path = env.storage.resolve_object(str(object_rel))
    assert object_path.is_file()

    (env.share / "资料" / "待清理.md").unlink()
    _scan(env, now=200)
    withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210)

    run_id = start_run(env.conn, env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone(), trigger_type="nightly", now=300)
    candidates = pending_cleanup_candidates(
        env.conn, source_id=env.source_id, now=310, grace_minutes=0
    )
    assert [str(row["version_id"]) for row in candidates] == [version_id]
    withdrawal_id = str(candidates[0]["id"])
    assert claim_withdrawal(env.conn, withdrawal_id, run_id=run_id, now=310)
    assert purge_withdrawal(env.conn, withdrawal_id, run_id=run_id, now=320)

    assert env.conn.execute(
        "SELECT state FROM content_head_withdrawals WHERE id=?", (withdrawal_id,)
    ).fetchone()[0] == STATE_PURGED
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == 0
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_publications WHERE version_id=?", (version_id,)
    ).fetchone()[0] == 0
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_items WHERE id=?", (item_id,)
    ).fetchone()[0] == 0
    assert not object_path.exists()
    # Nothing was ever written back to the share; it is simply still gone.
    assert not (env.share / "资料" / "待清理.md").exists()


def test_purge_keeps_item_when_other_versions_survive(sync_env: SyncEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    env = sync_env
    monkeypatch.setattr(content_trash_cleanup, "_client", lambda: _NoQdrant())
    item_id, first = _publish_document(env, "资料/多版本.md", "# 多版本\n\n一\n")
    path = env.share / "资料" / "多版本.md"
    path.write_text("# 多版本\n\n二\n", encoding="utf-8")
    _scan(env, now=200)
    withdraw_missing_and_superseded(env.conn, source_id=env.source_id, now=210)
    entry = _entry(env, "资料/多版本.md")
    import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    entry = _entry(env, "资料/多版本.md")
    second = str(entry["content_version_id"])
    _publish(env, second, generation=int(entry["sync_generation"]), entry_id=str(entry["id"]))
    assert _head(env, item_id) == second

    run_id = start_run(env.conn, env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone(), trigger_type="nightly", now=300)
    withdrawal = env.conn.execute(
        "SELECT * FROM content_head_withdrawals WHERE version_id=? AND state='withdrawn'", (first,)
    ).fetchone()
    assert claim_withdrawal(env.conn, str(withdrawal["id"]), run_id=run_id, now=310)
    assert purge_withdrawal(env.conn, str(withdrawal["id"]), run_id=run_id, now=320)

    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_items WHERE id=?", (item_id,)
    ).fetchone()[0] == 1
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_versions WHERE id=?", (second,)
    ).fetchone()[0] == 1
    assert _head(env, item_id) == second


# --------------------------------------------------------------------------- #
# Log retention, lease, windows, candidates
# --------------------------------------------------------------------------- #
def test_log_retention_only_drops_finished_old_runs(sync_env: SyncEnv) -> None:
    env = sync_env
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    old = start_run(env.conn, source, trigger_type="nightly", now=1_000)
    finish_run(env.conn, old, status="succeeded", counts={"imported_count": 2}, now=1_100)
    fresh = start_run(env.conn, source, trigger_type="manual", now=int(time.time()))
    finish_run(env.conn, fresh, status="succeeded", now=int(time.time()))

    removed = prune_expired_logs(env.conn, retention_days=30, now=1_000 + 31 * 86400)
    assert removed["runs"] == 1
    assert env.conn.execute(
        "SELECT COUNT(*) FROM shared_sync_runs WHERE id=?", (old,)
    ).fetchone()[0] == 0
    assert env.conn.execute(
        "SELECT COUNT(*) FROM shared_sync_runs WHERE id=?", (fresh,)
    ).fetchone()[0] == 1
    # Version/object/index retention is independent of the log.
    assert env.conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0] == 0


def test_lease_blocks_a_second_run_and_reaps_a_dead_one(sync_env: SyncEnv) -> None:
    env = sync_env
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    run_id = start_run(env.conn, source, trigger_type="nightly", now=1_000, lease_minutes=1)
    with pytest.raises(SharedSyncLeaseBusy):
        start_run(env.conn, source, trigger_type="manual", now=1_030, lease_minutes=1)

    assert reap_expired_runs(env.conn, now=1_100) == 1
    assert env.conn.execute(
        "SELECT status,error_code FROM shared_sync_runs WHERE id=?", (run_id,)
    ).fetchone()[:] == ("failed", "lease_expired")
    again = start_run(env.conn, source, trigger_type="manual", now=1_200, lease_minutes=1)
    assert env.conn.execute(
        "SELECT status FROM shared_sync_runs WHERE id=?", (again,)
    ).fetchone()[0] == "running"


def test_window_phases_follow_shanghai_wall_clock() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    def stamp(hour: int, minute: int) -> float:
        return datetime(2026, 11, 13, hour, minute, tzinfo=zone).timestamp()

    assert phase_for(stamp(0, 30)) == PHASE_QUIET
    assert phase_for(stamp(0, 59)) == PHASE_QUIET
    assert phase_for(stamp(1, 0)) == PHASE_WINDOW
    assert phase_for(stamp(6, 29)) == PHASE_WINDOW
    assert phase_for(stamp(6, 30)) == PHASE_DAY
    assert phase_for(stamp(14, 0)) == PHASE_DAY
    # A window that ends past midnight still compares as a half-open range.
    assert _in_window(30, (23, 0), (1, 0))
    assert _in_window(23 * 60, (23, 0), (1, 0))
    assert not _in_window(60, (23, 0), (1, 0))
    assert not _in_window(12 * 60, (23, 0), (1, 0))


def test_candidates_need_two_observations_and_coalesce_replacements(sync_env: SyncEnv) -> None:
    env = sync_env
    _write_document(env.share / "资料/草稿.md", "# 草稿\n\n一\n")
    _scan(env, now=100)
    entry = _entry(env, "资料/草稿.md")

    first = observe_candidate(
        env.conn, source_id=env.source_id, entry_id=str(entry["id"]),
        relative_path="资料/草稿.md", fingerprint=str(entry["fingerprint"]),
        doc_type="markdown", now=1_000,
    )
    assert first["state"] == CANDIDATE_WAITING
    too_soon = observe_candidate(
        env.conn, source_id=env.source_id, entry_id=str(entry["id"]),
        relative_path="资料/草稿.md", fingerprint=str(entry["fingerprint"]),
        doc_type="markdown", now=1_060,
    )
    assert too_soon["state"] == CANDIDATE_WAITING
    ready = observe_candidate(
        env.conn, source_id=env.source_id, entry_id=str(entry["id"]),
        relative_path="资料/草稿.md", fingerprint=str(entry["fingerprint"]),
        doc_type="markdown", now=1_130,
    )
    assert ready["state"] == CANDIDATE_READY

    # A newer fingerprint for the same path supersedes the ready candidate
    # instead of queueing both.
    replacement = observe_candidate(
        env.conn, source_id=env.source_id, entry_id=str(entry["id"]),
        relative_path="资料/草稿.md", fingerprint="f" * 64,
        doc_type="markdown", now=1_140,
    )
    assert replacement["state"] == CANDIDATE_WAITING
    assert env.conn.execute(
        "SELECT state FROM shared_sync_candidates WHERE id=?", (ready["id"],)
    ).fetchone()[0] == CANDIDATE_COALESCED
    events = [row[0] for row in env.conn.execute(
        "SELECT action FROM shared_sync_events ORDER BY created_at"
    ).fetchall()]
    assert "coalesced" in events


# --------------------------------------------------------------------------- #
# Shared attachments and scheduler behaviour
# --------------------------------------------------------------------------- #
def test_shared_rvt_attachment_is_registered_published_and_never_parsed(
    sync_env: SyncEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = sync_env
    model = env.share / "项目" / "模型.rvt"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"synthetic-rvt-bytes")
    _scan(env, now=100)
    entry = _entry(env, "项目/模型.rvt")
    assert (entry["entry_kind"], entry["doc_type"]) == ("document", "rvt")

    calls: list[str] = []
    monkeypatch.setattr(content_publication, "index_managed_content",
                        lambda *args, **kwargs: calls.append("index"))

    result = import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    assert (result.imported, result.failed) == (1, 0)
    entry = _entry(env, "项目/模型.rvt")
    version_id = str(entry["content_version_id"])
    assert env.conn.execute(
        "SELECT doc_type FROM content_versions WHERE id=?", (version_id,)
    ).fetchone()[0] == "rvt"

    _publish(env, version_id, generation=int(entry["sync_generation"]), entry_id=str(entry["id"]))
    assert _head(env, str(entry["content_item_id"])) == version_id
    # Attachments go straight to the head: no parser, no chunking, no embedding.
    assert calls == []
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_index_jobs j JOIN content_versions v ON v.id=j.version_id"
        " WHERE v.doc_type='rvt' AND j.status='done'"
    ).fetchone()[0] == 1


def test_scheduler_scans_during_the_day_but_only_publishes_in_the_window(
    sync_env: SyncEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = sync_env
    _write_document(env.share / "资料/夜间发布.md", "# 夜间发布\n\n正文\n")
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    enqueued: list[str] = []
    monkeypatch.setattr("api.indexing.enqueue_content_publication", enqueued.append)

    day = sync_source(env.conn, source, trigger_type="manual", now=1_000,
                      phase=PHASE_DAY, roots={"share": env.share})
    assert day["status"] == "succeeded"
    assert day["added_count"] == 1
    entry = _entry(env, "资料/夜间发布.md")
    assert entry["import_status"] == "not_imported"
    assert enqueued == []
    assert env.conn.execute("SELECT COUNT(*) FROM content_index_jobs").fetchone()[0] == 0

    # First window tick only observes: one look is never enough to publish.
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    sync_source(env.conn, source, trigger_type="nightly", now=2_000,
                phase=PHASE_WINDOW, roots={"share": env.share})
    entry = _entry(env, "资料/夜间发布.md")
    assert entry["import_status"] == "not_imported"

    # Second tick, past the stability window: import + publication job.
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    outcome = sync_source(env.conn, source, trigger_type="nightly", now=2_200,
                          phase=PHASE_WINDOW, roots={"share": env.share})
    assert outcome["status"] == "succeeded"
    assert outcome["imported_count"] == 1
    assert outcome["published_count"] == 1
    entry = _entry(env, "资料/夜间发布.md")
    assert entry["import_status"] == "imported"
    assert len(enqueued) == 1
    job = env.conn.execute(
        "SELECT status FROM content_index_jobs WHERE id=?", (enqueued[0],)
    ).fetchone()
    assert job["status"] == "pending"


def test_scheduler_failed_scan_never_looks_like_a_deletion(
    sync_env: SyncEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = sync_env
    _publish_document(env, "资料/断网.md", "# 断网\n\n正文\n")
    source = env.conn.execute(
        "SELECT * FROM external_media_sources WHERE id=?", (env.source_id,)
    ).fetchone()
    env.share.rename(env.share.with_name("share-offline"))

    outcome = sync_source(env.conn, source, trigger_type="manual", now=2_000,
                          phase=PHASE_DAY, roots={"share": env.share})
    assert outcome["status"] == "failed"
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_head_withdrawals"
    ).fetchone()[0] == 0
    assert env.conn.execute(
        "SELECT COUNT(*) FROM content_item_heads"
    ).fetchone()[0] == 1


def test_scan_ignores_lock_temp_and_backup_artefacts(sync_env: SyncEnv) -> None:
    env = sync_env
    folder = env.share / "资料"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "正式.md").write_text("# 正式\n", encoding="utf-8")
    # Editor/lock artefacts that must never become import candidates.
    for name in ("~$正式.docx", "正式.md.tmp", ".DS_Store", "旧版.md.bak", "锁定.laccdb"):
        (folder / name).write_bytes(b"x")
    _scan(env)
    registered = {
        row["relative_path"]
        for row in env.conn.execute(
            "SELECT relative_path FROM external_media_entries WHERE source_id=?", (env.source_id,)
        )
    }
    assert registered == {"资料/正式.md"}


def test_import_refuses_a_file_that_changed_after_the_scan(sync_env: SyncEnv) -> None:
    env = sync_env
    path = env.share / "资料" / "竞态.md"
    _write_document(path, "# 竞态\n\n一\n")
    _scan(env, now=100)
    entry = _entry(env, "资料/竞态.md")
    assert entry["import_status"] == "not_imported"

    # The share changes between scan and import: same path, newer content.
    path.write_text("# 竞态\n\n二\n", encoding="utf-8")
    os.utime(path, ns=(entry["modified_ns"] + 5_000_000_000, entry["modified_ns"] + 5_000_000_000))

    result = import_source_documents(
        env.conn, env.source_id, entry_ids=(str(entry["id"]),),
        actor_user_id=1, roots={"share": env.share},
    )
    assert (result.imported, result.failed) == (0, 0)
    assert result.outcomes[0].reason_code == "external_file_changed"
    entry = _entry(env, "资料/竞态.md")
    assert entry["import_status"] == "not_imported"
    assert entry["content_version_id"] is None
    assert env.conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0] == 0
