import os
import sqlite3
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes_media


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _media_api(tmp_path, monkeypatch, *, published: bool):
    db_path = tmp_path / "app.sqlite"
    media_root = tmp_path / "media"
    media_root.mkdir()
    media_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    relative = f"{media_id}/original.mp4"
    media_file = media_root / relative
    media_file.parent.mkdir()
    media_file.write_bytes(b"synthetic-video")

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE media_assets(
            media_id TEXT PRIMARY KEY,storage_rel_path TEXT,mime_type TEXT,status TEXT
        );
        CREATE TABLE transcript_versions(
            id TEXT PRIMARY KEY,media_id TEXT,publication_status TEXT
        );
        CREATE TABLE media_transcript_heads(
            media_id TEXT PRIMARY KEY,current_version_id TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?,'transcript_ready')",
        (media_id, relative, "video/mp4"),
    )
    conn.execute(
        "INSERT INTO transcript_versions VALUES (?,?,?)",
        (version_id, media_id, "published" if published else "not_published"),
    )
    conn.execute("INSERT INTO media_transcript_heads VALUES (?,?)", (media_id, version_id))
    conn.commit()
    conn.close()

    monkeypatch.setattr(routes_media, "MEDIA_DIR", media_root)
    monkeypatch.setattr(routes_media, "db_connect", lambda: _connect(db_path))
    app = FastAPI()
    app.include_router(routes_media.router, prefix="/api")
    app.dependency_overrides[routes_media.require_user] = lambda: 1
    return TestClient(app), media_id


def test_published_transcript_allows_streaming_before_summary_status_is_ready(
    tmp_path, monkeypatch
):
    client, media_id = _media_api(tmp_path, monkeypatch, published=True)
    response = client.get(f"/api/media/{media_id}")
    assert response.status_code == 200
    assert response.content == b"synthetic-video"
    assert response.headers["accept-ranges"] == "bytes"


def test_unpublished_transcript_ready_media_remains_hidden(tmp_path, monkeypatch):
    client, media_id = _media_api(tmp_path, monkeypatch, published=False)
    assert client.get(f"/api/media/{media_id}").status_code == 404


def _external_media_api(tmp_path, monkeypatch, *, published: bool):
    """Build the public media API against a read-only external (shared) root.

    Mirrors the production layout: ``storage_kind='external'`` media point at
    a logical ``external/<source_id>/<uuid>`` path, while the real file lives
    under the configured external root alias (route table + entry binding),
    exactly like the UNC-mapped ``pincheng_kb`` shared folder.
    """
    db_path = tmp_path / "app.sqlite"
    external_root = tmp_path / "shared-root"
    source_rel = "品成知识库/1.2内部教学视频（MP4）"
    media_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    entry_rel = "1.土建/4.地上精装/4.2.19户型点位检查.mp4"

    source_dir = external_root / source_rel
    video = source_dir / entry_rel
    video.parent.mkdir(parents=True)
    video.write_bytes(b"shared-video-bytes")
    modified_ns = video.stat().st_mtime_ns
    size = video.stat().st_size

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE media_assets(
            media_id TEXT PRIMARY KEY,storage_rel_path TEXT,mime_type TEXT,status TEXT,
            storage_kind TEXT,file_size INTEGER
        );
        CREATE TABLE transcript_versions(
            id TEXT PRIMARY KEY,media_id TEXT,publication_status TEXT
        );
        CREATE TABLE media_transcript_heads(
            media_id TEXT PRIMARY KEY,current_version_id TEXT
        );
        CREATE TABLE external_media_entries(
            id TEXT PRIMARY KEY,source_id TEXT,relative_path TEXT,file_size INTEGER,
            modified_ns INTEGER,availability TEXT,media_id TEXT,filename TEXT
        );
        CREATE TABLE external_media_sources(
            id TEXT PRIMARY KEY,name TEXT,root_alias TEXT,relative_path TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?,?,?,?)",
        (media_id, f"external/source-1/{media_id}", "video/mp4", "transcript_ready", "external", size),
    )
    conn.execute(
        "INSERT INTO transcript_versions VALUES (?,?,?)",
        (version_id, media_id, "published" if published else "not_published"),
    )
    conn.execute("INSERT INTO media_transcript_heads VALUES (?,?)", (media_id, version_id))
    conn.execute(
        "INSERT INTO external_media_entries VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "source-1", entry_rel, size, modified_ns, "available", media_id, "video.mp4"),
    )
    conn.execute(
        "INSERT INTO external_media_sources VALUES (?,?,?,?)",
        ("source-1", "内部教学视频", "pincheng_kb", source_rel),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(routes_media, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(routes_media, "EXTERNAL_MEDIA_ROOTS", {"pincheng_kb": external_root})
    monkeypatch.setattr(routes_media, "db_connect", lambda: _connect(db_path))
    app = FastAPI()
    app.include_router(routes_media.router, prefix="/api")
    app.dependency_overrides[routes_media.require_user] = lambda: 1
    return TestClient(app), media_id, video


def test_external_shared_video_streams_via_public_endpoint(tmp_path, monkeypatch):
    client, media_id, video = _external_media_api(tmp_path, monkeypatch, published=True)
    response = client.get(f"/api/media/{media_id}", headers={"Range": "bytes=2-6"})

    assert response.status_code == 206
    assert response.content == b"ared-"
    assert response.headers["accept-ranges"] == "bytes"


def test_external_shared_video_hidden_without_published_head(tmp_path, monkeypatch):
    client, media_id, _video = _external_media_api(tmp_path, monkeypatch, published=False)
    assert client.get(f"/api/media/{media_id}").status_code == 404


def test_external_shared_video_missing_file_returns_404(tmp_path, monkeypatch):
    client, media_id, video = _external_media_api(tmp_path, monkeypatch, published=True)
    os.remove(video)
    assert client.get(f"/api/media/{media_id}").status_code == 404
