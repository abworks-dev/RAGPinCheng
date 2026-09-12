"""Transcript version list: completion time and stable newest-first ordering."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from api import transcription_store
from api.routes_transcription import _version_job_lookup


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE transcript_versions (
               id TEXT PRIMARY KEY,
               media_id TEXT NOT NULL,
               transcription_job_id TEXT,
               created_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE transcription_jobs (
               id TEXT PRIMARY KEY,
               finished_at INTEGER,
               attempt_number INTEGER
           )"""
    )
    return conn


def test_automatic_version_reports_the_job_completion_time_and_attempt() -> None:
    conn = _connection()
    conn.execute(
        "INSERT INTO transcription_jobs(id,finished_at,attempt_number) VALUES ('job-1',1700003600,3)"
    )
    conn.execute(
        "INSERT INTO transcript_versions(id,media_id,transcription_job_id,created_at)"
        " VALUES ('version-1','media-1','job-1',1700003600)"
    )

    lookup = _version_job_lookup(conn, ["version-1"])

    assert lookup["version-1"] == (1700003600, 3)


def test_manual_version_falls_back_to_its_own_creation_time() -> None:
    conn = _connection()
    conn.execute(
        "INSERT INTO transcript_versions(id,media_id,transcription_job_id,created_at)"
        " VALUES ('version-manual','media-1',NULL,1699999999)"
    )

    lookup = _version_job_lookup(conn, ["version-manual"])

    assert lookup["version-manual"] == (1699999999, None)


def test_finished_job_without_a_timestamp_falls_back_instead_of_reporting_none() -> None:
    conn = _connection()
    conn.execute(
        "INSERT INTO transcription_jobs(id,finished_at,attempt_number) VALUES ('job-2',NULL,1)"
    )
    conn.execute(
        "INSERT INTO transcript_versions(id,media_id,transcription_job_id,created_at)"
        " VALUES ('version-2','media-1','job-2',1700000000)"
    )

    lookup = _version_job_lookup(conn, ["version-2"])

    assert lookup["version-2"] == (1700000000, 1)


def test_unknown_version_ids_are_not_invented() -> None:
    conn = _connection()

    assert _version_job_lookup(conn, ["missing"]) == {}
    assert _version_job_lookup(conn, []) == {}


def test_version_list_breaks_creation_time_ties_by_insertion_order() -> None:
    """Batch runs share a created_at second; the UUID primary key must not decide."""

    source = Path(transcription_store.__file__).read_text(encoding="utf-8")
    statement = source.split("def list_versions", 1)[1].split("def load_publication_job", 1)[0]

    assert "ORDER BY created_at DESC,rowid DESC" in statement
    assert "ORDER BY created_at DESC,id DESC" not in statement
