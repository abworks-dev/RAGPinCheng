"""Real metadata-search ports + ChatSession + HTTP persistence, synthetic data only."""
import json

import pytest

from api import conversation_runtime as runtime, content_publication, content_attachments, routes_content
from api.auth import CurrentUser
from api.db import connect
from src.session import ChatSession
from tests.test_content_library_api import content_api, _auth
from tests.test_content_attachments import upload, publish


@pytest.fixture
def published_chat(content_api, monkeypatch):
    client, sessions, queued, path = content_api
    monkeypatch.setattr(content_publication, '_storage', routes_content._storage)
    monkeypatch.setattr(content_attachments, '_storage', routes_content._storage)
    monkeypatch.setattr(content_publication, 'connect', lambda: connect(path))
    monkeypatch.setattr(runtime, 'connect', lambda: connect(path))
    monkeypatch.setattr(ChatSession, '_fresh_retrieve', lambda *a, **k: pytest.fail('file-only queried RAG'))
    monkeypatch.setattr('src.session.rewrite_query', lambda *a, **k: pytest.fail('file-only called model'))
    entries = [upload(content_api, f'图片{number}.dwg') for number in (11, 12)]
    for entry in entries:
        publish(content_api, entry['version_id'])
    conn = connect(path)
    row = conn.execute("SELECT * FROM users WHERE employee_id='plain'").fetchone()
    user = CurrentUser(id=row['id'], employee_id=row['employee_id'], real_name=row['real_name'], role=row['role'], csrf_token='synthetic-csrf')
    scope = conn.execute("SELECT category_key FROM category_nodes WHERE id='cat-04'").fetchone()[0]
    conv = runtime.create_conversation(conn, user_id=user.id)
    conn.close()
    yield path, user, scope, conv, entries, content_api


def test_actual_search_polite_filename_reload_ordinal_and_expired_scope(published_chat):
    path, user, scope, conv, entries, _env = published_chat
    session = ChatSession()
    runtime.configure_attachment_search(session, user)
    result = session.ask('帮我找图片11', [scope])
    assert [r['version_id'] for r in result.file_results] == [entries[0]['version_id']]
    assert not result.file_results[0]['can_download']
    assert result.sources == []
    result = session.ask('查找图片', [scope])
    assert len(result.file_results) == 2
    expected_second = result.file_results[1]['version_id']
    # Persist just this latest turn into the synthetic conversation.
    runtime.persist_turn(runtime.TurnPersistencePlan(conv['id'], '查找图片', True, categories=[scope]), session)
    conn = connect(path)
    restored = runtime.hydrate_session(conn, runtime.get_conversation(conn, conv['id']))
    runtime.configure_attachment_search(restored, user)
    follow = restored.ask('第二个文件', [scope])
    assert [r['version_id'] for r in follow.file_results] == [expected_second]
    conn.execute("UPDATE category_nodes SET chat_search_enabled=0 WHERE id='cat-04'")
    conn.commit()
    assert restored.ask('第一个文件', [scope]).file_results == []
    assert restored.ask('找图片11', []).file_results == []
    conn.close()


def test_http_sse_cards_refresh_auth_and_archive_revalidation(published_chat, monkeypatch):
    from api import routes_chat
    path, user, scope, conv, entries, env = published_chat
    client, sessions, _, _ = env
    client.app.include_router(routes_chat.router, prefix='/api')
    monkeypatch.setattr(routes_chat, 'db_connect', lambda: connect(path))
    url = f"/api/conversations/{conv['id']}"
    assert client.get(url).status_code == 401
    assert client.get(url, **_auth(sessions, 'organizer')).status_code == 403
    assert client.post(url + '/chat', json={'query': '找图片11'}, **_auth(sessions, 'plain')).status_code == 403
    response = client.post(url + '/chat', json={'query': '帮我找图片11', 'category_ids': ['cat-04']}, **_auth(sessions, 'plain', csrf=True))
    assert response.status_code == 200
    payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not any('message' in payload for payload in payloads), response.text
    prep = next(payload for payload in payloads if 'used_sources' in payload)
    done = next(payload for payload in payloads if 'answer_text' in payload)
    assert prep['file_results'][0]['version_id'] == entries[0]['version_id']
    assert done['file_results'][0]['version_id'] == entries[0]['version_id']
    assert prep['used_sources'] == [] and done['sources'] == []
    state = client.get(url, **_auth(sessions, 'plain')).json()
    assistant = state['messages'][-1]
    assert assistant['file_results'][0]['version_id'] == entries[0]['version_id']
    assert assistant['answer_versions'][0]['file_results'][0]['version_id'] == entries[0]['version_id']
    conn = connect(path)
    conn.execute('UPDATE content_items SET archived_at=1 WHERE id=?', (entries[0]['item_id'],))
    conn.commit()
    conn.close()
    state = client.get(url, **_auth(sessions, 'plain')).json()
    assert state['messages'][-1]['file_results'] == []
    assert state['messages'][-1]['answer_versions'][0]['file_results'] == []
