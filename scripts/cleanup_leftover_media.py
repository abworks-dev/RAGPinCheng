"""Inventory and clean up leftover managed media records without a catalog shell.

Context
-------
The media workbench lists every ``media_assets`` row (``status <> 'archived'``)
and advertises ``archive_media`` purely from media state.  The archive
endpoint, however, requires a non-archived ``content_items(
content_kind='media_transcript')`` catalog shell.  Media uploaded before the
unified upload flow (decision 0006) that were never promoted get no shell, so
they can never be archived and stay stuck in the transcription task list with
"该视频没有可归档的转写资料".

This script supports three modes:

* ``inventory`` (read-only): list every leftover record, optionally with
  parents.sqlite / Qdrant indexed-evidence counts, and write a frozen
  per-item detail JSON for operator review and subsequent validation.
* ``archive``: backfill the catalog shell (idempotent
  ``ensure_media_transcript_catalog_item``) and archive each record through
  the application's own ``archive_content_item`` path, so the recycle-bin /
  restore / audit behavior is identical to the UI.
* ``delete``: permanently remove the leftover records (media row, jobs,
  versions, heads, publication index history, catalog shells, related rows)
  and the local media directory.  Delete fails closed on ANY indexed
  evidence (active jobs, transcript head, parents.sqlite rows, Qdrant
  points), because that would orphan RAG content.

The script is environment-agnostic: ``--database`` points at ``app.sqlite``.
It runs inside the backend container (or any environment with the app
dependencies) so it can reuse ``api.content_store`` / catalog helpers.
"""  # noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# When executed from a copied location (e.g. /tmp inside the container),
# fall back to the process working directory so `api` / `src` resolve.
_cwd = os.getcwd()
if str(_cwd) not in sys.path:
    sys.path.insert(0, str(_cwd))

ARCHIVE_CONFIRMATION = "ARCHIVE_LEFT_OVER_MEDIA"
DELETE_CONFIRMATION = "DELETE_LEFT_OVER_MEDIA"

CANDIDATE_SQL = """
SELECT m.media_id AS media_id,
       m.title AS title,
       m.original_filename AS original_filename,
       m.status AS status,
       m.storage_kind AS storage_kind,
       m.transcript_origin AS transcript_origin,
       m.created_at AS created_at,
       m.updated_at AS updated_at,
       m.error AS error,
       m.target_category_id AS target_category_id,
       m.storage_rel_path AS storage_rel_path,
       (SELECT COUNT(*) FROM transcription_jobs j
         WHERE j.media_id=m.media_id) AS job_count,
       (SELECT j.status FROM transcription_jobs j
         WHERE j.media_id=m.media_id
         ORDER BY j.attempt_number DESC,j.created_at DESC LIMIT 1) AS job_status,
       (SELECT COUNT(*) FROM transcription_jobs j
         WHERE j.media_id=m.media_id AND j.status IN ('pending','running')) AS active_job_count,
       (SELECT COUNT(*) FROM transcript_versions v
         WHERE v.media_id=m.media_id) AS version_count,
       (SELECT v.review_status FROM transcript_versions v
         WHERE v.media_id=m.media_id
         ORDER BY v.created_at DESC,v.id DESC LIMIT 1) AS review_status,
       (SELECT v.publication_status FROM transcript_versions v
         WHERE v.media_id=m.media_id
         ORDER BY v.created_at DESC,v.id DESC LIMIT 1) AS publication_status,
       EXISTS(SELECT 1 FROM media_transcript_heads h
              WHERE h.media_id=m.media_id) AS has_head,
       EXISTS(SELECT 1 FROM transcript_publication_index_jobs p
                JOIN transcript_versions v ON v.id=p.transcript_version_id
              WHERE v.media_id=m.media_id) AS has_publication_index_jobs,
       EXISTS(SELECT 1 FROM media_publication_requests r
              WHERE r.media_id=m.media_id) AS has_publication_request,
       EXISTS(SELECT 1 FROM media_replacements r
              WHERE r.source_media_id=m.media_id OR r.candidate_media_id=m.media_id) AS has_replacements,
       EXISTS(SELECT 1 FROM media_metadata_revisions r
              WHERE r.media_id=m.media_id) AS has_metadata_revisions,
       EXISTS(SELECT 1 FROM upload_batch_entries e
              WHERE e.media_id=m.media_id) AS has_batch_entry,
       EXISTS(SELECT 1 FROM external_media_entries e
              WHERE e.media_id=m.media_id) AS has_external_entry,
       EXISTS(SELECT 1 FROM content_items i
              WHERE i.media_id=m.media_id AND i.content_kind='media_transcript'
                AND i.archived_at IS NOT NULL) AS has_archived_shell,
       (SELECT i.id FROM content_items i
         WHERE i.media_id=m.media_id AND i.archived_at IS NULL LIMIT 1) AS shell_id,
       (SELECT i.content_kind FROM content_items i
         WHERE i.media_id=m.media_id AND i.archived_at IS NULL LIMIT 1) AS shell_kind
FROM media_assets m
WHERE m.status <> 'archived'
  AND NOT EXISTS (
      SELECT 1 FROM content_items i
      WHERE i.media_id=m.media_id AND i.content_kind='media_transcript'
        AND i.archived_at IS NULL
  )
ORDER BY m.created_at DESC
"""


class LeftoverMediaError(ValueError):
    pass


def _line(item: dict[str, object]) -> str:
    return json.dumps(
        {
            "media_id": item["media_id"],
            "status": item["status"],
            "title": item["title"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def manifest_sha256(items: list[dict[str, object]]) -> str:
    payload = "\n".join(sorted(_line(item) for item in items))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _media_dirs(
    item: dict[str, object],
    roots: list[Path],
) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        root = root.resolve(strict=False)
        candidate = root / str(item["media_id"])
        if candidate.is_dir():
            found.append(candidate)
    return found


def _dir_size(directory: Path) -> int:
    total = 0
    try:
        for entry in directory.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        return -1
    return total


def qdrant_point_count(media_id: str) -> int | None:
    """Return 1 when any indexed point carries this media_id, else 0.

    Uses ``scroll`` with ``limit=1`` instead of the approximate ``count``
    API: the approximate count path can report the collection total instead
    of a filtered count, which would block cleanup with a false positive.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models

        from src.config import COLLECTION, QDRANT_URL
    except Exception as exc:  # pragma: no cover - environment dependent
        raise LeftoverMediaError(f"qdrant_check_unavailable: {exc}") from exc
    client = QdrantClient(url=QDRANT_URL)
    try:
        records, _next = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="media_id", match=models.MatchValue(value=media_id)
                    )
                ]
            ),
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        return int(len(records) > 0)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise LeftoverMediaError(f"qdrant_check_failed: {exc}") from exc
    finally:
        client.close()


def find_candidates(
    conn: sqlite3.Connection,
    *,
    media_roots: list[Path],
    parents_database: Path | None,
    with_qdrant: bool,
) -> list[dict[str, object]]:
    rows = conn.execute(CANDIDATE_SQL).fetchall()
    parents_conn: sqlite3.Connection | None = None
    if parents_database is not None and parents_database.exists():
        parents_conn = sqlite3.connect(
            f"file:{parents_database.as_posix()}?mode=ro", uri=True
        )
    items: list[dict[str, object]] = []
    try:
        for row in rows:
            item = dict(row)
            directories = _media_dirs(item, media_roots)
            item["media_dir_count"] = len(directories)
            item["media_dir_paths"] = [str(path) for path in directories]
            item["media_dir_bytes"] = (
                _dir_size(directories[0]) if directories else 0
            )
            item["parents_count"] = 0
            if parents_conn is not None:
                try:
                    item["parents_count"] = int(
                        parents_conn.execute(
                            "SELECT COUNT(*) FROM parents WHERE media_id=?",
                            (str(item["media_id"]),),
                        ).fetchone()[0]
                    )
                except sqlite3.Error:
                    item["parents_count"] = -1
            item["qdrant_points"] = None
            if with_qdrant:
                try:
                    item["qdrant_points"] = qdrant_point_count(
                        str(item["media_id"])
                    )
                except LeftoverMediaError:
                    item["qdrant_points"] = -1
            items.append(item)
    finally:
        if parents_conn is not None:
            parents_conn.close()
    return items


def summarize(items: list[dict[str, object]]) -> dict[str, object]:
    blockers_archive = [
        item["media_id"]
        for item in items
        if item["storage_kind"] == "external" or item["shell_kind"] not in (None, "media_transcript") or bool(item["has_archived_shell"])
    ]
    blockers_delete = [
        item["media_id"]
        for item in items
        if int(item["active_job_count"]) > 0
        or bool(item["has_head"])
        or int(item["parents_count"]) > 0
        or (item["qdrant_points"] is not None and int(item["qdrant_points"]) > 0)
    ]
    return {
        "candidate_count": len(items),
        "archive_blocked_count": len(blockers_archive),
        "delete_blocked_count": len(blockers_delete),
        "by_status": dict(sorted(Counter(str(item["status"]) for item in items).items())),
        "by_storage_kind": dict(
            sorted(Counter(str(item["storage_kind"]) for item in items).items())
        ),
        "archive_blocked_media_ids": sorted(str(m) for m in blockers_archive),
        "delete_blocked_media_ids": sorted(str(m) for m in blockers_delete),
        "manifest_sha256": manifest_sha256(items),
    }


def _load_actor(conn: sqlite3.Connection, actor_user_id: int) -> None:
    actor = conn.execute(
        "SELECT 1 FROM users WHERE id=? AND is_active=1 AND role='admin'",
        (actor_user_id,),
    ).fetchone()
    if actor is None:
        raise LeftoverMediaError("active_admin_actor_not_found")


def archive_leftover(
    conn: sqlite3.Connection,
    items: list[dict[str, object]],
    *,
    actor_user_id: int,
) -> dict[str, object]:
    from api.content_store import archive_content_item
    from api.media_transcript_catalog import ensure_media_transcript_catalog_item

    _load_actor(conn, actor_user_id)
    succeeded: list[str] = []
    blocked: dict[str, str] = {}
    now = int(time.time())
    for item in items:
        media_id = str(item["media_id"])
        if item["storage_kind"] == "external":
            blocked[media_id] = "共享目录视频为只读来源，不能移入回收站"
            continue
        if item["shell_kind"] not in (None, "media_transcript"):
            blocked[media_id] = "目录壳类型冲突，不能补壳归档"
            continue
        if bool(item["has_archived_shell"]):
            blocked[media_id] = "已存在归档目录壳，只能在回收站恢复或删除"
            continue
        try:
            item_id = ensure_media_transcript_catalog_item(
                conn, media_id=media_id, now=now
            )
            # ensure_* opens an implicit transaction; close it so the archive
            # helper can start its own atomic transaction.  A later failure
            # leaves the idempotent shell in place, so the record becomes a
            # normal (archivable or deletable) candidate on the next run.
            conn.commit()
            archive_content_item(
                conn,
                item_id,
                expected_version_id=f"media-pending-{media_id}",
                actor_user_id=actor_user_id,
                can_archive_draft=True,
                can_archive_published=True,
            )
            succeeded.append(media_id)
        except ValueError as exc:
            blocked[media_id] = str(exc)
        except Exception:
            conn.rollback()
            raise
    return {
        "archived_count": len(succeeded),
        "archived_media_ids": sorted(succeeded),
        "blocked": blocked,
    }


def _delete_rows_for_media(
    conn: sqlite3.Connection,
    media_ids: list[str],
    item_ids: list[str],
) -> None:
    """Delete rows child-first so ``PRAGMA foreign_keys=ON`` never trips.

    Order matters: every table that references ``content_items``,
    ``transcript_versions`` or ``media_assets`` must be emptied before the
    referenced row goes away.  Transcript versions also reference each other
    through ``supersedes_version_id`` and ``derived_from_version_id``, so the
    version set is removed in two passes (leaves first).
    """
    if not media_ids:
        return
    media_placeholders = ",".join("?" for _ in media_ids)
    version_ids = (
        "SELECT id FROM transcript_versions WHERE media_id IN (" + media_placeholders + ")"
    )
    # 1) Audit events referencing the items or their transcript versions.
    if item_ids:
        conn.execute(
            f"""DELETE FROM content_audit_events
                WHERE item_id IN ({','.join('?' for _ in item_ids)})
                   OR version_id IN ({version_ids})""",
            [*item_ids, *media_ids],
        )
    else:
        conn.execute(f"DELETE FROM content_audit_events WHERE version_id IN ({version_ids})", media_ids)
    # 2) Transcript artifacts reference transcript_versions.
    conn.execute(
        f"DELETE FROM transcript_version_artifacts WHERE version_id IN ({version_ids})",
        media_ids,
    )
    # 3) Managed-content rows that can only exist for real documents (media
    #    never builds them), deleted defensively via their content_versions /
    #    catalog shells.
    if item_ids:
        item_placeholders = ",".join("?" for _ in item_ids)
        conn.execute(
            f"""DELETE FROM content_reviews
                WHERE version_id IN (
                    SELECT id FROM content_versions WHERE item_id IN ({item_placeholders})
                )""",
            item_ids,
        )
        conn.execute(
            f"""DELETE FROM content_publications
                WHERE version_id IN (
                    SELECT id FROM content_versions WHERE item_id IN ({item_placeholders})
                )""",
            item_ids,
        )
        conn.execute(
            f"""DELETE FROM content_index_jobs
                WHERE version_id IN (
                    SELECT id FROM content_versions WHERE item_id IN ({item_placeholders})
                )""",
            item_ids,
        )
        conn.execute(
            f"""DELETE FROM content_versions
                WHERE item_id IN ({item_placeholders})
                   OR transcript_version_id IN ({version_ids})""",
            [*item_ids, *media_ids],
        )
        conn.execute(
            f"DELETE FROM content_item_heads WHERE item_id IN ({item_placeholders})",
            item_ids,
        )
        conn.execute(
            f"DELETE FROM content_reclassification_jobs WHERE item_id IN ({item_placeholders})",
            item_ids,
        )
    else:
        conn.execute(
            f"DELETE FROM content_versions WHERE transcript_version_id IN ({version_ids})",
            media_ids,
        )
    # 4) Publication index jobs / heads reference transcript_versions.
    conn.execute(
        f"DELETE FROM transcript_publication_index_jobs WHERE transcript_version_id IN ({version_ids})",
        media_ids,
    )
    conn.execute(
        f"DELETE FROM media_transcript_heads WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    # 5) Replacements and metadata revisions reference content_items /
    #    transcript_versions / media_assets.
    conn.execute(
        f"""DELETE FROM media_replacements
            WHERE source_media_id IN ({media_placeholders})
               OR candidate_media_id IN ({media_placeholders})""",
        [*media_ids, *media_ids],
    )
    conn.execute(
        f"DELETE FROM media_metadata_revisions WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    # 6) Versions before jobs (transcript_versions.transcription_job_id
    #    references transcription_jobs).  The version set is removed in an
    #    iterative leaf-first loop because versions reference each other
    #    through supersedes_version_id / derived_from_version_id — chains of
    #    any length collapse one hop at a time; a leftover set that cannot
    #    shrink means a cycle and fails closed.
    while True:
        leaf_ids = [
            str(row["id"])
            for row in conn.execute(
                f"""SELECT id FROM transcript_versions
                    WHERE media_id IN ({media_placeholders})
                      AND id NOT IN (
                        SELECT supersedes_version_id FROM transcript_versions
                         WHERE media_id IN ({media_placeholders})
                           AND supersedes_version_id IS NOT NULL
                        UNION
                        SELECT derived_from_version_id FROM transcript_versions
                         WHERE media_id IN ({media_placeholders})
                           AND derived_from_version_id IS NOT NULL
                      )""",
                [*media_ids, *media_ids, *media_ids],
            ).fetchall()
        ]
        if not leaf_ids:
            remaining = conn.execute(
                f"SELECT 1 FROM transcript_versions WHERE media_id IN ({media_placeholders}) LIMIT 1",
                media_ids,
            ).fetchone()
            if remaining is not None:
                raise LeftoverMediaError("transcript_version_reference_cycle")
            break
        conn.executemany(
            "DELETE FROM transcript_versions WHERE id=?",
            [(leaf_id,) for leaf_id in leaf_ids],
        )
    # 7) Jobs last among the transcript chain.
    conn.execute(
        f"DELETE FROM transcription_jobs WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    # 8) Catalog shells reference media_assets.
    conn.execute(
        f"DELETE FROM content_items WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    conn.execute(
        f"DELETE FROM media_publication_requests WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    conn.execute(
        f"DELETE FROM upload_batch_entries WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    conn.execute(
        f"DELETE FROM external_media_entries WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    conn.execute(
        f"DELETE FROM index_jobs WHERE media_id IN ({media_placeholders})",
        media_ids,
    )
    # 9) The media row itself, last.
    conn.execute(
        f"DELETE FROM media_assets WHERE media_id IN ({media_placeholders})",
        media_ids,
    )


def _stage_media_dirs(
    items: list[dict[str, object]],
    roots: list[Path],
) -> list[tuple[Path, Path]]:
    staged: list[tuple[Path, Path]] = []
    for item in items:
        for directory in _media_dirs(item, roots):
            attributes = getattr(directory.lstat(), "st_file_attributes", 0)
            if directory.is_symlink() or bool(attributes & 0x400):  # FILE_ATTRIBUTE_REPARSE_POINT
                raise LeftoverMediaError(f"media_dir_is_reparse_point: {directory}")
            pending = directory.parent / f".cleanup-pending-{item['media_id']}-{time.time_ns()}"
            directory.replace(pending)
            staged.append((directory, pending))
    return staged


def delete_leftover(
    conn: sqlite3.Connection,
    items: list[dict[str, object]],
    *,
    actor_user_id: int,
    media_roots: list[Path],
) -> dict[str, object]:
    from api.content_store import audit_event

    _load_actor(conn, actor_user_id)
    blocked: dict[str, str] = {}
    for item in items:
        media_id = str(item["media_id"])
        if int(item["active_job_count"]) > 0:
            blocked[media_id] = "视频正在处理，不能删除"
        elif bool(item["has_head"]):
            blocked[media_id] = "已有正式转录 head，不能直接删除"
        elif int(item["parents_count"]) > 0:
            blocked[media_id] = "存在已索引 Parent，不能直接删除"
        elif item["qdrant_points"] is not None and int(item["qdrant_points"]) > 0:
            blocked[media_id] = "存在 Qdrant Point，不能直接删除"
        elif item["qdrant_points"] == -1:
            blocked[media_id] = "Qdrant 检查不可用，删除前必须确认"
    if blocked:
        raise LeftoverMediaError(
            "delete_blocked: " + json.dumps(blocked, ensure_ascii=False, sort_keys=True)
        )

    media_ids = [str(item["media_id"]) for item in items]
    item_ids = [
        str(item["shell_id"])
        for item in items
        if item["shell_id"] is not None
    ]
    staged = _stage_media_dirs(items, media_roots)
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        _delete_rows_for_media(conn, media_ids, item_ids)
        for item in items:
            audit_event(
                conn,
                "content.media_leftover_deleted",
                actor_user_id=actor_user_id,
                metadata={
                    "media_id": str(item["media_id"]),
                    "title": str(item["title"]),
                    "storage_kind": str(item["storage_kind"]),
                    "mode": "delete",
                },
            )
        conn.commit()
    except Exception:
        conn.rollback()
        for original, pending in reversed(staged):
            if pending.exists():
                pending.replace(original)
        raise
    removed_bytes = 0
    for _original, pending in staged:
        if pending.exists():
            removed_bytes += _dir_size(pending)
            shutil.rmtree(pending)
    return {
        "deleted_count": len(media_ids),
        "deleted_media_ids": sorted(media_ids),
        "media_dir_bytes_removed": removed_bytes,
    }


def run_inventory(
    conn: sqlite3.Connection,
    *,
    media_roots: list[Path],
    parents_database: Path | None,
    with_qdrant: bool,
    detail_output: Path | None,
) -> dict[str, object]:
    items = find_candidates(
        conn,
        media_roots=media_roots,
        parents_database=parents_database,
        with_qdrant=with_qdrant,
    )
    report = summarize(items)
    if detail_output is not None:
        detail_output.write_text(
            json.dumps(items, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        report["detail_output"] = str(detail_output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory / archive / delete leftover managed media records"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--parents-database", type=Path)
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--mode",
        choices=("inventory", "archive", "delete"),
        default="inventory",
    )
    parser.add_argument("--detail-output", type=Path)
    parser.add_argument("--with-qdrant", action="store_true")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--actor-user-id", type=int)
    parser.add_argument("--confirm")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    media_roots = [Path(path).resolve() for path in args.media_root]

    if args.mode in ("archive", "delete") and not args.apply:
        # Require an explicit --apply for any write mode.
        raise LeftoverMediaError(f"{args.mode}_apply_required")
    expected_confirmation = (
        ARCHIVE_CONFIRMATION if args.mode == "archive" else DELETE_CONFIRMATION
    )
    if args.apply and (
        args.confirm != expected_confirmation or args.actor_user_id is None
    ):
        raise LeftoverMediaError("apply_confirmation_required")

    from api.db import connect

    conn = connect(args.database)
    try:
        if args.mode == "inventory":
            report = run_inventory(
                conn,
                media_roots=media_roots,
                parents_database=args.parents_database,
                with_qdrant=args.with_qdrant,
                detail_output=args.detail_output,
            )
            report["status"] = "dry_run"
        else:
            items = find_candidates(
                conn,
                media_roots=media_roots,
                parents_database=args.parents_database,
                with_qdrant=args.with_qdrant or args.mode == "delete",
            )
            frozen = summarize(items)
            if args.manifest_sha256 != frozen["manifest_sha256"]:
                raise LeftoverMediaError(
                    f"inventory_manifest_mismatch expected={args.manifest_sha256} actual={frozen['manifest_sha256']}"
                )
            if args.expected_count is not None and args.expected_count != frozen["candidate_count"]:
                raise LeftoverMediaError(
                    f"expected_count_mismatch expected={args.expected_count} actual={frozen['candidate_count']}"
                )
            if args.mode == "archive":
                result = archive_leftover(conn, items, actor_user_id=args.actor_user_id)
                report = {**frozen, **result, "status": "applied"}
            else:
                result = delete_leftover(
                    conn,
                    items,
                    actor_user_id=args.actor_user_id,
                    media_roots=media_roots,
                )
                report = {**frozen, **result, "status": "applied"}
                report["verify"] = run_inventory(
                    conn,
                    media_roots=media_roots,
                    parents_database=args.parents_database,
                    with_qdrant=False,
                    detail_output=None,
                )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LeftoverMediaError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)