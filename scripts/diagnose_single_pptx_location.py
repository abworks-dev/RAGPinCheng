"""Read-only structural diagnostics for one managed PPTX."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.document_locations import normalize_location_text


@dataclass(frozen=True)
class TargetPptx:
    source_path: Path
    original_filename: str
    object_sha256: str


def load_target(
    app_database: Path,
    content_root: Path,
    item_id: str,
    version_id: str,
) -> TargetPptx:
    database_uri = f"file:{app_database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        row = connection.execute(
            """SELECT v.original_filename,v.doc_type,o.storage_rel_path,o.sha256
                 FROM content_item_heads h
                 JOIN content_versions v
                   ON v.id=h.current_version_id AND v.item_id=h.item_id
                 JOIN content_objects o ON o.sha256=v.object_sha256
                WHERE h.item_id=? AND h.current_version_id=?""",
            (item_id, version_id),
        ).fetchone()
    if row is None:
        raise ValueError("managed_pptx_target_not_found")
    original_filename, doc_type, storage_rel_path, object_sha256 = map(str, row)
    if doc_type != "pptx":
        raise ValueError("managed_target_is_not_pptx")
    filename = Path(original_filename).name
    if filename != original_filename or filename in {"", ".", ".."}:
        raise ValueError("invalid_original_filename")
    root = content_root.resolve(strict=True)
    source = (root / storage_rel_path).resolve(strict=True)
    if source == root or root not in source.parents:
        raise ValueError("content_path_escape")
    if not source.is_file() or source.is_symlink():
        raise ValueError("managed_source_unavailable")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != object_sha256:
        raise ValueError("managed_source_sha256_mismatch")
    return TargetPptx(source, original_filename, object_sha256)


def _has_location(chunk: Any) -> bool:
    return getattr(chunk, "page_number", None) is not None or any(
        getattr(chunk, field, None)
        for field in ("paragraph_anchor", "sheet_name", "topic_id")
    )


def _probe_matches(target: str, candidate: str) -> bool:
    if len(candidate) < 4:
        return bool(candidate) and candidate == target
    probe = candidate[: min(80, len(candidate))]
    reverse_probe = target[: min(80, len(target))]
    return probe in target or reverse_probe in candidate


def build_location_report(
    markdown: str,
    slides: list[dict[str, Any]],
    parents: list[Any],
    children: list[Any],
) -> dict[str, Any]:
    normalized_slides = [
        (int(slide["slide_number"]), normalize_location_text(str(slide["text"])))
        for slide in slides
    ]
    parent_matches = []
    for ordinal, parent in enumerate(parents, start=1):
        target = normalize_location_text(parent.text)
        scored = [
            (
                SequenceMatcher(None, target, candidate).ratio(),
                slide_number,
                _probe_matches(target, candidate),
            )
            for slide_number, candidate in normalized_slides
        ]
        best = max(scored, key=lambda item: (item[0], -item[1])) if scored else (0.0, None, False)
        parent_matches.append(
            {
                "parent_ordinal": ordinal,
                "assigned_page_number": getattr(parent, "page_number", None),
                "best_slide_number": best[1],
                "best_similarity": round(best[0], 4),
                "probe_match_slide_numbers": [
                    slide_number
                    for _similarity, slide_number, matches in scored
                    if matches
                ],
            }
        )

    nonempty_lines = [line for line in markdown.splitlines() if line.strip()]
    return {
        "schema_version": 1,
        "markdown": {
            "characters": len(markdown),
            "nonempty_lines": len(nonempty_lines),
            "heading_lines": sum(line.lstrip().startswith("#") for line in nonempty_lines),
        },
        "slides": [
            {
                "slide_number": slide_number,
                "normalized_characters": len(normalized),
            }
            for slide_number, normalized in normalized_slides
        ],
        "chunks": {
            "parents": len(parents),
            "children": len(children),
            "located_parents": sum(_has_location(parent) for parent in parents),
            "located_children": sum(_has_location(child) for child in children),
            "unmatched_parent_ordinals": [
                ordinal
                for ordinal, parent in enumerate(parents, start=1)
                if not _has_location(parent)
            ],
            "child_content_types": dict(
                sorted(Counter(child.content_type for child in children).items())
            ),
        },
        "parent_matches": parent_matches,
    }


def diagnose_target(target: TargetPptx, work_dir: Path) -> dict[str, Any]:
    from src.chunk import chunk_document
    from src.document_locations import read_location_sidecar
    from src.indexing_pipeline import _build_pptx_doc

    work_dir.mkdir(parents=True, exist_ok=True)
    if work_dir.is_symlink():
        raise ValueError("diagnostic_work_dir_must_not_be_symlink")
    source = work_dir / "input" / target.original_filename
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target.source_path, source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != target.object_sha256:
        raise ValueError("diagnostic_copy_sha256_mismatch")

    document = _build_pptx_doc(
        source,
        lambda _status: None,
        parsed_dir=work_dir / "parsed",
        write_preview=False,
        force_parse=True,
    )
    locations = read_location_sidecar(document.location_map_path)
    slides = [
        {
            "slide_number": location.slide_number or location.page_number,
            "text": location.text,
        }
        for location in locations
        if location.slide_number is not None or location.page_number is not None
    ]
    markdown = document.markdown_path.read_text(encoding="utf-8")
    parents, children = chunk_document(document)
    return build_location_report(markdown, slides, parents, children)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-database", type=Path, required=True)
    parser.add_argument("--content-root", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()

    target = load_target(
        args.app_database,
        args.content_root,
        args.item_id,
        args.version_id,
    )
    report = diagnose_target(target, args.work_dir)
    print(
        "PPTX_SINGLE_FILE_DIAGNOSTIC "
        + json.dumps(report, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    main()
