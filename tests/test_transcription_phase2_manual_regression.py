import sqlite3
from pathlib import Path

from api.db import init_db


LEGACY = Path(__file__).parent / "fixtures" / "transcription" / "phase2-legacy-app-schema.sql"


def test_phase2_migration_does_not_backfill_or_rewrite_manual_transcript(tmp_path):
    transcript = tmp_path / "docs" / "教学视频" / "manual.md"
    transcript.parent.mkdir(parents=True)
    original = "# 人工稿\n\n说话人 1 00:00\n人工原始字节。\n".encode("utf-8")
    transcript.write_bytes(original)
    path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "123e4567-e89b-12d3-a456-426614174000", "Manual", "manual.mp4", "m/original.mp4",
            "video/mp4", 1, None, str(transcript), "uploaded", "ready", None, 1, 1, None,
        ),
    )
    conn.commit()
    conn.close()
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT transcript_source_path FROM media_assets").fetchone()[0] == str(transcript)
    assert conn.execute("SELECT count(*) FROM transcript_versions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM media_transcript_heads").fetchone()[0] == 0
    conn.close()
    assert transcript.read_bytes() == original
