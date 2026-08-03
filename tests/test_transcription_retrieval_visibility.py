from __future__ import annotations

import sqlite3

from src.transcription_retrieval_visibility import SQLitePublishedTranscriptVisibility

MEDIA = "123e4567-e89b-12d3-a456-426614174000"
VERSION = "123e4567-e89b-12d3-a456-426614174001"


def _db(path, *, corrupt=False):
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE media_transcript_heads(media_id TEXT PRIMARY KEY,current_version_id TEXT NOT NULL,updated_at INTEGER NOT NULL);
    CREATE TABLE transcript_versions(id TEXT PRIMARY KEY,media_id TEXT NOT NULL,publication_status TEXT NOT NULL);
    """)
    if corrupt:
        conn.execute("INSERT INTO media_transcript_heads VALUES(?,?,?)", (MEDIA, VERSION, 1))
        conn.execute("INSERT INTO transcript_versions VALUES(?,?,?)", (VERSION, "wrong", "published"))
    else:
        conn.execute("INSERT INTO media_transcript_heads VALUES(?,?,?)", (MEDIA, VERSION, 1))
        conn.execute("INSERT INTO transcript_versions VALUES(?,?,?)", (VERSION, MEDIA, "published"))
    conn.commit(); conn.close()


def test_only_current_published_head_is_visible(tmp_path):
    path = tmp_path / "app.sqlite"
    _db(path)
    snapshot = SQLitePublishedTranscriptVisibility(path).snapshot()
    assert snapshot.healthy is True
    assert snapshot.allows(VERSION)
    assert snapshot.allows(None)
    assert not snapshot.allows("123e4567-e89b-12d3-a456-426614174002")


def test_corrupt_head_fails_closed_for_versioned_but_keeps_legacy_visible(tmp_path):
    path = tmp_path / "app.sqlite"
    _db(path, corrupt=True)
    snapshot = SQLitePublishedTranscriptVisibility(path).snapshot()
    assert snapshot.healthy is False
    assert snapshot.allows(None)
    assert not snapshot.allows(VERSION)

def test_missing_database_fails_closed_for_versioned_but_keeps_legacy_visible(tmp_path):
    snapshot = SQLitePublishedTranscriptVisibility(tmp_path / "missing.sqlite").snapshot()
    assert snapshot.healthy is False
    assert snapshot.allows(None)
    assert not snapshot.allows(VERSION)
