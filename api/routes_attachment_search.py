"""Narrow chat attachment access and separately permissioned administration."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src import config
from .auth import CurrentUser, require_user, require_csrf
from .content_permissions import has_content_permission, require_content_permission, require_any_content_permission
from .content_store import audit_event
from .db import get_db
from . import content_attachments
from .content_search import search_attachment_files, revalidate_attachment_results, visible_attachment_version, update_search_metadata
from .image_ocr_jobs import enqueue_image_ocr, cancel_image_ocr, job_dto, ocr_availability


def _feature():
    content_attachments._require_feature()
    if not getattr(config,'ATTACHMENT_SEARCH_ENABLED',True):
        raise HTTPException(404,'attachment_search_disabled')


router = APIRouter(dependencies=[Depends(_feature)])
admin_router = APIRouter(dependencies=[Depends(content_attachments._require_feature)])


def _visible(conn, version_id):
    row = visible_attachment_version(conn,version_id)
    if row is None:
        raise HTTPException(404,'attachment_not_found')
    return row


@router.get('/search')
def search(query: str = Query(min_length=1,max_length=200), categories: list[str] | None = Query(default=None), limit: int = Query(default=5,ge=1,le=20), user: CurrentUser = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cards = search_attachment_files(conn,query,categories=categories,limit=limit)
    for card in cards:
        card['can_download'] = has_content_permission(conn,user,'item.download')
    return {'results':cards}


@router.get('/versions/{version_id}/info')
def info(version_id: str, user: CurrentUser = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _visible(conn,version_id)
    card = revalidate_attachment_results(conn,[{'version_id':version_id}],user=user)[0]
    associated = conn.execute('SELECT 1 FROM content_version_previews WHERE version_id=?',(version_id,)).fetchone() is not None
    result = {**card,'lifecycle_status':'published','source_origin':row['source_origin'], 'source_rel_path':None,
              'category_id':row['category_id'],'category_name':row['category_path'], 'created_at':row['created_at'],
              'updated_at':row['updated_at'],'size_bytes':row['file_size'],'rag_enabled':False,'is_associated':associated,
              'current_version_id':version_id,'is_current':True,'preview_status':'ready' if card['can_preview'] else 'not_applicable',
              'preview_url':f'/api/attachments/versions/{version_id}/preview' if card['can_preview'] else None}
    audit_event(conn,'content.info_viewed',actor_user_id=user.id,item_id=row['item_id'],version_id=version_id)
    conn.commit()
    return result


@router.get('/versions/{version_id}/preview')
def preview(version_id: str, user: CurrentUser = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    _visible(conn,version_id)
    # Existing preview helper never returns opaque DWG/RVT/etc bytes, only safe preview types.
    return content_attachments.version_preview(version_id,user,conn)


@router.get('/versions/{version_id}/download')
def download(version_id: str, user: CurrentUser = Depends(require_content_permission('item.download')), conn: sqlite3.Connection = Depends(get_db)):
    row = _visible(conn,version_id)
    try:
        path = content_attachments._storage.resolve_object(row['storage_rel_path'])
        if not path.is_file():
            raise ValueError('missing')
    except ValueError as exc:
        raise HTTPException(404,'attachment_not_found') from exc
    audit_event(conn,'content.downloaded',actor_user_id=user.id,item_id=row['item_id'],version_id=version_id)
    conn.commit()
    return FileResponse(path,filename=row['original_filename'],media_type='application/octet-stream',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


class SearchMetadata(BaseModel):
    description: str = Field(default='',max_length=4000)
    tags: list[str] = Field(default_factory=list,max_length=50)
    project: str = Field(default='',max_length=300)
    aliases: list[str] = Field(default_factory=list,max_length=50)


def _metadata(conn,version_id):
    row = conn.execute('SELECT * FROM attachment_search_metadata WHERE version_id=?',(version_id,)).fetchone()
    return {'version_id':version_id,'description':row['description'] if row else '', 'tags':json.loads(row['tags_json']) if row else [],
            'project':row['project'] if row else '', 'aliases':json.loads(row['aliases_json']) if row else [],'revision':row['revision'] if row else 0}


def _editable(conn,user,version_id):
    row = content_attachments.authorized_version(conn,user,version_id)
    if not has_content_permission(conn,user,'item.publish') and row['created_by']!=user.id:
        raise HTTPException(403,'attachment_metadata_permission_required')
    return row


_editor = require_any_content_permission(frozenset({'item.upload','item.publish'}))


@admin_router.get('/versions/{version_id}/search-info')
def search_info(version_id: str, user: CurrentUser = Depends(_editor), conn: sqlite3.Connection = Depends(get_db)):
    _editable(conn,user,version_id)
    return _metadata(conn,version_id)


@admin_router.patch('/versions/{version_id}/search-metadata',dependencies=[Depends(require_csrf)])
def metadata(version_id: str, body: SearchMetadata, user: CurrentUser = Depends(_editor), conn: sqlite3.Connection = Depends(get_db)):
    row = _editable(conn,user,version_id)
    try:
        conn.execute('BEGIN IMMEDIATE')
        _editable(conn,user,version_id)
        update_search_metadata(conn,version_id,**body.model_dump())
        audit_event(conn,'content.search_metadata_updated',actor_user_id=user.id,item_id=row['item_id'],version_id=version_id)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(422,str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    return _metadata(conn,version_id)


@admin_router.get('/versions/{version_id}/ocr')
def ocr_status(version_id: str, user: CurrentUser = Depends(_editor), conn: sqlite3.Connection = Depends(get_db)):
    _editable(conn,user,version_id)
    head = conn.execute('SELECT job_id FROM image_ocr_heads WHERE version_id=?',(version_id,)).fetchone()
    jobs = conn.execute('SELECT * FROM image_ocr_jobs WHERE version_id=? ORDER BY attempt_number DESC LIMIT 20',(version_id,)).fetchall()
    return {'version_id':version_id,'current_job_id':head['job_id'] if head else None,**ocr_availability(),'jobs':[job_dto(row) for row in jobs]}


@admin_router.post('/versions/{version_id}/ocr',dependencies=[Depends(require_csrf)])
def ocr_enqueue(version_id: str, user: CurrentUser = Depends(require_content_permission('item.publish')), conn: sqlite3.Connection = Depends(get_db)):
    row = content_attachments.authorized_version(conn,user,version_id)
    try:
        conn.execute('BEGIN IMMEDIATE')
        content_attachments.authorized_version(conn,user,version_id)
        result = enqueue_image_ocr(conn,version_id)
        audit_event(conn,'content.ocr_enqueued',actor_user_id=user.id,item_id=row['item_id'],version_id=version_id)
        conn.commit()
        return result
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(409,str(exc)) from exc
    except Exception:
        conn.rollback()
        raise


@admin_router.post('/versions/{version_id}/ocr/cancel',dependencies=[Depends(require_csrf)])
def ocr_cancel(version_id: str, user: CurrentUser = Depends(require_content_permission('item.publish')), conn: sqlite3.Connection = Depends(get_db)):
    row = content_attachments.authorized_version(conn,user,version_id)
    conn.execute('BEGIN IMMEDIATE')
    try:
        content_attachments.authorized_version(conn,user,version_id)
        count = cancel_image_ocr(conn,version_id)
        audit_event(conn,'content.ocr_cancelled',actor_user_id=user.id,item_id=row['item_id'],version_id=version_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'version_id':version_id,'cancelled':count}
