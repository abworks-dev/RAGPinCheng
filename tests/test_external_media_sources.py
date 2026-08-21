from __future__ import annotations

import os
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_external_media
from api.db import connect, init_db
from api.db import get_db
from api.external_media import ExternalMediaError, discover_video_files, reconcile_source
from api.media_storage import MediaStorageError, normalize_external_relative_path, resolve_media_path
from src.config import parse_external_unc_roots, resolve_external_unc_path


def _source_database(tmp_path: Path, share: Path) -> tuple[sqlite3.Connection, str]:
    db_path = tmp_path / "app.sqlite"
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    scheme = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()
    assert scheme is not None
    source_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO external_media_sources(
               id,name,root_alias,relative_path,target_category_id,default_scheme_id,
               created_at,updated_at,created_by
           ) VALUES (?,?,?,?,?,?,?,?,NULL)""",
        (source_id, "Synthetic share", "share", "", "cat-05", scheme["id"], 1, 1),
    )
    conn.commit()
    return conn, source_id


def test_unc_root_mapping_accepts_unicode_path_and_rejects_escape_or_unknown_share(tmp_path: Path) -> None:
    roots = parse_external_unc_roots(json.dumps({"training": {"unc": r"\\192.168.0.252\项目工程", "path": str(tmp_path)}}))
    assert resolve_external_unc_path(r"\\192.168.0.252\项目工程\品成知识库\1.2内部教学视频（MP4）", roots) == (
        "training", "品成知识库/1.2内部教学视频（MP4）"
    )
    with pytest.raises(ValueError, match="invalid_external_unc_path"):
        resolve_external_unc_path(r"\\192.168.0.252\项目工程\..\秘密", roots)
    with pytest.raises(ValueError, match="external_unc_root_unconfigured"):
        resolve_external_unc_path(r"\\192.168.0.252\其他共享\视频", roots)


def test_scan_reconciles_add_change_missing_recovery_and_outage(tmp_path: Path) -> None:
    share = tmp_path / "share"
    (share / "课程").mkdir(parents=True)
    first = share / "课程" / "lesson.mp4"
    first.write_bytes(b"video-v1")
    conn, source_id = _source_database(tmp_path, share)
    roots = {"share": share}
    try:
        initial = reconcile_source(conn, source_id, trigger_type="manual", roots=roots, now=10)
        assert (initial.discovered_count, initial.added_count, initial.changed_count) == (1, 1, 0)
        original = conn.execute(
            "SELECT id,media_id,fingerprint FROM external_media_entries WHERE source_id=? AND availability='available'",
            (source_id,),
        ).fetchone()
        assert original is not None

        repeat = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=20)
        assert (repeat.added_count, repeat.changed_count, repeat.missing_count) == (0, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM external_media_entries").fetchone()[0] == 1

        first.write_bytes(b"video-v2-longer")
        changed = reconcile_source(conn, source_id, trigger_type="manual", roots=roots, now=30)
        assert (changed.added_count, changed.changed_count) == (0, 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM external_media_entries WHERE source_id=? AND availability='superseded'", (source_id,)
        ).fetchone()[0] == 1

        current = conn.execute(
            "SELECT id,media_id FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()
        current_identity = first.stat()
        first.unlink()
        missing = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=40)
        assert missing.missing_count == 1
        assert conn.execute(
            "SELECT availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()[0] == "missing"

        first.write_bytes(b"video-v2-longer")
        os.utime(first, ns=(current_identity.st_atime_ns, current_identity.st_mtime_ns))
        recovered = reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=50)
        assert (recovered.added_count, recovered.changed_count) == (0, 0)
        recovered_row = conn.execute(
            "SELECT media_id,availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()
        assert (recovered_row["media_id"], recovered_row["availability"]) == (current["media_id"], "available")

        offline = tmp_path / "share-offline"
        share.rename(offline)
        with pytest.raises(ExternalMediaError, match="external_source_unavailable"):
            reconcile_source(conn, source_id, trigger_type="scheduled", roots=roots, now=60)
        assert conn.execute(
            "SELECT availability FROM external_media_entries WHERE id=?", (current["id"],)
        ).fetchone()[0] == "available"
        assert conn.execute("SELECT status FROM external_media_sources WHERE id=?", (source_id,)).fetchone()[0] == "unavailable"
    finally:
        conn.close()


def test_scan_database_failure_does_not_leave_running_state(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    (share / "clip.mp4").write_bytes(b"video")
    conn, source_id = _source_database(tmp_path, share)
    conn.execute(
        """CREATE TRIGGER fail_external_entry_insert
           BEFORE INSERT ON external_media_entries
           BEGIN
             SELECT RAISE(ABORT, 'synthetic reconcile failure');
           END"""
    )
    conn.commit()
    try:
        with pytest.raises(ExternalMediaError, match="external_reconcile_failed"):
            reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)

        source = conn.execute(
            "SELECT status,last_error_code FROM external_media_sources WHERE id=?", (source_id,)
        ).fetchone()
        run = conn.execute(
            "SELECT status,error_code,finished_at FROM external_media_scan_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()
        assert (source["status"], source["last_error_code"]) == (
            "scan_failed",
            "external_reconcile_failed",
        )
        assert (run["status"], run["error_code"]) == ("failed", "external_reconcile_failed")
        assert run["finished_at"] is not None
        assert conn.execute("SELECT COUNT(*) FROM external_media_entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0
    finally:
        conn.close()


def test_external_media_resolver_checks_identity_and_never_returns_host_path(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    video = share / "clip.mp4"
    video.write_bytes(b"range-video")
    conn, source_id = _source_database(tmp_path, share)
    try:
        reconcile_source(conn, source_id, trigger_type="manual", roots={"share": share}, now=10)
        row = conn.execute(
            "SELECT media_id FROM external_media_entries WHERE source_id=? AND availability='available'", (source_id,)
        ).fetchone()
        resolved = resolve_media_path(conn, row["media_id"], external_roots={"share": share})
        assert resolved.path == video
        assert resolved.storage_kind == "external"
        video.write_bytes(b"mutated")
        with pytest.raises(MediaStorageError, match="external_media_changed"):
            resolve_media_path(conn, row["media_id"], external_roots={"share": share})
        persisted = conn.execute(
            "SELECT storage_rel_path FROM media_assets WHERE media_id=?", (row["media_id"],)
        ).fetchone()[0]
        assert str(share) not in persisted
    finally:
        conn.close()


@pytest.mark.parametrize("value", ["../secret.mp4", "folder/../../secret.mp4", "/absolute.mp4", r"folder\clip.mp4", "./clip.mp4"])
def test_external_relative_path_rejects_escape_and_ambiguous_separators(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_external_relative_path"):
        normalize_external_relative_path(value)


def test_scanner_ignores_non_mp4_and_rejects_bounded_overflow(tmp_path: Path) -> None:
    (tmp_path / "one.mp4").write_bytes(b"1")
    (tmp_path / "notes.md").write_text("not media", encoding="utf-8")
    assert [item.filename for item in discover_video_files(tmp_path, max_files=1)] == ["one.mp4"]
    (tmp_path / "two.mp4").write_bytes(b"2")
    with pytest.raises(ExternalMediaError, match="external_source_file_limit_exceeded"):
        discover_video_files(tmp_path, max_files=1)


def test_scanner_skips_symbolic_links_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.mp4"
    target.write_bytes(b"video")
    link = tmp_path / "linked.mp4"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable for this account")
    found = discover_video_files(tmp_path)
    assert [item.filename for item in found] == ["target.mp4"]


def test_external_media_api_requires_admin_and_csrf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "app.sqlite"
    share = tmp_path / "share"
    share.mkdir()
    init_db(db_path, backup_dir=tmp_path / "backups")
    conn = connect(db_path)
    now = int(time.time())
    conn.executemany(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at) VALUES (?,?,?,?,?,1,?)",
        [(1, "admin", "Admin", "x", "admin", now), (2, "user", "User", "x", "user", now)],
    )
    conn.executemany(
        "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
        [("admin-sid", 1, "admin-csrf", now, now + 3600), ("user-sid", 2, "user-csrf", now, now + 3600)],
    )
    scheme_id = conn.execute(
        "SELECT id FROM transcription_schemes WHERE enabled=1 AND archived=0 ORDER BY sort_order LIMIT 1"
    ).fetchone()[0]
    conn.commit()
    conn.close()

    def override_db():
        request_conn = connect(db_path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    monkeypatch.setattr(routes_external_media, "EXTERNAL_MEDIA_ROOTS", {"share": share})
    app = FastAPI()
    app.include_router(routes_external_media.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        assert client.get("/api/admin/external-media/roots").status_code == 401
        assert client.get(
            "/api/admin/external-media/roots", cookies={"pc_sid": "user-sid"}
        ).status_code == 403
        assert client.get(
            "/api/admin/external-media/roots", cookies={"pc_sid": "admin-sid"}
        ).json() == [{"alias": "share"}]
        body = {
            "name": "Training", "root_alias": "share", "relative_path": "",
            "target_category_id": "cat-05", "default_scheme_id": scheme_id,
            "auto_enqueue": False, "scan_interval_seconds": 900,
        }
        assert client.post(
            "/api/admin/external-media/sources", json=body, cookies={"pc_sid": "admin-sid"}
        ).status_code == 403
        created = client.post(
            "/api/admin/external-media/sources", json=body,
            cookies={"pc_sid": "admin-sid"}, headers={"X-CSRF-Token": "admin-csrf"},
        )
        assert created.status_code == 201
        assert created.json()["root_alias"] == "share"
