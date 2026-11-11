"""Synthetic database attachment search and closed permission boundary tests."""
import pytest
from api import routes_attachment_search as routes
from api.content_search import search_attachment_files, update_search_metadata, revalidate_attachment_results
from api.db import connect, init_db
from tests.test_content_library_api import content_api, _auth
from tests.test_content_attachments import attachments, upload, publish


@pytest.fixture
def search_env(attachments, monkeypatch):
    client, sessions, _, path = attachments
    client.app.include_router(routes.router, prefix='/api/attachments')
    client.app.include_router(routes.admin_router, prefix='/api/admin/content')
    monkeypatch.setattr(routes.config, 'ATTACHMENT_SEARCH_ENABLED', True)
    conn = connect(path)
    conn.execute("DELETE FROM content_permissions WHERE user_id=(SELECT id FROM users WHERE employee_id='plain')")
    conn.commit()
    conn.close()
    return attachments


@pytest.mark.parametrize('filename,query', [('图片11.png','帮我找一下图片11'), ('消防图纸.dwg','消防图纸'), ('项目A.rvt','找项目A的RVT文件')])
def test_literal_intent_and_short_chinese(search_env, filename, query):
    entry = upload(search_env, filename)
    publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    results = search_attachment_files(conn, query, categories=None)
    assert [x['version_id'] for x in results] == [entry['version_id']]
    assert search_attachment_files(conn, query, categories=[]) == []
    assert search_attachment_files(conn, query, categories=['no-such-category']) == []
    conn.close()


def test_metadata_scopes_and_persisted_cards(search_env):
    entry = upload(search_env, 'opaque.rvt')
    publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    update_search_metadata(conn, entry['version_id'], description='消防设备布置', aliases=['机房'], tags=['ABC11'], project='项目A')
    conn.commit()
    for query in ('消防','机房','ABC11','项目A'):
        assert search_attachment_files(conn, query, categories=None)[0]['version_id'] == entry['version_id']
    saved = search_attachment_files(conn, '消防', categories=None)
    assert revalidate_attachment_results(conn, saved)[0]['snippet'] == ''
    conn.execute("UPDATE category_nodes SET chat_search_enabled=0 WHERE id='cat-04'")
    conn.commit()
    assert search_attachment_files(conn, 'opaque', categories=None) == []
    assert revalidate_attachment_results(conn, saved) == []
    conn.close()


def test_chat_only_cannot_download_opaque_or_read_drafts(search_env):
    client, sessions, _, _ = search_env
    entry = upload(search_env, 'closed.rvt')
    url = '/api/attachments/versions/' + entry['version_id']
    assert client.get(url+'/info').status_code == 401
    assert client.get(url+'/info', **_auth(sessions,'plain')).status_code == 404
    publish(search_env, entry['version_id'])
    info = client.get(url+'/info', **_auth(sessions,'plain'))
    assert info.status_code == 200, info.text
    assert info.json()['source_rel_path'] is None
    assert client.get(url+'/download', **_auth(sessions,'plain')).status_code == 403
    assert client.get(url+'/preview', **_auth(sessions,'plain')).status_code == 404
    admin_url = '/api/admin/content/versions/' + entry['version_id'] + '/search-metadata'
    assert client.patch(admin_url,json={'description':'changed'},**_auth(sessions,'organizer')).status_code == 403
    assert client.patch(admin_url,json={'description':'changed'},**_auth(sessions,'plain',csrf=True)).status_code == 403
    assert client.patch(admin_url,json={'description':'changed'},**_auth(sessions,'organizer',csrf=True)).status_code == 200


@pytest.mark.parametrize('mutation', [
    "UPDATE content_items SET archived_at=1",
    "UPDATE category_nodes SET is_active=0 WHERE id='cat-04'",
    "UPDATE category_nodes SET deleted_at=1 WHERE id='cat-04'",
    "UPDATE content_publications SET status='failed'",
    "UPDATE content_versions SET lifecycle_status='superseded'",
    "DELETE FROM content_item_heads",
])
def test_every_request_rechecks_lifecycle(search_env, mutation):
    entry = upload(search_env, 'scope.rvt')
    publish(search_env,entry['version_id'])
    client,sessions,_,path = search_env
    conn = connect(path)
    saved = search_attachment_files(conn,'scope',categories=None)
    assert saved
    conn.execute(mutation)
    conn.commit()
    assert search_attachment_files(conn,'scope',categories=None)==[]
    assert revalidate_attachment_results(conn,saved)==[]
    assert client.get('/api/attachments/versions/'+entry['version_id']+'/info',**_auth(sessions,'plain')).status_code==404
    conn.close()


def test_new_head_hides_old_cards_and_hidden_ancestor(search_env):
    entry=upload(search_env,'old.rvt')
    publish(search_env,entry['version_id'])
    client,sessions,_,path=search_env
    conn=connect(path)
    saved=search_attachment_files(conn,'old',categories=None)
    response=client.post('/api/admin/content/items/'+entry['item_id']+'/versions',
        data={'expected_version_id':entry['version_id'],'filename_mode':'new'},
        files={'file':('replacement.rvt',b'synthetic-replacement','application/octet-stream')},
        **_auth(sessions,'organizer',csrf=True))
    assert response.status_code==200,response.text
    replacement=response.json()['version_id']
    assert search_attachment_files(conn,'replacement',categories=None)==[]
    assert search_attachment_files(conn,'old',categories=None)
    publish(search_env,replacement)
    assert search_attachment_files(conn,'replacement',categories=None)[0]['version_id']==replacement
    # A replacement preserves its version title; it may legitimately match 'old',
    # but the historical version must never be returned.
    assert all(card['version_id']==replacement for card in search_attachment_files(conn,'old',categories=None))
    assert revalidate_attachment_results(conn,saved)==[]
    conn.execute("UPDATE category_nodes SET parent_id='cat-03' WHERE id='cat-04'")
    conn.execute("UPDATE category_nodes SET chat_search_enabled=0 WHERE id='cat-03'")
    conn.commit()
    assert search_attachment_files(conn,'replacement',categories=None)==[]
    conn.close()


def test_ocr_admin_auth_csrf_and_disabled_state(search_env,monkeypatch):
    client,sessions,_,_=search_env
    entry=upload(search_env,'ocr.png')
    url='/api/admin/content/versions/'+entry['version_id']+'/ocr'
    monkeypatch.setattr(routes.config,'IMAGE_OCR_ENABLED',False)
    monkeypatch.setattr(routes.config,'IMAGE_OCR_TOKEN','')
    assert client.get(url).status_code==401
    assert client.get(url,**_auth(sessions,'plain')).status_code==403
    status=client.get(url,**_auth(sessions,'organizer'))
    assert status.status_code==200,status.text
    assert status.json()['enabled'] is False and status.json()['available'] is False
    assert client.post(url,**_auth(sessions,'publisher')).status_code==403
    assert client.post(url,**_auth(sessions,'organizer',csrf=True)).status_code==403
    assert client.post(url,**_auth(sessions,'publisher',csrf=True)).status_code==409
    monkeypatch.setattr(routes.config,'IMAGE_OCR_ENABLED',True)
    enqueued=client.post(url,**_auth(sessions,'publisher',csrf=True))
    assert enqueued.status_code==200,enqueued.text
    assert enqueued.json()['status']=='pending'
    assert client.post(url+'/cancel',**_auth(sessions,'publisher')).status_code==403
    assert client.post(url+'/cancel',**_auth(sessions,'publisher',csrf=True)).json()['cancelled']==1


@pytest.mark.parametrize('indexed', [True, False])
def test_directory_only_rename_move_and_scope(search_env, indexed):
    entry = upload(search_env, 'opaque.rvt')
    publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    if not indexed:
        conn.execute('DELETE FROM attachment_search_documents')
        conn.execute('DELETE FROM attachment_search_terms')
    conn.execute("UPDATE category_nodes SET display_name='合成消防目录' WHERE id='cat-04'")
    conn.commit()
    cards = search_attachment_files(conn, '合成消防目录', categories=None)
    assert [card['version_id'] for card in cards] == [entry['version_id']]
    assert cards[0]['matched_fields'] == ['category_path']
    assert '合成消防目录' in cards[0]['snippet']
    assert search_attachment_files(conn, '合成消防目录', categories=['unrelated']) == []
    assert search_attachment_files(conn, '合成消防目录', categories=[]) == []
    conn.execute("UPDATE category_nodes SET display_name='合成暖通目录' WHERE id='cat-04'")
    conn.commit()
    assert search_attachment_files(conn, '合成消防目录', categories=None) == []
    assert search_attachment_files(conn, '合成暖通目录', categories=None)
    conn.execute("UPDATE category_nodes SET display_name='合成目标父目录' WHERE id='cat-03'")
    conn.execute("UPDATE category_nodes SET parent_id='cat-03' WHERE id='cat-04'")
    conn.commit()
    assert search_attachment_files(conn, '合成目标父目录', categories=None)
    assert search_attachment_files(conn, '合成目标父目录 opaque', categories=None)
    assert '合成目标父目录' in revalidate_attachment_results(conn, cards)[0]['category_path']
    for field in ('chat_search_enabled', 'is_active'):
        conn.execute(f"UPDATE category_nodes SET {field}=0 WHERE id='cat-03'")
        conn.commit()
        assert search_attachment_files(conn, '合成暖通目录', categories=None) == []
        assert revalidate_attachment_results(conn, cards) == []
        conn.execute(f"UPDATE category_nodes SET {field}=1 WHERE id='cat-03'")
    conn.execute("UPDATE content_items SET category_id='cat-03' WHERE id=?", (entry['item_id'],))
    conn.commit()
    assert search_attachment_files(conn, '合成暖通目录', categories=None) == []
    assert search_attachment_files(conn, '合成目标父目录', categories=None)
    conn.close()


def test_synthetic_retrieval_golden_and_latency(search_env, monkeypatch):
    import math
    import time
    from api import image_ocr_jobs as jobs
    from src import config
    monkeypatch.setattr(config, 'IMAGE_OCR_ENABLED', False)
    names = ['合成检索.png', 'metadata.png', 'ocr.png', 'directory.rvt']
    entries = [upload(search_env, name) for name in names]
    for entry in entries:
        publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    update_search_metadata(conn, entries[1]['version_id'], aliases=['合成检索'], tags=['合成标签'], description='合成描述')
    monkeypatch.setattr(config, 'IMAGE_OCR_ENABLED', True)
    jobs.enqueue_image_ocr(conn, entries[2]['version_id'])
    conn.commit()
    lease = jobs.lease_image_ocr(conn)
    assert jobs.finish_image_ocr(conn, lease, result={
        'text': '合成检索 合成独有文字', 'blocks': [], 'engine': 'fake-local', 'model_version': 'test-v1'})
    conn.execute("UPDATE category_nodes SET display_name='合成目录' WHERE id='cat-04'")
    conn.commit()
    ranked = search_attachment_files(conn, '合成检索', categories=None)
    assert [card['version_id'] for card in ranked] == [entry['version_id'] for entry in entries[:3]]
    golden = [('合成检索.png', entries[0]), ('合成标签', entries[1]),
              ('合成描述', entries[1]), ('合成独有文字', entries[2]),
              ('合成目录 directory', entries[3]), ('合成目录 合成独有文字', entries[2])]
    ranks, elapsed = [], []
    for _ in range(10):
        for query, expected in golden:
            start = time.perf_counter()
            cards = search_attachment_files(conn, query, categories=None)
            elapsed.append((time.perf_counter() - start) * 1000)
            ids = [card['version_id'] for card in cards]
            ranks.append(ids.index(expected['version_id']) + 1 if expected['version_id'] in ids else 0)
    recall1 = sum(rank == 1 for rank in ranks) / len(ranks)
    recall5 = sum(0 < rank <= 5 for rank in ranks) / len(ranks)
    mrr = sum(1 / rank if rank else 0 for rank in ranks) / len(ranks)
    assert (recall1, recall5, mrr) == (1, 1, 1)
    assert search_attachment_files(conn, '不存在的合成词', categories=None) == []
    p95 = sorted(elapsed)[math.ceil(len(elapsed) * .95) - 1]
    print(f'synthetic golden: queries={len(golden)}, runs={len(ranks)}, Recall@1={recall1:.3f}, '
          f'Recall@5={recall5:.3f}, MRR={mrr:.3f}, no-answer=1/1, p95_ms={p95:.3f}')
    conn.close()


@pytest.mark.parametrize('query', [
    '帮我寻找一个地下室一层含开闭所的案例图纸',
    '请查找地下一层包含开闭所的图纸',
    '找负一层带有开闭所的图纸',
    '搜索 b1 开闭所 图纸',
])
def test_natural_language_plan_constraints(search_env, query):
    names = ['地下室一层_开闭所.dwg', '地下室二层_开闭所.dwg',
             '地上一层_开闭所.dwg', '地下室一层_消防泵房.dwg',
             '地下室一层_不含开闭所.dwg', 'opaque.dwg']
    entries = [upload(search_env, name) for name in names]
    for entry in entries:
        publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    update_search_metadata(conn, entries[-1]['version_id'], project='合成项目甲', description='地下一层 开闭所 平面布置')
    conn.commit()
    ids = [card['version_id'] for card in search_attachment_files(conn, query, categories=None)]
    assert ids == [entries[0]['version_id'], entries[-1]['version_id']]
    assert search_attachment_files(conn, query, categories=[]) == []
    assert search_attachment_files(conn, query, categories=['unrelated']) == []
    assert [c['version_id'] for c in search_attachment_files(conn, '找合成项目甲 地下一层含开闭所的图纸', categories=None)] == [entries[-1]['version_id']]
    for negative in ('找合成项目乙 地下一层含开闭所的图纸', '找地下三层含开闭所的图纸',
                     '找地下一层不含开闭所的图纸', '找地下一层没有开闭所的图纸',
                     '找地下一层开闭所 设备编号123 图纸'):
        assert search_attachment_files(conn, negative, categories=None) == []
    conn.close()


def test_lexical_parser_keeps_unknown_identity_and_bounds():
    from src.attachment_search import attachment_query_terms
    from api.content_search import _term_match
    assert attachment_query_terms('找内含项目 地下一层含开闭所的图纸') == ['内含项目', '地下一层', '开闭所']
    assert attachment_query_terms('消防图纸') == ['消防图纸']
    assert attachment_query_terms('找' + ' '.join(f'id{i}' for i in range(13))) == []
    assert _term_match('ABC1234.dwg', 'abc123') == 0
    assert _term_match('XABC123.dwg', 'abc123') == 0
    assert _term_match('abc123.DWG', 'abc123') == 1
    assert _term_match('B11 开闭所', 'b1') == 0
    assert _term_match('地下十一层 开闭所', '地下一层') == 0
    assert _term_match('地下室一层 不含开闭所', '开闭所') == 0


def test_exact_name_stopwords_and_numeric_near_misses(search_env):
    entries = [upload(search_env, name) for name in ('不含开闭所的案例图纸.dwg', '图片11.png', '图片111.png')]
    for entry in entries:
        publish(search_env, entry['version_id'])
    conn = connect(search_env[3])
    assert search_attachment_files(conn, '不含开闭所的案例图纸.dwg', categories=None)[0]['version_id'] == entries[0]['version_id']
    assert [c['version_id'] for c in search_attachment_files(conn, '图片11', categories=None)] == [entries[1]['version_id']]
    conn.close()


def test_upgrade_from_45_and_replay(tmp_path, monkeypatch):
    from api import db_migrations
    path = tmp_path/'upgrade.sqlite'
    migrations = db_migrations.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db_migrations,'MIGRATIONS',tuple(m for m in migrations if m.version <=45))
        init_db(path,backup_dir=tmp_path/'backups')
    conn = connect(path)
    assert conn.execute('SELECT max(version) FROM app_schema_migrations').fetchone()[0] ==45
    categories = [tuple(r) for r in conn.execute('SELECT * FROM category_nodes ORDER BY id')]
    conn.close()
    init_db(path,backup_dir=tmp_path/'backups')
    init_db(path,backup_dir=tmp_path/'backups')
    conn = connect(path)
    assert conn.execute('SELECT max(version) FROM app_schema_migrations').fetchone()[0] == migrations[-1].version
    assert categories == [tuple(r) for r in conn.execute('SELECT * FROM category_nodes ORDER BY id')]
    for table in ('messages','message_answer_versions'):
        assert 'file_results_json' in [r['name'] for r in conn.execute('PRAGMA table_info('+table+')')]
    assert conn.execute('SELECT count(*) FROM image_ocr_jobs').fetchone()[0] ==0
    conn.close()
