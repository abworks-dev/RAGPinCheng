import hashlib
import json
import sqlite3
import uuid

import pytest
from fastapi import HTTPException

from api import routes_media_transcript
def _connection(tmp_path):
    path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE media_assets (
            media_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            transcript_source_path TEXT
        );
        CREATE TABLE transcript_versions (
            id TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            source TEXT NOT NULL,
            canonical_json TEXT,
            canonical_sha256 TEXT,
            markdown_storage_kind TEXT NOT NULL,
            markdown_rel_path TEXT NOT NULL,
            markdown_sha256 TEXT NOT NULL,
            markdown_size_bytes INTEGER NOT NULL,
            publication_status TEXT NOT NULL
        );
        CREATE TABLE media_transcript_heads (
            media_id TEXT PRIMARY KEY,
            current_version_id TEXT NOT NULL
        );
        """
    )
    conn.close()
    return path


def test_legacy_ready_media_returns_inferred_segments(tmp_path, monkeypatch):
    db_path = _connection(tmp_path)
    docs = tmp_path / "docs"
    transcript = docs / "教学视频" / "sample.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "# 示例\n\n说话人 1 00:00\n第一段\n\n说话人 2 00:07\n第二段\n",
        encoding="utf-8",
    )
    media_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?)",
        (media_id, "ready", str(transcript)),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(routes_media_transcript, "DOCS_DIR", docs)
    monkeypatch.setattr(
        routes_media_transcript,
        "db_connect",
        lambda: _open_rows(db_path),
    )

    result = routes_media_transcript.get_media_transcript(media_id, 1)

    assert [item.start_ms for item in result.segments] == [0, 7000]
    assert result.segments[0].end_ms == 7000
    assert result.segments[1].end_ms is None
    assert result.version_id is None


def test_candidate_version_is_never_exposed(tmp_path, monkeypatch):
    db_path = _connection(tmp_path)
    media_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO media_assets VALUES (?,?,NULL)", (media_id, "ready"))
    conn.execute(
        """INSERT INTO transcript_versions
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id,
            media_id,
            "automatic",
            None,
            None,
            "managed_artifact",
            "markdown/aa/file.md",
            hashlib.sha256(b"draft").hexdigest(),
            5,
            "not_published",
        ),
    )
    conn.execute("INSERT INTO media_transcript_heads VALUES (?,?)", (media_id, version_id))
    conn.commit()
    conn.close()
    monkeypatch.setattr(routes_media_transcript, "db_connect", lambda: _open_rows(db_path))

    with pytest.raises(HTTPException) as exc:
        routes_media_transcript.get_media_transcript(media_id, 1)

    assert exc.value.status_code == 404


def test_canonical_hash_mismatch_is_integrity_error(tmp_path, monkeypatch):
    db_path = _connection(tmp_path)
    media_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    canonical = json.dumps({"media_id": media_id})
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO media_assets VALUES (?,?,NULL)", (media_id, "ready"))
    conn.execute(
        """INSERT INTO transcript_versions
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id,
            media_id,
            "automatic",
            canonical,
            "0" * 64,
            "managed_artifact",
            "markdown/aa/file.md",
            "0" * 64,
            1,
            "published",
        ),
    )
    conn.execute("INSERT INTO media_transcript_heads VALUES (?,?)", (media_id, version_id))
    conn.commit()
    conn.close()
    monkeypatch.setattr(routes_media_transcript, "db_connect", lambda: _open_rows(db_path))

    with pytest.raises(HTTPException) as exc:
        routes_media_transcript.get_media_transcript(media_id, 1)

    assert exc.value.status_code == 409


def _open_rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
