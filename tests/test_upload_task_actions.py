"""Synthetic task actions: dismissal is not content deletion."""
import pytest

from api.db import connect, init_db
from api import db_migrations
from api.content_store import create_web_batch, record_upload_batch_entry
from tests.test_content_library_api import content_api, _auth, _insert_catalogued_media


def _upload(client, sessions):
    response = client.post('/api/admin/content/uploads', data={'category_id': 'cat-03'},
        files=[('files', ('synthetic.md', b'# Synthetic', 'text/markdown'))],
        **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    return response.json()


def test_dismiss_preserves_content_entries_and_audit_and_is_idempotent(content_api):
    client, sessions, _, db_path = content_api
    uploaded = _upload(client, sessions)
    batch = uploaded['batch_id']
    endpoint = f'/api/admin/content/upload-tasks/{batch}/dismiss'
    for _ in range(2):
        response = client.post(endpoint, **_auth(sessions, 'organizer', csrf=True))
        assert response.status_code == 200, response.text
    conn = connect(db_path)
    assert conn.execute('SELECT dismissed_at FROM upload_batches WHERE id=?', (batch,)).fetchone()[0]
    assert conn.execute('SELECT count(*) FROM upload_batch_entries WHERE batch_id=?', (batch,)).fetchone()[0] == 1
    item = uploaded['entries'][0]['item_id']
    assert conn.execute('SELECT archived_at FROM content_items WHERE id=?', (item,)).fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM content_audit_events WHERE batch_id=? AND event_type='upload_task.dismissed'", (batch,)).fetchone()[0] == 1
    conn.close()
    listing = client.get('/api/admin/content/upload-tasks', **_auth(sessions, 'organizer'))
    assert listing.status_code == 200
    assert batch not in str(listing.json())


def test_task_actions_enforce_auth_csrf_ownership_and_processing(content_api):
    client, sessions, _, db_path = content_api
    uploaded = _upload(client, sessions)
    batch = uploaded['batch_id']
    for action in ('dismiss', 'archive'):
        endpoint = f'/api/admin/content/upload-tasks/{batch}/{action}'
        assert client.post(endpoint).status_code == 401
        assert client.post(endpoint, **_auth(sessions, 'organizer')).status_code == 403
        assert client.post(endpoint, **_auth(sessions, 'plain', csrf=True)).status_code == 403
    conn = connect(db_path)
    owner = conn.execute("SELECT id FROM users WHERE employee_id='organizer'").fetchone()[0]
    active = create_web_batch(conn, actor_user_id=owner, total_files=2)
    conn.close()
    for action in ('dismiss', 'archive'):
        response = client.post(f'/api/admin/content/upload-tasks/{active}/{action}',
                               **_auth(sessions, 'organizer', csrf=True))
        assert response.status_code == 409, response.text


def test_archive_uses_original_snapshot_and_does_not_dismiss(content_api):
    client, sessions, _, db_path = content_api
    uploaded = _upload(client, sessions)
    batch = uploaded['batch_id']
    response = client.post(f'/api/admin/content/upload-tasks/{batch}/archive',
                           **_auth(sessions, 'organizer', csrf=True))
    assert response.status_code == 200, response.text
    assert response.json()['succeeded'] == 1
    conn = connect(db_path)
    assert conn.execute('SELECT archived_at FROM content_items WHERE id=?',
                        (uploaded['entries'][0]['item_id'],)).fetchone()[0]
    assert conn.execute('SELECT dismissed_at FROM upload_batches WHERE id=?', (batch,)).fetchone()[0] is None
    conn.close()


def test_archive_refuses_document_updated_since_upload(content_api):
    client, sessions, _, db_path = content_api
    uploaded = _upload(client, sessions)
    entry = uploaded['entries'][0]
    auth = _auth(sessions, 'organizer', csrf=True)
    updated = client.post(f"/api/admin/content/items/{entry['item_id']}/versions",
        data={'expected_version_id': entry['version_id'], 'filename_mode': 'new'},
        files={'file': ('new.md', b'# New', 'text/markdown')}, **auth)
    assert updated.status_code == 200, updated.text
    result = client.post(f"/api/admin/content/upload-tasks/{uploaded['batch_id']}/archive", **auth)
    assert result.status_code == 200, result.text
    assert result.json()['failed'] == 1
    conn = connect(db_path)
    assert conn.execute('SELECT archived_at FROM content_items WHERE id=?', (entry['item_id'],)).fetchone()[0] is None
    conn.close()


@pytest.mark.parametrize('action', ['dismiss', 'archive'])
def test_other_uploader_cannot_operate_but_admin_can(content_api, action):
    client, sessions, _, db_path = content_api
    uploaded = _upload(client, sessions)
    conn = connect(db_path)
    other = conn.execute("SELECT id FROM users WHERE employee_id='reviewer'").fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO content_permissions(user_id,permission,created_at) VALUES (?,'item.upload',1)", (other,))
    conn.commit()
    conn.close()
    endpoint = f"/api/admin/content/upload-tasks/{uploaded['batch_id']}/{action}"
    assert client.post(endpoint, **_auth(sessions, 'reviewer', csrf=True)).status_code == 404
    response = client.post(endpoint, **_auth(sessions, 'admin', csrf=True))
    assert response.status_code == 200, response.text


def test_dismiss_migration_preserves_old_batch_and_reopens_idempotently(tmp_path, monkeypatch):
    migrations = db_migrations.MIGRATIONS
    monkeypatch.setattr(db_migrations, 'MIGRATIONS', tuple(m for m in migrations if m.version <= 43))
    db_path = tmp_path / 'synthetic.sqlite'
    init_db(db_path, backup_dir=tmp_path / 'backups')
    conn = connect(db_path)
    conn.execute("INSERT INTO upload_batches(id,origin,status,storage_rel_path,created_at,updated_at,total_files,accepted_files) VALUES ('legacy','web','ready_for_review','synthetic',1,1,1,1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_migrations, 'MIGRATIONS', migrations)
    for _ in range(2):
        init_db(db_path, backup_dir=tmp_path / 'backups')
    conn = connect(db_path)
    row = conn.execute("SELECT status,accepted_files,dismissed_at,dismissed_by FROM upload_batches WHERE id='legacy'").fetchone()
    assert tuple(row) == ('ready_for_review', 1, None, None)
    conn.close()


def test_archive_unpublished_video_snapshot(content_api):
    client, sessions, _, db_path = content_api
    conn = connect(db_path)
    owner = conn.execute("SELECT id FROM users WHERE employee_id='admin'").fetchone()[0]
    batch = create_web_batch(conn, actor_user_id=owner, total_files=1)
    media_id = '123e4567-e89b-12d3-a456-426614174701'
    item, _ = _insert_catalogued_media(conn, media_id=media_id, title='Synthetic',
                                      filename='synthetic.mp4', now=400)
    record_upload_batch_entry(conn, batch_id=batch, sequence=1, filename='synthetic.mp4',
                             relative_path=None, size_bytes=1024, status='accepted',
                             entry_kind='video', media_id=media_id)
    conn.close()
    response = client.post(f'/api/admin/content/upload-tasks/{batch}/archive',
                           **_auth(sessions, 'admin', csrf=True))
    assert response.status_code == 200, response.text
    assert response.json()['succeeded'] == 1
    conn = connect(db_path)
    assert conn.execute('SELECT archived_at FROM content_items WHERE id=?', (item,)).fetchone()[0]
    conn.close()
