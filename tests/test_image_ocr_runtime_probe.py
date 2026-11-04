"""Runtime probe CI uses fake HTTP only; no production DB, token or OCR service."""
import httpx
import pytest

from scripts import verify_local_image_ocr as probe


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_ENABLED', True)
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_URL', 'http://127.0.0.1:8091')
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_TOKEN', 'synthetic-ci-token')
    original = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == '/health':
            return httpx.Response(200, json={'status': 'ok', 'api_version': 'image-ocr/1'})
        if request.headers.get('authorization') != 'Bearer synthetic-ci-token':
            return httpx.Response(401)
        assert b'filename="sample.png"' in request.content
        assert probe._fixture_bytes() in request.content
        return httpx.Response(200, json={'text': '消防通道 禁止堆放', 'blocks': [], 'engine': 'synthetic-ci', 'model_version': 'test'})

    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(probe.httpx, 'Client', client)
    return requests


def test_runtime_probe_durable_pipeline_and_preview(fake_http, capsys):
    previous_storage = probe.content_attachments._storage
    report = probe.verify()
    assert report['persistent_queue_verified']
    assert report['ocr_only_search_verified']
    assert report['ocr_head_verified']
    assert report['chat_only_preview_bytes_verified']
    assert report['historical_backfill'] is False
    assert report['draft_preview_status'] == report['archived_preview_status'] == 404
    assert probe.content_attachments._storage is previous_storage
    assert len(fake_http) == 3
    output = capsys.readouterr().out
    assert 'synthetic-ci-token' not in output
    assert 'http://' not in output
    assert '消防通道' not in output


def test_disabled_probe_never_opens_database_or_http(monkeypatch):
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_ENABLED', False)
    def forbidden(*args, **kwargs):
        pytest.fail('disabled probe touched HTTP or DB')
    monkeypatch.setattr(probe, 'init_db', forbidden)
    monkeypatch.setattr(probe.httpx, 'Client', forbidden)
    assert probe.verify()['enabled'] is False


def test_endpoint_rejected_before_any_http(monkeypatch):
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_ENABLED', True)
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_URL', 'https://example.com')
    monkeypatch.setattr(probe.config, 'IMAGE_OCR_TOKEN', 'synthetic-ci-token')
    def forbidden(*args, **kwargs):
        pytest.fail('unvalidated endpoint reached HTTP')
    monkeypatch.setattr(probe.httpx, 'Client', forbidden)
    with pytest.raises(ValueError, match='image_ocr_nonlocal_url'):
        probe.verify()


def test_wrong_chinese_ocr_fails_closed(fake_http, monkeypatch, tmp_path):
    original = probe.image_ocr_jobs.local_ocr_client
    def wrong_text(*args, **kwargs):
        result = original(*args, **kwargs)
        result['text'] = 'unrelated synthetic text'
        return result
    monkeypatch.setattr(probe.image_ocr_jobs, 'local_ocr_client', wrong_text)
    with pytest.raises(AssertionError, match='probe_expected_chinese_missing'):
        probe._verify_pipeline(tmp_path, probe._fixture_bytes())
