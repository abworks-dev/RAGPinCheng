"""Build all current managed-content heads into isolated index destinations."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple


class ManagedHead(NamedTuple):
    item_id: str
    version_id: str
    object_sha256: str
    storage_rel_path: str
    original_filename: str
    doc_type: str
    title: str
    category_key: str
    category_display_name: str
    publication_target_id: str


class TranscriptHead(NamedTuple):
    media_id: str
    version_id: str
    title: str
    markdown_rel_path: str
    markdown_sha256: str
    markdown_size_bytes: int
    publication_target_id: str


class RebuildSnapshot(NamedTuple):
    managed: tuple[ManagedHead, ...]
    transcripts: tuple[TranscriptHead, ...]


def validate_shadow_destinations(collection: str, parents_database: Path) -> None:
    collection_match = re.fullmatch(
        r"pincheng_docs_rebuild_([0-9]+)_([0-9]+)", collection
    )
    if collection_match is None:
        raise ValueError("shadow_collection_required")
    parent_match = re.fullmatch(
        r"full-reindex-([0-9]+)-([0-9]+)-shadow", parents_database.parent.name
    )
    if parents_database.name != "parents.sqlite" or parent_match is None:
        raise ValueError("shadow_parent_database_required")
    if collection_match.groups() != parent_match.groups():
        raise ValueError("shadow_destination_mismatch")


def load_rebuild_snapshot(conn: sqlite3.Connection) -> RebuildSnapshot:
    """Freeze the exact currently visible ordinary and transcript heads."""
    conn.row_factory = sqlite3.Row
    managed_rows = conn.execute(
        """
        SELECT h.item_id,h.current_version_id AS version_id,v.object_sha256,
               o.storage_rel_path,v.original_filename,v.doc_type,
               COALESCE(v.title,i.title) AS title,c.category_key,
               c.display_name AS category_display_name,
               (SELECT j.target_index_id FROM content_index_jobs j
                  WHERE j.publication_id=h.publication_id
                    AND j.version_id=h.current_version_id AND j.status='done'
                  ORDER BY j.finished_at DESC,j.id DESC LIMIT 1) AS publication_target_id
          FROM content_item_heads h
          JOIN content_items i ON i.id=h.item_id
          JOIN content_versions v ON v.id=h.current_version_id AND v.item_id=h.item_id
          JOIN category_nodes c ON c.id=i.category_id
          JOIN content_objects o ON o.sha256=v.object_sha256
         ORDER BY h.item_id
        """
    ).fetchall()
    transcript_rows = conn.execute(
        """
        SELECT h.media_id,h.current_version_id AS version_id,m.title,
               v.markdown_rel_path,v.markdown_sha256,v.markdown_size_bytes,
               (SELECT j.target_index_id FROM transcript_publication_index_jobs j
                  WHERE j.transcript_version_id=h.current_version_id AND j.status='done'
                  ORDER BY j.finished_at DESC,j.id DESC LIMIT 1) AS publication_target_id
          FROM media_transcript_heads h
          JOIN media_assets m ON m.media_id=h.media_id
          JOIN transcript_versions v ON v.id=h.current_version_id
         WHERE v.markdown_storage_kind='managed_artifact'
         ORDER BY h.media_id
        """
    ).fetchall()
    if any(row["publication_target_id"] is None for row in managed_rows):
        raise ValueError("managed_head_missing_completed_index_identity")
    if any(row["publication_target_id"] is None for row in transcript_rows):
        raise ValueError("transcript_head_missing_completed_index_identity")
    transcript_head_count = conn.execute(
        "SELECT count(*) FROM media_transcript_heads"
    ).fetchone()[0]
    if transcript_head_count != len(transcript_rows):
        raise ValueError("unsupported_transcript_head_storage")
    return RebuildSnapshot(
        tuple(ManagedHead(**dict(row)) for row in managed_rows),
        tuple(TranscriptHead(**dict(row)) for row in transcript_rows),
    )


def verify_file_sha256(path: Path, expected: str) -> int:
    if not path.is_file() or path.is_symlink():
        raise ValueError("source_unavailable")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    if digest.hexdigest() != expected:
        raise ValueError("source_hash_mismatch")
    return size


def validate_report(report: dict[str, object]) -> None:
    expected = report["expected"]
    indexed = report["indexed"]
    qdrant = report["qdrant"]
    if indexed["managed_heads"] != expected["managed_heads"]:
        raise ValueError("managed_head_coverage_mismatch")
    if indexed["transcript_heads"] != expected["transcript_heads"]:
        raise ValueError("transcript_head_coverage_mismatch")
    if indexed["parents"] <= 0 or indexed["children"] <= 0:
        raise ValueError("empty_rebuild")
    if report["parents_integrity"] != "ok":
        raise ValueError("parents_integrity_failed")
    if qdrant["status"] != "green":
        raise ValueError("qdrant_not_green")
    if qdrant["points_count"] != indexed["children"]:
        raise ValueError("qdrant_child_count_mismatch")
    location_coverage = report["location_head_coverage"]
    if location_coverage["located"] != location_coverage["expected"]:
        raise ValueError("location_head_coverage_mismatch")


def _materialize_managed_source(content_root: Path, data_root: Path, head: ManagedHead) -> Path:
    source = (content_root / head.storage_rel_path).resolve(strict=False)
    root = content_root.resolve(strict=False)
    if source != root and root not in source.parents:
        raise ValueError("content_path_escape")
    verify_file_sha256(source, head.object_sha256)
    filename = Path(head.original_filename).name
    if filename != head.original_filename or filename in {"", ".", ".."}:
        raise ValueError("invalid_original_filename")
    target = data_root / "inputs" / head.item_id / head.version_id / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    verify_file_sha256(target, head.object_sha256)
    return target


def run_rebuild(
    *,
    app_database: Path,
    content_root: Path,
    artifact_root: Path,
    report_path: Path,
) -> dict[str, object]:
    from src.config import COLLECTION, PARENTS_DB
    from src.index import _client, reset_index
    from src.ingest import ParsedDoc
    from src.indexing_pipeline import (
        ManagedIndexMetadata,
        index_managed_content,
        index_transcript_candidate,
    )

    validate_shadow_destinations(COLLECTION, PARENTS_DB)
    data_root = PARENTS_DB.parent
    data_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{app_database}?mode=ro", uri=True) as conn:
        snapshot = load_rebuild_snapshot(conn)

    reset_index()
    managed_parents = managed_children = 0
    transcript_parents = transcript_children = 0
    try:
        for head in snapshot.managed:
            source = _materialize_managed_source(content_root, data_root, head)
            result = index_managed_content(
                source,
                head.doc_type,
                ManagedIndexMetadata(
                    content_item_id=head.item_id,
                    content_version_id=head.version_id,
                    publication_target_id=head.publication_target_id,
                    category_key=head.category_key,
                    category_display_name=head.category_display_name,
                    doc_title=head.title,
                    source_ref=f"content://{head.item_id}/{head.version_id}",
                ),
                force_parse=True,
                write_preview=False,
            )
            managed_parents += result.parents
            managed_children += result.children

        for head in snapshot.transcripts:
            markdown = (artifact_root / head.markdown_rel_path).resolve(strict=False)
            root = artifact_root.resolve(strict=False)
            if markdown != root and root not in markdown.parents:
                raise ValueError("transcript_artifact_path_escape")
            if verify_file_sha256(markdown, head.markdown_sha256) != head.markdown_size_bytes:
                raise ValueError("transcript_artifact_size_mismatch")
            synthetic_source = Path("/app/docs/教学视频/_media") / f"{head.media_id}.md"
            result = index_transcript_candidate(
                ParsedDoc(
                    source_path=synthetic_source,
                    category="教学视频",
                    doc_title=head.title.strip() or head.media_id,
                    markdown_path=markdown,
                    doc_type="transcript",
                    media_id=head.media_id,
                    transcript_version_id=head.version_id,
                    publication_target_id=head.publication_target_id,
                )
            )
            transcript_parents += result.parents
            transcript_children += result.children

        with sqlite3.connect(PARENTS_DB) as parents:
            integrity = parents.execute("PRAGMA integrity_check").fetchone()[0]
            parent_count = parents.execute("SELECT count(*) FROM parents").fetchone()[0]
            location_rows = parents.execute(
                """SELECT doc_type,count(*) AS total,
                          sum(CASE WHEN page_number IS NOT NULL OR paragraph_anchor IS NOT NULL
                                        OR sheet_name IS NOT NULL OR topic_id IS NOT NULL
                                   THEN 1 ELSE 0 END) AS located
                     FROM parents GROUP BY doc_type ORDER BY doc_type"""
            ).fetchall()
            locatable_version_ids = {
                head.version_id
                for head in snapshot.managed
                if head.doc_type in {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "xmind"}
            }
            located_version_ids = {
                row[0]
                for row in parents.execute(
                    """SELECT DISTINCT content_version_id FROM parents
                         WHERE content_version_id IS NOT NULL
                           AND (page_number IS NOT NULL OR paragraph_anchor IS NOT NULL
                                OR sheet_name IS NOT NULL OR topic_id IS NOT NULL)"""
                ).fetchall()
            }
        info = _client().get_collection(COLLECTION)
        status = getattr(info.status, "value", str(info.status))
        report = {
            "schema_version": 1,
            "expected": {
                "managed_heads": len(snapshot.managed),
                "transcript_heads": len(snapshot.transcripts),
            },
            "indexed": {
                "managed_heads": len(snapshot.managed),
                "transcript_heads": len(snapshot.transcripts),
                "parents": parent_count,
                "children": managed_children + transcript_children,
                "managed_parents": managed_parents,
                "transcript_parents": transcript_parents,
            },
            "locations": {
                row[0]: {"parents": row[1], "located": row[2] or 0}
                for row in location_rows
            },
            "location_head_coverage": {
                "expected": len(locatable_version_ids),
                "located": len(locatable_version_ids & located_version_ids),
            },
            "parents_integrity": integrity,
            "qdrant": {"status": status, "points_count": info.points_count},
        }
        validate_report(report)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    except Exception:
        client = _client()
        if client.collection_exists(COLLECTION):
            client.delete_collection(COLLECTION)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--content-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run_rebuild(
        app_database=args.app_database,
        content_root=args.content_root,
        artifact_root=args.artifact_root,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
