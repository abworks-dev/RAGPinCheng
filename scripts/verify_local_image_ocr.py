"""Isolated synthetic HTTP OCR -> durable queue -> search/preview runtime probe.

Run from /app with ``python -m scripts.verify_local_image_ocr``. Never opens the
configured application DB or starts the runtime app/workers. Only the approved
synthetic fixture is sent to the configured, validated private OCR endpoint.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx
from fastapi import FastAPI, UploadFile
from starlette.testclient import TestClient

from api import content_attachments, image_ocr_jobs, routes_attachment_search as routes
from api.auth import CurrentUser, require_user
from api.content_storage import ContentStorage
from api.content_store import register_uploaded_document
from api.content_search import search_attachment_files
from api.db import connect, get_db, init_db
from src import config

QUERY = '消防通道'


def _fixture_bytes() -> bytes:
    # Docker copies this approved fixture next to the script; source checkouts
    # use the identical checked-in image. No generated fonts/Pillow dependency.
    sibling = Path(__file__).with_name('image-ocr-synthetic.png')
    source = Path(__file__).resolve().parents[1] / 'services/image_ocr/synthetic-chinese.png'
    path = sibling if sibling.is_file() else source
    return path.read_bytes()


def _verify_pipeline(root: Path, png: bytes) -> dict:
    db_path = root / 'probe.sqlite'
    init_db(db_path)
    storage = ContentStorage(root / 'content')
    conn = connect(db_path)
    try:
        conn.execute("INSERT INTO users(id,employee_id,real_name,password_hash,role,created_at) VALUES (1,'synthetic-probe','Synthetic','not-a-password','user',1)")
        conn.execute("INSERT INTO upload_batches(id,origin,status,created_by,created_at,updated_at) VALUES ('probe','web','staging',1,1,1)")
        conn.commit()
        stored = asyncio.run(storage.ingest_upload(UploadFile(filename='sample.png', file=io.BytesIO(png)), batch_id='probe', max_bytes=len(png)))
        # A pre-enable synthetic image must remain untouched: enabling and
        # processing a new upload must not implicitly scan historical versions.
        with patch.object(config, 'IMAGE_OCR_ENABLED', False):
            historical = register_uploaded_document(conn, batch_id='probe', category_id='cat-04', title='Historical synthetic', original_filename='historical.png', doc_type='png', stored=stored, actor_user_id=1)
        entry = register_uploaded_document(conn, batch_id='probe', category_id='cat-04', title='Synthetic sample', original_filename='sample.png', doc_type='png', stored=stored, actor_user_id=1)
        version = entry.version_id
        assert conn.execute('SELECT count(*) FROM image_ocr_jobs').fetchone()[0] == 1, 'probe_queue_missing'
        assert not search_attachment_files(conn, QUERY, categories=None), 'probe_draft_search_visible'
        # Exercise actual default local HTTP client, not injected OCR text.
        assert image_ocr_jobs.run_one_image_ocr(conn, storage=storage), 'probe_queue_not_run'
        conn.close()
        conn = connect(db_path)  # Persistence must survive a fresh connection.
        job = conn.execute('SELECT * FROM image_ocr_jobs').fetchone()
        assert job['status'] == 'succeeded', 'probe_ocr_not_succeeded'
        assert QUERY in job['text'], 'probe_expected_chinese_missing'
        head = conn.execute('SELECT job_id FROM image_ocr_heads WHERE version_id=?', (version,)).fetchone()
        assert head and head['job_id'] == job['id'], 'probe_ocr_head_missing'
        assert not image_ocr_jobs.run_one_image_ocr(conn, storage=storage), 'probe_unexpected_extra_job'
        assert conn.execute('SELECT count(*) FROM image_ocr_jobs WHERE version_id=?', (historical.version_id,)).fetchone()[0] == 0, 'probe_historical_backfill'

        # Tiny in-memory HTTP surface: no api.main/lifespan/auth-server import.
        # Inject only a chat identity (zero content permissions), never bypass
        # visibility helpers or the preview route's real FileResponse bytes.
        app = FastAPI()
        app.include_router(routes.router, prefix='/api/attachments')
        app.dependency_overrides[routes._feature] = lambda: None
        app.dependency_overrides[require_user] = lambda: CurrentUser(1, 'synthetic-probe', 'Synthetic', 'user', '')
        app.dependency_overrides[get_db] = lambda: conn
        url = '/api/attachments/versions/' + version + '/preview'
        with patch.object(content_attachments, '_storage', storage), TestClient(app) as client:
            assert client.get(url).status_code == 404, 'probe_draft_preview_visible'
            # Synthetic publication metadata only; do not invoke indexing or a
            # production publication worker/materialization path.
            conn.execute("UPDATE content_versions SET lifecycle_status='published' WHERE id=?", (version,))
            conn.execute("INSERT INTO content_publications(id,version_id,status,publisher_id,created_at,updated_at,published_at) VALUES ('probe-publication',?,'published',1,1,1,1)", (version,))
            conn.execute("INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,'probe-publication',1)", (entry.item_id, version))
            conn.commit()
            cards = search_attachment_files(conn, QUERY, categories=None)
            assert [card['version_id'] for card in cards] == [version], 'probe_ocr_search_missing'
            assert cards[0]['matched_fields'] == ['ocr'], 'probe_not_ocr_only_match'
            response = client.get(url)
            assert response.status_code == 200 and response.content == png, 'probe_preview_bytes_mismatch'
            assert response.headers['content-type'] == 'image/png', 'probe_preview_mime_mismatch'
            assert response.headers['x-content-type-options'] == 'nosniff'
            conn.execute('DELETE FROM content_item_heads WHERE item_id=?', (entry.item_id,))
            conn.commit()
            assert not search_attachment_files(conn, QUERY, categories=None), 'probe_noncurrent_search_visible'
            assert client.get(url).status_code == 404, 'probe_noncurrent_preview_visible'
            conn.execute("INSERT INTO content_item_heads(item_id,current_version_id,publication_id,updated_at) VALUES (?,?,'probe-publication',1)", (entry.item_id, version))
            conn.execute('UPDATE content_items SET archived_at=1 WHERE id=?', (entry.item_id,))
            conn.commit()
            assert client.get(url).status_code == 404, 'probe_archived_preview_visible'
            assert not search_attachment_files(conn, QUERY, categories=None), 'probe_archived_search_visible'
        return {'synthetic_client_verified': True, 'persistent_queue_verified': True,
                'ocr_head_verified': True, 'ocr_only_search_verified': True,
                'chat_only_preview_bytes_verified': True, 'draft_preview_status': 404,
                'archived_preview_status': 404}
    finally:
        conn.close()


def verify() -> dict:
    report = {'enabled': bool(config.IMAGE_OCR_ENABLED),
              'token_configured': bool(config.IMAGE_OCR_TOKEN), 'historical_backfill': False}
    if report['enabled']:
        # Validate the endpoint BEFORE even the unauthenticated health request.
        url, _ = image_ocr_jobs._local_endpoint()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            response = client.get(url + '/health')
            assert response.status_code == 200, 'probe_health_failed'
            assert response.json() == {'status': 'ok', 'api_version': 'image-ocr/1'}, 'probe_health_invalid'
            assert client.post(url + '/v1/ocr').status_code == 401, 'probe_unauthorized_ocr_accepted'
        with tempfile.TemporaryDirectory(prefix='synthetic-ocr-probe-') as directory:
            report.update(_verify_pipeline(Path(directory), _fixture_bytes()))
    print('IMAGE_OCR_RUNTIME ' + json.dumps(report, sort_keys=True))
    return report


if __name__ == '__main__':
    try:
        verify()
    except Exception:
        # Do not expose HTTP request URLs, configuration, response text, paths,
        # tokens or OCR text in the operator-facing failure report/traceback.
        print('IMAGE_OCR_RUNTIME {"verified": false, "error_code": "synthetic_probe_failed"}')
        raise SystemExit(1) from None
