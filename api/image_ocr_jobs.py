"""Durable, leased original-image OCR. Internal HTTP only; no cloud fallback."""
from __future__ import annotations

import hashlib
import json
import ipaddress
import logging
import sqlite3
import struct
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src import config
from .content_storage import ContentStorage
from .db import connect

logger = logging.getLogger(__name__)
JOB_FIELDS = ('id','version_id','source_sha256','attempt_number','status','error_code','created_at','updated_at')
MAX_TEXT_CHARS = 200_000


def job_dto(row) -> dict:
    return {key: row[key] for key in JOB_FIELDS}


def enqueue_image_ocr(conn: sqlite3.Connection, version_id: str, *, automatic: bool = False) -> dict | None:
    if not getattr(config, 'IMAGE_OCR_ENABLED', False):
        if automatic:
            return None
        raise ValueError('image_ocr_disabled')
    row = conn.execute('''SELECT v.*,i.archived_at FROM content_versions v JOIN content_items i ON i.id=v.item_id
        WHERE v.id=?''', (version_id,)).fetchone()
    if row is None or row['doc_type'] not in {'jpg','jpeg','png'} or row['archived_at'] is not None:
        if automatic:
            return None
        raise ValueError('original_image_not_found')
    # Never accept associated previews, CAD bytes, or implicit historical scans.
    active = conn.execute("SELECT * FROM image_ocr_jobs WHERE version_id=? AND status IN ('pending','running')", (version_id,)).fetchone()
    if active:
        return job_dto(active)
    now = int(time.time())
    attempt = conn.execute('SELECT COALESCE(max(attempt_number),0)+1 FROM image_ocr_jobs WHERE version_id=?', (version_id,)).fetchone()[0]
    job_id = 'ocr_' + uuid.uuid4().hex
    conn.execute('''INSERT INTO image_ocr_jobs(id,version_id,source_sha256,attempt_number,status,created_at,updated_at)
        VALUES (?,?,?,?,'pending',?,?)''', (job_id,version_id,row['object_sha256'],attempt,now,now))
    return job_dto(conn.execute('SELECT * FROM image_ocr_jobs WHERE id=?', (job_id,)).fetchone())


def lease_image_ocr(conn: sqlite3.Connection, *, now: int | None = None) -> dict | None:
    if not getattr(config, 'IMAGE_OCR_ENABLED', False):
        return None
    now = int(time.time()) if now is None else now
    conn.execute('BEGIN IMMEDIATE')
    try:
        # Restart recovery only requeues expired leases, not other live workers.
        conn.execute("UPDATE image_ocr_jobs SET status='pending',lease_token=NULL,lease_until=NULL,updated_at=? WHERE status='running' AND lease_until<=?", (now,now))
        row = conn.execute("SELECT * FROM image_ocr_jobs WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
        if row is None:
            conn.commit()
            return None
        token = uuid.uuid4().hex
        lease_seconds = max(60, int(getattr(config,'IMAGE_OCR_TIMEOUT_SECONDS',30))*2+30)
        conn.execute("UPDATE image_ocr_jobs SET status='running',lease_token=?,lease_until=?,updated_at=? WHERE id=?", (token,now+lease_seconds,now,row['id']))
        job = dict(conn.execute('SELECT * FROM image_ocr_jobs WHERE id=?', (row['id'],)).fetchone())
        conn.commit()
        return job
    except Exception:
        conn.rollback()
        raise


def cancel_image_ocr(conn: sqlite3.Connection, version_id: str) -> int:
    cursor = conn.execute("UPDATE image_ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,updated_at=? WHERE version_id=? AND status IN ('pending','running')", (int(time.time()),version_id))
    return cursor.rowcount


def _image_dimensions(path: Path, kind: str) -> tuple[int,int]:
    # Header-only validation avoids decoder allocation/decompression bombs in API workers.
    with path.open('rb') as handle:
        if kind == 'png':
            head = handle.read(24)
            if len(head)!=24 or head[:8] != bytes.fromhex('89504e470d0a1a0a') or head[12:16] != b'IHDR':
                raise ValueError('image_ocr_invalid_image')
            return struct.unpack('>II',head[16:24])
        if handle.read(2) != b'\xff\xd8':
            raise ValueError('image_ocr_invalid_image')
        for _ in range(4096):
            marker = handle.read(1)
            if marker != b'\xff':
                raise ValueError('image_ocr_invalid_image')
            while marker == b'\xff':
                marker = handle.read(1)
            if not marker or marker in (b'\xd9',b'\xda'):
                break
            code = marker[0]
            if code in (0x01, *range(0xd0,0xd9)):
                continue
            raw = handle.read(2)
            if len(raw)!=2:
                break
            length = struct.unpack('>H',raw)[0]
            if length<2:
                break
            if code in {0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf}:
                dims = handle.read(5)
                if len(dims)==5:
                    height,width = struct.unpack('>HH',dims[1:])
                    return width,height
                break
            handle.seek(length-2,1)
    raise ValueError('image_ocr_invalid_image')


def _local_endpoint() -> tuple[str, str]:
    url = getattr(config,'IMAGE_OCR_URL','http://image-ocr:8091').rstrip('/')
    parsed = urlsplit(url)
    # Require explicitly configured internal endpoint; reject credentials and redirects.
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('image_ocr_invalid_url')
    host = parsed.hostname.lower()
    if host not in {'image-ocr','localhost'}:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError('image_ocr_nonlocal_url') from exc
        if not (address.is_loopback or address.is_private) or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError('image_ocr_nonlocal_url')
    if parsed.path not in {'','/'}:
        raise ValueError('image_ocr_invalid_url')
    token = getattr(config,'IMAGE_OCR_TOKEN','')
    if not token or not token.strip():
        raise ValueError('image_ocr_token_required')
    return url, token


def ocr_availability() -> dict:
    enabled = bool(getattr(config, 'IMAGE_OCR_ENABLED', False))
    try:
        _local_endpoint()
    except ValueError as exc:
        return {'enabled':enabled,'available':False,'reason_code':str(exc)}
    return {'enabled':enabled,'available':True,'reason_code':None if enabled else 'image_ocr_disabled'}


def local_ocr_client(path: Path, *, kind: str, filename: str) -> dict:
    max_bytes = int(getattr(config,'IMAGE_OCR_MAX_BYTES',32*1024*1024))
    if path.stat().st_size>max_bytes:
        raise ValueError('image_ocr_too_large')
    width,height = _image_dimensions(path,kind)
    if not width or not height or max(width,height)>32768 or width*height>int(getattr(config,'IMAGE_OCR_MAX_PIXELS',80_000_000)):
        raise ValueError('image_ocr_pixel_limit')
    url, token = _local_endpoint()
    try:
        with httpx.Client(timeout=float(getattr(config,'IMAGE_OCR_TIMEOUT_SECONDS',30)),follow_redirects=False,trust_env=False) as client, path.open('rb') as handle:
            with client.stream('POST',url+'/v1/ocr',headers={'Authorization':'Bearer '+token},
                               files={'file':(filename,handle,'image/png' if kind=='png' else 'image/jpeg')}) as response:
                if response.status_code!=200:
                    raise ValueError('image_ocr_service_' + str(response.status_code))
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload)>8*1024*1024:
                        raise ValueError('image_ocr_response_too_large')
        result = json.loads(payload)
    except httpx.TimeoutException as exc:
        raise ValueError('image_ocr_timeout') from exc
    except httpx.HTTPError as exc:
        raise ValueError('image_ocr_unavailable') from exc
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:
        raise ValueError('image_ocr_invalid_response') from exc
    if not isinstance(result,dict) or not isinstance(result.get('text'),str) or len(result['text'])>MAX_TEXT_CHARS or not isinstance(result.get('blocks'),list) or not isinstance(result.get('engine'),str) or not isinstance(result.get('model_version'),str):
        raise ValueError('image_ocr_invalid_response')
    return result


def finish_image_ocr(conn: sqlite3.Connection, job: dict, *, result: dict | None = None, error_code: str | None = None) -> bool:
    from .content_search import index_attachment_version
    now = int(time.time())
    conn.execute('BEGIN IMMEDIATE')
    try:
        current = conn.execute("SELECT * FROM image_ocr_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (job['id'],job['lease_token'],now)).fetchone()
        if current is None:
            conn.rollback()
            return False
        source = conn.execute('''SELECT v.object_sha256,i.archived_at,v.lifecycle_status FROM content_versions v
            JOIN content_items i ON i.id=v.item_id WHERE v.id=?''', (job['version_id'],)).fetchone()
        latest = conn.execute('SELECT max(attempt_number) FROM image_ocr_jobs WHERE version_id=?', (job['version_id'],)).fetchone()[0]
        stale = source is None or source['object_sha256']!=job['source_sha256'] or source['archived_at'] is not None or source['lifecycle_status']=='superseded' or latest!=job['attempt_number']
        if stale:
            status='stale'
        elif error_code:
            status='failed'
        else:
            status='succeeded' if result and result['text'].strip() else 'no_text'
        conn.execute('''UPDATE image_ocr_jobs SET status=?,error_code=?,text=?,blocks_json=?,engine=?,model_version=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?''',
            (status,error_code,result['text'] if result else None,json.dumps(result['blocks'],ensure_ascii=False) if result else None,
             result['engine'] if result else None,result['model_version'] if result else None,now,job['id']))
        # Only successful candidates atomically replace the derived head+index. Failed retries keep prior good text.
        if status in {'succeeded','no_text'}:
            conn.execute('''INSERT INTO image_ocr_heads(version_id,job_id,source_sha256,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(version_id) DO UPDATE SET job_id=excluded.job_id,source_sha256=excluded.source_sha256,updated_at=excluded.updated_at''',
                (job['version_id'],job['id'],job['source_sha256'],now))
            index_attachment_version(conn,job['version_id'])
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def run_one_image_ocr(conn: sqlite3.Connection, *, storage: ContentStorage | None = None, client=None) -> bool:
    job = lease_image_ocr(conn)
    if job is None:
        return False
    storage = storage or ContentStorage(config.CONTENT_ROOT)
    client = client or local_ocr_client
    try:
        row = conn.execute('''SELECT v.doc_type,v.original_filename,o.storage_rel_path,o.size_bytes FROM content_versions v
            JOIN content_objects o ON o.sha256=v.object_sha256 WHERE v.id=? AND v.object_sha256=?''', (job['version_id'],job['source_sha256'])).fetchone()
        if row is None:
            raise ValueError('image_ocr_source_changed')
        path = storage.resolve_object(row['storage_rel_path'])
        if row['size_bytes']>int(getattr(config,'IMAGE_OCR_MAX_BYTES',32*1024*1024)) or path.stat().st_size>int(getattr(config,'IMAGE_OCR_MAX_BYTES',32*1024*1024)):
            raise ValueError('image_ocr_too_large')
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b''):
                digest.update(chunk)
        if digest.hexdigest()!=job['source_sha256']:
            raise ValueError('image_ocr_source_changed')
        result = client(path,kind=row['doc_type'],filename=row['original_filename'])
        finish_image_ocr(conn,job,result=result)
    except Exception as exc:
        # Persist stable codes only, never service bodies, paths, token, or OCR content in logs.
        code = str(exc) if isinstance(exc,ValueError) and str(exc).startswith('image_ocr_') else 'image_ocr_failed'
        finish_image_ocr(conn,job,error_code=code)
    return True


def start_image_ocr_worker() -> tuple[threading.Event,threading.Thread]:
    stop = threading.Event()
    def work():
        from .content_search import reconcile_attachment_index
        while not stop.is_set():
            conn = connect()
            try:
                reconciled = reconcile_attachment_index(conn,limit=50)
                conn.commit()
                worked = run_one_image_ocr(conn)
            except Exception:
                conn.rollback()
                logger.exception('attachment_worker_iteration_failed')
                reconciled,worked = 0,False
            finally:
                conn.close()
            stop.wait(0.1 if worked or reconciled else 2)
    thread = threading.Thread(target=work,name='image-ocr-worker',daemon=True)
    thread.start()
    return stop,thread
