"""Published transcripts are independent of original video availability."""
import pytest
from api.db import connect
from api.transcription_artifacts import LocalTranscriptionArtifactStore
from tests.test_content_library_api import content_api, _auth, _insert_published_media


def test_transcript_requires_actual_published_head(content_api):
    client, sessions, _, db_path = content_api
    conn = connect(db_path)
    media = '123e4567-e89b-12d3-a456-426614174811'
    item = _insert_published_media(conn, media_id=media,
        version_id='123e4567-e89b-12d3-a456-426614174812',
        title='Synthetic', filename='synthetic.mp4', now=400)
    conn.execute('DELETE FROM media_transcript_heads WHERE media_id=?', (media,))
    conn.commit()
    conn.close()
    response = client.get(f'/api/admin/content/items/{item}/media-download',
                          params={'part': 'transcript'}, **_auth(sessions, 'publisher'))
    assert response.status_code == 404
    assert response.json()['detail'] == '视频转录稿尚未正式发布'


@pytest.mark.parametrize('video_state', ['missing', 'invalid_hash', 'invalid_size', 'outside_root'])
def test_transcript_download_does_not_require_video(content_api, tmp_path, video_state):
    client, sessions, _, db_path = content_api
    conn = connect(db_path)
    media = '123e4567-e89b-12d3-a456-426614174801'
    version = '123e4567-e89b-12d3-a456-426614174802'
    item = _insert_published_media(conn, media_id=media, version_id=version,
                                  title='Synthetic', filename='synthetic.mp4', now=400)
    markdown = b'# Synthetic transcript\n'
    ref = LocalTranscriptionArtifactStore((tmp_path / 'transcription-artifacts').resolve()).write_markdown(markdown)
    conn.execute('UPDATE transcript_versions SET markdown_rel_path=?,markdown_sha256=?,markdown_size_bytes=? WHERE id=?',
                 (ref.relative_path, ref.content_sha256, ref.size_bytes, version))
    if video_state != 'missing':
        path = tmp_path / 'media' / 'synthetic' / f'{media}.mp4'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic-video')
        conn.execute('UPDATE media_assets SET file_size=?,sha256=? WHERE media_id=?',
                     (len(b'synthetic-video'), '0' * 64, media))
        if video_state == 'invalid_size':
            conn.execute('UPDATE media_assets SET file_size=1 WHERE media_id=?', (media,))
        if video_state == 'outside_root':
            conn.execute("UPDATE media_assets SET storage_rel_path='../outside.mp4' WHERE media_id=?", (media,))
    conn.commit()
    conn.close()
    endpoint = f'/api/admin/content/items/{item}/media-download'
    auth = _auth(sessions, 'publisher')
    response = client.get(endpoint, params={'part': 'transcript'}, **auth)
    assert response.status_code == 200, response.text
    assert response.content == markdown
    assert client.get(endpoint, params={'part': 'video'}, **auth).status_code in (404, 409)
    assert client.get(endpoint, params={'part': 'all'}, **auth).status_code in (404, 409)
