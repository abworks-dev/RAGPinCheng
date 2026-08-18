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
