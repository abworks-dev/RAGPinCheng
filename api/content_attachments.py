"""Opaque, versioned non-RAG assets. No model/CAD parsers or external services."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from src.config import CONTENT_ROOT
from .auth import CurrentUser, require_csrf
from .content_permissions import has_content_permission, require_content_permission
from .content_storage import ContentStorage
from .content_store import audit_event
from .db import get_db

from .content_attachment_types import ATTACHMENT_TYPES, ATTACHMENT_EXTENSIONS
IMAGE_MIMES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'pdf': 'application/pdf'}
MAX_PREVIEW_BYTES = 32 * 1024 * 1024
def _require_feature() -> None:
    # Keep the existing content-management feature gate and test override.
    from .routes_content import _require_feature as require_feature
    require_feature()


router = APIRouter(dependencies=[Depends(_require_feature)])
_storage = ContentStorage(CONTENT_ROOT)


def can_view_drafts(conn: sqlite3.Connection, user: CurrentUser) -> bool:
    return any(has_content_permission(conn, user, permission) for permission in ('item.upload', 'item.publish'))


def authorized_version(conn: sqlite3.Connection, user: CurrentUser, version_id: str) -> sqlite3.Row:
    row = conn.execute('''SELECT v.*,i.category_id,i.archived_at,o.storage_rel_path,o.size_bytes,
                          c.display_name AS category_name
        FROM content_versions v JOIN content_items i ON i.id=v.item_id
        JOIN content_objects o ON o.sha256=v.object_sha256
        JOIN category_nodes c ON c.id=i.category_id
        WHERE v.id=? AND i.archived_at IS NULL''', (version_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '资料文件不存在')
    has_attachment = row['doc_type'] in ATTACHMENT_TYPES or conn.execute(
        "SELECT 1 FROM content_versions WHERE item_id=? AND doc_type IN ('jpg','jpeg','png','dwg','dxf','rvt','che','nwd','nwc','ifc','skp','max') LIMIT 1",
        (row['item_id'],),
    ).fetchone() is not None
    if has_attachment and not can_view_drafts(conn, user):
        published = conn.execute("SELECT 1 FROM content_publications WHERE version_id=? AND status='published'", (version_id,)).fetchone()
        if row['lifecycle_status'] not in ('published', 'superseded') or not published:
            raise HTTPException(404, '资料文件不存在')
    return row


def preview_descriptor(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    kind = row['doc_type']
    sidecar = conn.execute('''SELECT p.preview_kind,o.storage_rel_path FROM content_version_previews p
        JOIN content_objects o ON o.sha256=p.object_sha256 WHERE p.version_id=?''', (row['version_id'] if 'version_id' in row.keys() else row['id'],)).fetchone()
    version_id = row['version_id'] if 'version_id' in row.keys() else row['id']
    if sidecar:
        try:
            if _storage.resolve_object(sidecar['storage_rel_path']).is_file():
                return {'preview_kind': sidecar['preview_kind'], 'preview_url': f'/api/admin/content/versions/{version_id}/preview', 'preview_status': 'ready'}
        except ValueError:
            pass
    if kind in {'jpg', 'jpeg', 'png', 'dxf', 'ifc'}:
        return {'preview_kind': 'image' if kind in IMAGE_MIMES else kind, 'preview_url': f'/api/admin/content/versions/{version_id}/preview', 'preview_status': 'ready'}
    return {'preview_kind': 'none', 'preview_url': None, 'preview_status': 'not_applicable'}


def validate_preview(path: Path, kind: str) -> None:
    with path.open('rb') as handle:
        head = handle.read(16)
    valid = (kind == 'png' and head.startswith(b'\x89PNG\r\n\x1a\n')) or (kind in {'jpg', 'jpeg'} and head.startswith(b'\xff\xd8\xff')) or (kind == 'pdf' and head.startswith(b'%PDF-'))
    if not valid:
        raise ValueError('invalid_preview_signature')


@router.get('/versions/{version_id}/info')
def version_info(version_id: str, user: CurrentUser = Depends(require_content_permission('item.view')), conn: sqlite3.Connection = Depends(get_db)):
    row = authorized_version(conn, user, version_id)
    result = {key: row[key] for key in ('item_id', 'version_number', 'original_filename', 'doc_type', 'lifecycle_status', 'source_origin', 'source_rel_path', 'category_id', 'category_name', 'created_at', 'updated_at', 'size_bytes')}
    head = conn.execute('SELECT current_version_id FROM content_item_heads WHERE item_id=?', (row['item_id'],)).fetchone()
    associated = conn.execute('SELECT 1 FROM content_version_previews WHERE version_id=?', (version_id,)).fetchone() is not None
    result.update(version_id=version_id, file_size=row['size_bytes'], rag_enabled=row['doc_type'] not in ATTACHMENT_TYPES,
                  is_associated=associated, current_version_id=head['current_version_id'] if head else None,
                  is_current=bool(head and head['current_version_id'] == version_id))
    result.update(preview_descriptor(conn, row))
    audit_event(conn, 'content.info_viewed', actor_user_id=user.id, item_id=row['item_id'], version_id=version_id)
    conn.commit()
    return result


@router.get('/versions/{version_id}/preview')
def version_preview(version_id: str, user: CurrentUser = Depends(require_content_permission('item.view')), conn: sqlite3.Connection = Depends(get_db)):
    row = authorized_version(conn, user, version_id)
    sidecar = conn.execute('''SELECT p.original_filename,p.preview_kind,o.storage_rel_path FROM content_version_previews p
        JOIN content_objects o ON o.sha256=p.object_sha256 WHERE p.version_id=?''', (version_id,)).fetchone()
    if sidecar:
        storage_path, filename = sidecar['storage_rel_path'], sidecar['original_filename']
        mime = 'application/pdf' if sidecar['preview_kind'] == 'pdf' else IMAGE_MIMES[Path(filename).suffix.lower()[1:]]
    elif row['doc_type'] in {'jpg', 'jpeg', 'png', 'dxf', 'ifc'}:
        storage_path, filename = row['storage_rel_path'], row['original_filename']
        mime = IMAGE_MIMES.get(row['doc_type'], 'application/octet-stream')
    else:
        raise HTTPException(404, '该版本尚未关联预览')
    try:
        path = _storage.resolve_object(storage_path)
        if not path.is_file():
            raise FileNotFoundError
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, '预览文件不存在')
    audit_event(conn, 'content.previewed', actor_user_id=user.id, item_id=row['item_id'], version_id=version_id)
    conn.commit()
    return FileResponse(path, filename=filename, media_type=mime, content_disposition_type='inline', headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@router.post('/versions/{version_id}/associated-preview', dependencies=[Depends(require_csrf)])
async def associate_preview(version_id: str, file: UploadFile = File(...), user: CurrentUser = Depends(require_content_permission('item.publish')), conn: sqlite3.Connection = Depends(get_db)):
    row = authorized_version(conn, user, version_id)
    if row['doc_type'] not in ATTACHMENT_TYPES:
        raise HTTPException(400, '仅附件资料支持手工关联预览')
    kind = Path(file.filename or '').suffix.lower()[1:]
    if kind not in IMAGE_MIMES:
        raise HTTPException(400, '预览仅支持 JPG、PNG 或 PDF')
    # Validate before persisting so rejected input cannot create orphan objects.
    head = await file.read(16)
    await file.seek(0)
    valid = (kind == 'png' and head.startswith(b'\x89PNG\r\n\x1a\n')) or (kind in {'jpg', 'jpeg'} and head.startswith(b'\xff\xd8\xff')) or (kind == 'pdf' and head.startswith(b'%PDF-'))
    if not valid:
        raise HTTPException(422, 'invalid_preview_signature')
    try:
        stored = await _storage.ingest_upload(file, batch_id=f'preview-{version_id}', max_bytes=MAX_PREVIEW_BYTES)
        validate_preview(stored.absolute_path, kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = int(time.time())
    # Re-check lifecycle inside the write transaction to avoid attaching to an archived version.
    try:
        conn.execute('BEGIN IMMEDIATE')
        authorized_version(conn, user, version_id)
        conn.execute('''INSERT OR IGNORE INTO content_objects(sha256,size_bytes,mime_type,storage_rel_path,created_at)
                        VALUES (?,?,?,?,?)''', (stored.sha256, stored.size_bytes, IMAGE_MIMES[kind], stored.storage_rel_path, now))
        conn.execute('''INSERT INTO content_version_previews(version_id,object_sha256,original_filename,preview_kind,created_by,created_at)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET
                       object_sha256=excluded.object_sha256,original_filename=excluded.original_filename,
                       preview_kind=excluded.preview_kind,created_by=excluded.created_by,created_at=excluded.created_at''',
                     (version_id, stored.sha256, file.filename, 'pdf' if kind == 'pdf' else 'image', user.id, now))
        conn.execute('INSERT INTO content_version_preview_history(version_id,object_sha256,created_at) VALUES (?,?,?)', (version_id, stored.sha256, now))
        audit_event(conn, 'content.preview_associated', actor_user_id=user.id, item_id=row['item_id'], version_id=version_id, metadata={'preview_sha256': stored.sha256, 'preview_kind': kind})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'version_id': version_id, **preview_descriptor(conn, row)}
