"""Synthetic only: leases, restart recovery, atomic derived heads and OCR limits."""
import struct
import time
import pytest
from api import image_ocr_jobs as jobs
from api.content_search import search_attachment_files, reconcile_attachment_index
from api.db import connect
from src import config
from tests.test_content_library_api import content_api
from tests.test_content_attachments import attachments, upload, publish


def result(text):
    return {'text':text,'blocks':[],'engine':'fake-local','model_version':'test-v1'}


@pytest.fixture
def image_env(attachments,monkeypatch):
    monkeypatch.setattr(config,'IMAGE_OCR_ENABLED',False)
    entry = upload(attachments,'图片11.png')
    publish(attachments,entry['version_id'])
    conn = connect(attachments[3])
    assert conn.execute('SELECT count(*) FROM image_ocr_jobs').fetchone()[0] ==0
    reconcile_attachment_index(conn)
    conn.commit()
    assert conn.execute('SELECT count(*) FROM image_ocr_jobs').fetchone()[0] ==0
    monkeypatch.setattr(config,'IMAGE_OCR_ENABLED',True)
    yield conn,entry['version_id']
    conn.close()


def enqueue_lease(conn,version):
    pending = jobs.enqueue_image_ocr(conn,version)
    assert jobs.enqueue_image_ocr(conn,version)['id'] == pending['id']
    conn.commit()
    return jobs.lease_image_ocr(conn)


def test_atomic_success_failed_retry_no_text(image_env):
    conn,version=image_env
    first=enqueue_lease(conn,version)
    assert search_attachment_files(conn,'消防',categories=None)==[]
    assert jobs.finish_image_ocr(conn,first,result=result('消防原图文字'))
    assert search_attachment_files(conn,'消防',categories=None)[0]['version_id']==version
    second=enqueue_lease(conn,version)
    assert jobs.finish_image_ocr(conn,second,error_code='image_ocr_timeout')
    assert conn.execute('SELECT job_id FROM image_ocr_heads WHERE version_id=?',(version,)).fetchone()[0]==first['id']
    assert search_attachment_files(conn,'消防',categories=None)
    third=enqueue_lease(conn,version)
    assert jobs.finish_image_ocr(conn,third,result=result('  '))
    assert conn.execute('SELECT status FROM image_ocr_jobs WHERE id=?',(third['id'],)).fetchone()[0]=='no_text'
    assert search_attachment_files(conn,'消防',categories=None)==[]


def test_cancel_and_lease_restart_reject_old_result(image_env):
    conn,version=image_env
    first=enqueue_lease(conn,version)
    assert jobs.cancel_image_ocr(conn,version)==1
    conn.commit()
    assert not jobs.finish_image_ocr(conn,first,result=result('不可见'))
    second=enqueue_lease(conn,version)
    recovered=jobs.lease_image_ocr(conn,now=second['lease_until']+1)
    assert recovered['id']==second['id']
    assert recovered['lease_token']!=second['lease_token']
    assert not jobs.finish_image_ocr(conn,second,result=result('不可见'))
    assert jobs.finish_image_ocr(conn,recovered,result=result('恢复文字'))


def test_superseded_completion_never_promotes(image_env):
    conn,version=image_env
    job=enqueue_lease(conn,version)
    conn.execute("UPDATE content_versions SET lifecycle_status='superseded' WHERE id=?",(version,))
    conn.commit()
    assert jobs.finish_image_ocr(conn,job,result=result('不可见'))
    assert conn.execute('SELECT status FROM image_ocr_jobs WHERE id=?',(job['id'],)).fetchone()[0]=='stale'
    assert conn.execute('SELECT count(*) FROM image_ocr_heads').fetchone()[0]==0


@pytest.mark.parametrize('url',['https://example.com','http://8.8.8.8','http://169.254.169.254','http://0.0.0.0','http://image-ocr/redirect','http://user:pass@localhost'])
def test_public_or_ambiguous_ocr_url_rejected(tmp_path,monkeypatch,url):
    path=tmp_path/'image.png'
    path.write_bytes(bytes.fromhex('89504e470d0a1a0a')+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',1,1))
    monkeypatch.setattr(config,'IMAGE_OCR_URL',url)
    monkeypatch.setattr(config,'IMAGE_OCR_TOKEN','synthetic-only')
    with pytest.raises(ValueError,match='image_ocr_(nonlocal|invalid)_url'):
        jobs.local_ocr_client(path,kind='png',filename='image.png')


def test_worker_original_hash_and_auto_enable_gate(attachments,monkeypatch):
    monkeypatch.setattr(config,'IMAGE_OCR_ENABLED',True)
    entry = upload(attachments,'original.png',b'synthetic-original')
    conn = connect(attachments[3])
    assert conn.execute('SELECT count(*) FROM image_ocr_jobs').fetchone()[0]==1
    from api import routes_content
    calls=[]
    def client(path,**kwargs):
        calls.append((path.read_bytes(),kwargs))
        return result('原图文字')
    assert jobs.run_one_image_ocr(conn,storage=routes_content._storage,client=client)
    assert calls[0][0]==b'synthetic-original'
    assert calls[0][1]['kind']=='png'
    publish(attachments,entry['version_id'])
    assert search_attachment_files(conn,'原图',categories=None)
    next_job=jobs.enqueue_image_ocr(conn,entry['version_id'])
    conn.commit()
    row=conn.execute('SELECT storage_rel_path FROM content_objects WHERE sha256=?',(next_job['source_sha256'],)).fetchone()
    routes_content._storage.resolve_object(row['storage_rel_path']).write_bytes(b'synthetic-tamper')
    assert jobs.run_one_image_ocr(conn,storage=routes_content._storage,client=client)
    assert len(calls)==1
    assert conn.execute('SELECT error_code FROM image_ocr_jobs WHERE id=?',(next_job['id'],)).fetchone()[0]=='image_ocr_source_changed'
    conn.close()


def test_local_transport_contract_and_redirect_rejection(tmp_path,monkeypatch):
    import httpx
    path=tmp_path/'image.png'
    path.write_bytes(bytes.fromhex('89504e470d0a1a0a')+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',1,1))
    monkeypatch.setattr(config,'IMAGE_OCR_URL','http://image-ocr:8091')
    monkeypatch.setattr(config,'IMAGE_OCR_TOKEN','synthetic-only')
    original=httpx.Client
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['authorization']=='Bearer synthetic-only'
        assert request.url.path=='/v1/ocr'
        return httpx.Response(200,json=result('本地文字'))
    monkeypatch.setattr(jobs.httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))
    assert jobs.local_ocr_client(path,kind='png',filename='image.png')['text']=='本地文字'
    def redirect(request):
        return httpx.Response(302,headers={'location':'https://example.com'})
    monkeypatch.setattr(jobs.httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(redirect),**kwargs))
    with pytest.raises(ValueError,match='image_ocr_service_302'):
        jobs.local_ocr_client(path,kind='png',filename='image.png')
    assert len(requests)==1
    monkeypatch.setattr(config,'IMAGE_OCR_TOKEN','')
    assert jobs.ocr_availability()['available'] is False


def test_pixel_and_byte_limits_precede_service(tmp_path,monkeypatch):
    path=tmp_path/'image.png'
    path.write_bytes(bytes.fromhex('89504e470d0a1a0a')+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',100000,100000))
    with pytest.raises(ValueError,match='image_ocr_pixel_limit'):
        jobs.local_ocr_client(path,kind='png',filename='image.png')
    monkeypatch.setattr(config,'IMAGE_OCR_MAX_BYTES',1)
    with pytest.raises(ValueError,match='image_ocr_too_large'):
        jobs.local_ocr_client(path,kind='png',filename='image.png')
