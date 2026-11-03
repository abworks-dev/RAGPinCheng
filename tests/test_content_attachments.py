"""Synthetic-only attachment lifecycle, permissions, publication and storage tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_content_library_api import content_api, _auth
from api import content_attachments, content_publication, routes_content, content_trash_cleanup
from api.content_attachment_types import ATTACHMENT_TYPES
from api.content_import import import_server_batch
from api.content_store import create_publication_job, list_content_items
from api.db import connect

BASE = '/api/admin/content'
PNG = bytes.fromhex('89504e470d0a1a0a') + b'synthetic-png'
PDF = b'%PDF-1.4\nsynthetic-preview'


@pytest.fixture
def attachments(content_api, monkeypatch):
    client, sessions, queued, db_path = content_api
    storage = routes_content._storage
    monkeypatch.setattr(content_attachments, '_storage', storage)
    monkeypatch.setattr(content_publication, '_storage', storage)
    monkeypatch.setattr(content_publication, 'connect', lambda: connect(db_path))
    # A view/download-only user: cannot read unpublished attachment candidates.
    conn = connect(db_path)
    user_id = conn.execute("SELECT id FROM users WHERE employee_id='plain'").fetchone()[0]
    conn.executemany('INSERT INTO content_permissions(user_id,permission,created_at) VALUES (?,?,1)',
                     [(user_id, p) for p in ('workspace.view', 'category.view', 'item.view', 'item.download')])
    conn.commit()
    conn.close()
    yield content_api


def upload(env, filename='model.dwg', data=b'synthetic-model'):
    client, sessions, _, _ = env
    response = client.post(BASE + '/uploads', data={'category_id': 'cat-04'},
                           files=[('files', (filename, data, 'application/octet-stream'))],
                           **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    entry = response.json()['entries'][0]
    assert entry['status'] == 'accepted', entry
    return entry


def publish(env, version_id):
    client, sessions, queued, db_path = env
    response = client.post(BASE + f'/versions/{version_id}/publish', **_auth(sessions, 'publisher', csrf=True))
    assert response.status_code == 200, response.text
    content_publication.run_content_publication(queued[-1])
    conn = connect(db_path)
    assert conn.execute('SELECT lifecycle_status FROM content_versions WHERE id=?', (version_id,)).fetchone()[0] == 'published'
    conn.close()


@pytest.mark.parametrize('kind', sorted(ATTACHMENT_TYPES))
def test_upload_publish_all_formats_never_calls_index(attachments, monkeypatch, kind):
    client, sessions, _, db_path = attachments
    def forbidden(*args, **kwargs):
        pytest.fail('Non-RAG attachment called the indexing pipeline')
    monkeypatch.setattr(content_publication, 'index_managed_content', forbidden)
    monkeypatch.setattr(content_publication._storage, 'materialize_published_source', forbidden)
    entry = upload(attachments, f'asset.{kind}')
    version = entry['version_id']
    assert client.get(BASE + f'/versions/{version}/info', **_auth(sessions, 'plain')).status_code == 404
    publish(attachments, version)
    info = client.get(BASE + f'/versions/{version}/info', **_auth(sessions, 'plain'))
    assert info.status_code == 200, info.text
    assert info.json()['rag_enabled'] is False
    assert info.json()['doc_type'] == kind
    assert info.json()['file_size'] == len(b'synthetic-model')
    assert info.json()['is_current'] is True
    download = client.get(BASE + f'/versions/{version}/file?download=true', **_auth(sessions, 'plain'))
    assert download.status_code == 200 and download.content == b'synthetic-model'
    assert download.headers['x-content-type-options'] == 'nosniff'
    conn = connect(db_path)
    assert conn.execute("SELECT count(*) FROM content_audit_events WHERE event_type='content.downloaded' AND version_id=?", (version,)).fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize('kind,mime,data', [('png', 'image/png', PNG), ('jpg', 'image/jpeg', bytes.fromhex('ffd8ff') + b'synthetic'), ('pdf', 'application/pdf', PDF)])
def test_associated_preview_permissions_and_version_bytes(attachments, kind, mime, data):
    client, sessions, _, db_path = attachments
    entry = upload(attachments)
    version = entry['version_id']
    url = BASE + f'/versions/{version}/associated-preview'
    files = {'file': (f'view.{kind}', data, mime)}
    assert client.post(url, files=files).status_code == 401
    assert client.post(url, files=files, **_auth(sessions, 'publisher')).status_code == 403
    assert client.post(url, files=files, **_auth(sessions, 'organizer', csrf=True)).status_code == 403
    attached = client.post(url, files=files, **_auth(sessions, 'publisher', csrf=True))
    assert attached.status_code == 200, attached.text
    assert attached.json()['preview_kind'] == ('pdf' if kind == 'pdf' else 'image')
    assert client.get(BASE + f'/versions/{version}/preview').status_code == 401
    assert client.get(BASE + f'/versions/{version}/preview', **_auth(sessions, 'plain')).status_code == 404
    draft_preview = client.get(BASE + f'/versions/{version}/preview', **_auth(sessions, 'publisher'))
    assert draft_preview.content == data
    assert draft_preview.headers['content-type'].startswith(mime)
    publish(attachments, version)
    info = client.get(BASE + f'/versions/{version}/info', **_auth(sessions, 'plain')).json()
    assert info['is_associated'] is True
    assert client.get(info['preview_url'], **_auth(sessions, 'plain')).content == data
    conn = connect(db_path)
    assert conn.execute('SELECT count(*) FROM content_version_preview_history WHERE version_id=?', (version,)).fetchone()[0] == 1
    conn.close()


def test_reject_invalid_sidecar_before_storage_and_limit(attachments, monkeypatch):
    client, sessions, _, db_path = attachments
    version = upload(attachments)['version_id']
    url = BASE + f'/versions/{version}/associated-preview'
    bad = b'<html>not-an-image</html>'
    rejected = client.post(url, files={'file': ('bad.png', bad, 'image/png')}, **_auth(sessions, 'publisher', csrf=True))
    assert rejected.status_code == 422
    assert not content_attachments._storage.object_path_for_sha256(hashlib.sha256(bad).hexdigest()).exists()
    monkeypatch.setattr(content_attachments, 'MAX_PREVIEW_BYTES', 8)
    limited = client.post(url, files={'file': ('large.png', PNG, 'image/png')}, **_auth(sessions, 'publisher', csrf=True))
    assert limited.status_code == 422
    conn = connect(db_path)
    assert conn.execute('SELECT count(*) FROM content_version_previews').fetchone()[0] == 0
    conn.close()


def test_view_permission_does_not_grant_explicit_download(attachments):
    client, sessions, _, db_path = attachments
    version = upload(attachments, 'image.png', PNG)['version_id']
    publish(attachments, version)
    conn = connect(db_path)
    conn.execute("DELETE FROM content_permissions WHERE user_id=(SELECT id FROM users WHERE employee_id='plain') AND permission='item.download'")
    conn.commit()
    conn.close()
    assert client.get(BASE + f'/versions/{version}/preview', **_auth(sessions, 'plain')).status_code == 200
    assert client.get(BASE + f'/versions/{version}/file?download=false', **_auth(sessions, 'plain')).status_code == 200
    assert client.get(BASE + f'/versions/{version}/file?download=true', **_auth(sessions, 'plain')).status_code == 403


def test_draft_update_preserves_published_list_and_history_archive_guards(attachments):
    client, sessions, _, db_path = attachments
    entry = upload(attachments)
    version = entry['version_id']
    publish(attachments, version)
    response = client.post(BASE + f"/items/{entry['item_id']}/versions",
                           data={'expected_version_id': version, 'filename_mode': 'new'},
                           files={'file': ('updated.dwg', b'updated-model', 'application/octet-stream')},
                           **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    new_version = response.json()['version_id']
    plain_rows = client.get(BASE + '/items-page', **_auth(sessions, 'plain')).json()
    assert plain_rows['total'] == 1
    assert plain_rows['items'][0]['version_id'] == version
    assert client.get(BASE + f'/versions/{new_version}/info', **_auth(sessions, 'plain')).status_code == 404
    blocked_bulk = client.post(BASE + '/bulk-download', json={'version_ids': [new_version]}, **_auth(sessions, 'plain', csrf=True))
    assert blocked_bulk.status_code == 404
    publish(attachments, new_version)
    assert client.get(BASE + f'/versions/{version}/file?download=true', **_auth(sessions, 'plain')).status_code == 200
    conn = connect(db_path)
    conn.execute("UPDATE content_items SET archived_at=1 WHERE id=?", (entry['item_id'],))
    conn.commit()
    conn.close()
    for historical in (version, new_version):
        for endpoint in ('info', 'preview', 'file?download=true'):
            assert client.get(BASE + f'/versions/{historical}/{endpoint}', **_auth(sessions, 'admin')).status_code == 404


def test_server_import_registers_all_attachment_formats(attachments, tmp_path):
    _, _, _, db_path = attachments
    storage = routes_content._storage
    batch = storage.inbox_root / 'server' / 'synthetic-batch'
    batch.mkdir(parents=True)
    for kind in ATTACHMENT_TYPES:
        (batch / f'asset.{kind}').write_bytes(b'synthetic-import-' + kind.encode())
    conn = connect(db_path)
    actor = conn.execute("SELECT id FROM users WHERE employee_id='admin'").fetchone()[0]
    _, entries = import_server_batch(conn, storage, batch, actor_user_id=actor, max_bytes=1024, apply=True)
    assert len(entries) == len(ATTACHMENT_TYPES)
    assert all(entry.status == 'imported' for entry in entries), entries
    conn.close()


def test_replaced_sidecar_history_purge_retains_shared_hash(attachments):
    client, sessions, _, db_path = attachments
    first, second = upload(attachments), upload(attachments, 'second.dwg')
    for entry, payload in ((first, PNG), (first, PDF), (second, PNG)):
        suffix = 'png' if payload == PNG else 'pdf'
        response = client.post(BASE + f"/versions/{entry['version_id']}/associated-preview",
                               files={'file': (f'view.{suffix}', payload, 'application/octet-stream')},
                               **_auth(sessions, 'publisher', csrf=True))
        assert response.status_code == 200, response.text
    conn = connect(db_path)
    assert conn.execute('SELECT count(*) FROM content_version_preview_history WHERE version_id=?', (first['version_id'],)).fetchone()[0] == 2
    conn.commit()
    deleted, _ = content_trash_cleanup._delete_app_records(conn, first['item_id'])
    png_hash = hashlib.sha256(PNG).hexdigest()
    pdf_hash = hashlib.sha256(PDF).hexdigest()
    assert conn.execute('SELECT 1 FROM content_objects WHERE sha256=?', (png_hash,)).fetchone()
    assert conn.execute('SELECT 1 FROM content_objects WHERE sha256=?', (pdf_hash,)).fetchone() is None
    assert any(pdf_hash in path for path in deleted)
    assert not any(png_hash in path for path in deleted)
    conn.close()


def test_opaque_original_requires_download_even_without_download_query(attachments):
    client, sessions, _, db_path = attachments
    version = upload(attachments, 'model.rvt')['version_id']
    publish(attachments, version)
    associated = client.post(BASE + f'/versions/{version}/associated-preview',
                             files={'file': ('view.pdf', PDF, 'application/pdf')},
                             **_auth(sessions, 'publisher', csrf=True))
    assert associated.status_code == 200
    conn = connect(db_path)
    conn.execute("DELETE FROM content_permissions WHERE user_id=(SELECT id FROM users WHERE employee_id='plain') AND permission='item.download'")
    conn.commit()
    conn.close()
    assert client.get(BASE + f'/versions/{version}/info', **_auth(sessions, 'plain')).status_code == 200
    assert client.get(BASE + f'/versions/{version}/preview', **_auth(sessions, 'plain')).content == PDF
    for suffix in ('file', 'file?download=false', 'file?download=true'):
        assert client.get(BASE + f'/versions/{version}/{suffix}', **_auth(sessions, 'plain')).status_code == 403


def test_category_and_recursive_download_exclude_unpublished_attachments(attachments):
    from api.content_bulk_operations import create_preflight
    client, sessions, _, db_path = attachments
    draft = upload(attachments, 'draft.ifc')
    current = upload(attachments, 'current.ifc')
    publish(attachments, current['version_id'])
    conn = connect(db_path)
    from api.auth import CurrentUser
    # Use authenticated route context for category ZIP; recursive preflight uses permission snapshot.
    actor = conn.execute("SELECT id FROM users WHERE employee_id='plain'").fetchone()[0]
    category = conn.execute("SELECT version FROM category_nodes WHERE id='cat-04'").fetchone()[0]
    result = create_preflight(conn, operation='download', category_refs=[{'category_id': 'cat-04', 'expected_version': category}],
                              item_refs=[], actor_user_id=actor, permissions={'item.view', 'item.download'})
    rows = conn.execute('SELECT version_id FROM content_bulk_operation_items WHERE run_id=?', (result['id'],)).fetchall()
    assert [r[0] for r in rows] == [current['version_id']]
    conn.close()
    response = client.post(BASE + '/categories/cat-04/download', **_auth(sessions, 'plain', csrf=True))
    assert response.status_code == 200, response.text
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 1
        assert archive.namelist()[0].endswith('current.ifc')


@pytest.mark.parametrize('initial,updated', [('dwg', 'md'), ('md', 'ifc')])
def test_cross_type_revision_changes_rag_contract_only_at_publication(attachments, monkeypatch, initial, updated):
    client, sessions, _, db_path = attachments
    indexed = []
    monkeypatch.setattr(content_publication, 'index_managed_content', lambda *args: indexed.append(args[1]))
    entry = upload(attachments, f'initial.{initial}')
    publish(attachments, entry['version_id'])
    response = client.post(BASE + f"/items/{entry['item_id']}/versions",
                           data={'expected_version_id': entry['version_id'], 'filename_mode': 'new'},
                           files={'file': (f'updated.{updated}', b'synthetic-updated', 'application/octet-stream')},
                           **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    candidate = response.json()['version_id']
    assert client.get(BASE + f'/versions/{candidate}/info', **_auth(sessions, 'plain')).status_code == 404
    current = client.get(BASE + '/items-page', **_auth(sessions, 'plain')).json()['items'][0]
    assert current['version_id'] == entry['version_id']
    publish(attachments, candidate)
    assert indexed == ['markdown']
    current = client.get(BASE + '/items-page', **_auth(sessions, 'plain')).json()['items'][0]
    assert current['version_id'] == candidate
    assert current['rag_enabled'] is (updated == 'md')
    conn = connect(db_path)
    assert conn.execute('SELECT current_version_id FROM content_item_heads WHERE item_id=?', (entry['item_id'],)).fetchone()[0] == candidate
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()


def test_attachment_to_xmind_draft_preview_requires_editor(attachments, monkeypatch):
    import io
    import json
    import zipfile

    client, sessions, _, _ = attachments
    entry = upload(attachments, 'current.dwg')
    publish(attachments, entry['version_id'])
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('content.json', json.dumps([{
            'id': 'sheet', 'title': 'Synthetic plan',
            'rootTopic': {'id': 'root', 'title': 'Unpublished candidate'},
        }]))
    response = client.post(BASE + f"/items/{entry['item_id']}/versions",
                           data={'expected_version_id': entry['version_id'], 'filename_mode': 'new'},
                           files={'file': ('candidate.xmind', payload.getvalue(), 'application/x-xmind')},
                           **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    candidate = response.json()['version_id']
    url = BASE + f'/versions/{candidate}/xmind-preview'
    assert client.get(url, **_auth(sessions, 'plain')).status_code == 404
    editor = client.get(url, **_auth(sessions, 'organizer'))
    assert editor.status_code == 200, editor.text
    assert editor.json()['sheets'][0]['root_topic']['title'] == 'Unpublished candidate'
    monkeypatch.setattr(content_publication, 'index_managed_content', lambda *args: None)
    publish(attachments, candidate)
    assert client.get(url, **_auth(sessions, 'plain')).status_code == 200


def test_upgrade_from_44_preserves_old_rows_and_allows_attachments(tmp_path, monkeypatch):
    from api import db_migrations
    from api.db import init_db
    path = tmp_path / 'upgrade.sqlite'
    migrations = db_migrations.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db_migrations, 'MIGRATIONS', migrations[:-1])
        init_db(path, backup_dir=tmp_path / 'backups')
    conn = connect(path)
    conn.execute("INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES ('legacy','Legacy','unused','user',1,1)")
    conn.commit()
    category_rows = [tuple(row) for row in conn.execute('SELECT * FROM category_nodes ORDER BY id')]
    assert conn.execute('SELECT max(version) FROM app_schema_migrations').fetchone()[0] == 44
    conn.close()
    init_db(path, backup_dir=tmp_path / 'backups')
    init_db(path, backup_dir=tmp_path / 'backups')
    conn = connect(path)
    assert conn.execute('SELECT max(version) FROM app_schema_migrations').fetchone()[0] == 45
    assert conn.execute("SELECT real_name FROM users WHERE employee_id='legacy'").fetchone()[0] == 'Legacy'
    assert [tuple(row) for row in conn.execute('SELECT * FROM category_nodes ORDER BY id')] == category_rows
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='content_versions'").fetchone()[0]
    for kind in ATTACHMENT_TYPES | {'pdf', 'markdown', 'docx', 'xlsx', 'pptx', 'xmind'}:
        assert f"'{kind}'" in schema
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    conn.close()
