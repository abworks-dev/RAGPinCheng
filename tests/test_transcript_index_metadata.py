from __future__ import annotations

import sqlite3

from pathlib import Path

from src.chunk import chunk_transcript
from src.ingest import ParsedDoc


def test_parents_version_columns_are_additive_nullable_and_idempotent(tmp_path, monkeypatch):
    from src import index as index_module

    db_path = tmp_path / "parents.sqlite"
    monkeypatch.setattr(index_module, "PARENTS_DB", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE parents (parent_id TEXT PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO parents(parent_id, text) VALUES (?, ?)", ("legacy", "kept"))
    conn.commit()
    conn.close()

    first = index_module._init_parents_db()
    first.close()
    second = index_module._init_parents_db()
    columns = {row[1]: row for row in second.execute("PRAGMA table_info(parents)").fetchall()}
    second.close()
    assert columns["transcript_version_id"][3] == 0
    assert columns["publication_target_id"][3] == 0
    check = sqlite3.connect(db_path)
    try:
        assert check.execute("SELECT text FROM parents WHERE parent_id=?", ("legacy",)).fetchone() == ("kept",)
        names = [row[1] for row in check.execute("PRAGMA table_info(parents)").fetchall()]
        assert names.count("transcript_version_id") == 1
        assert names.count("publication_target_id") == 1
    finally:
        check.close()


MEDIA = "123e4567-e89b-12d3-a456-426614174000"
VERSION = "123e4567-e89b-12d3-a456-426614174001"
TARGET = "transcript-candidate-123e4567-e89b-12d3-a456-426614174001-a1"


def test_versioned_transcript_metadata_propagates_and_separates_ids(tmp_path):
    md = tmp_path / "transcript.md"
    md.write_text("说话人 1 00:00\n你好\n\n说话人 1 00:02\n世界\n", encoding="utf-8")
    legacy = ParsedDoc(md, "教学视频", "Video", md, "transcript", media_id=MEDIA)
    versioned = ParsedDoc(md, "教学视频", "Video", md, "transcript", media_id=MEDIA, transcript_version_id=VERSION, publication_target_id=TARGET)
    old_parents, old_children = chunk_transcript(legacy)
    new_parents, new_children = chunk_transcript(versioned)
    assert old_parents[0].transcript_version_id is None
    assert new_parents[0].transcript_version_id == VERSION
    assert new_children[0].publication_target_id == TARGET
    assert old_parents[0].parent_id != new_parents[0].parent_id
    assert [parent.parent_id for parent in old_parents] == ["09638a1d-ffc2-51db-bf85-118125a7f765"]
    assert [child.child_id for child in old_children] == [
        "6da58600-2c45-5f6e-bc0f-ba31fe6c1c9c",
        "e44c70f3-e58b-5509-93af-d534e6b3b6c4",
    ]


def test_transcript_child_ids_remain_unique_for_repeated_timestamp_and_prefix(tmp_path):
    md = tmp_path / "transcript.md"
    prefix = "same-prefix-" * 8
    md.write_text(
        f"说话人 1 00:01\n{prefix}A\n\n说话人 2 00:01\n{prefix}B\n",
        encoding="utf-8",
    )
    doc = ParsedDoc(
        md,
        "教学视频",
        "Video",
        md,
        "transcript",
        media_id=MEDIA,
        transcript_version_id=VERSION,
        publication_target_id=TARGET,
    )

    _, children = chunk_transcript(doc)

    assert len(children) == 2
    assert len({child.child_id for child in children}) == 2
