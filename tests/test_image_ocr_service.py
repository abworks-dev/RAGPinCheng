"""Synthetic, offline service boundary tests; real engine smoke lives with the service."""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from services.image_ocr import app as service


def _image(kind="PNG", size=(120, 60)):
    data = io.BytesIO()
    Image.new("RGB", size, "white").save(data, format=kind)
    return data.getvalue()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "synthetic-local-test-token")
    monkeypatch.setattr(service, "_SEMAPHORE", asyncio.Semaphore(1))
    return TestClient(service.app)


def _post(client, data, **kwargs):
    return client.post("/v1/ocr", headers={"Authorization": "Bearer synthetic-local-test-token"}, files={"file": ("synthetic.image", data)}, **kwargs)


def test_authentication_precedes_body_parsing(client, monkeypatch):
    assert client.post("/v1/ocr", content=b"not-multipart").status_code == 401
    assert client.post("/v1/ocr", headers={b"authorization": b"Bearer \xff"}, content=b"not-multipart").status_code == 401
    monkeypatch.setattr(service, "TOKEN", "")
    assert _post(client, _image()).status_code == 503


@pytest.mark.parametrize("kind", ["PNG", "JPEG"])
def test_original_image_contract_and_temporary_cleanup(client, monkeypatch, kind):
    paths = []

    async def fake_worker(path):
        paths.append(path)
        assert path.is_file()
        service.validate_image(path.read_bytes())
        return {"text": "SYNTHETIC 11", "blocks": [], "engine": "test", "model_version": "test"}

    monkeypatch.setattr(service, "run_worker", fake_worker)
    response = _post(client, _image(kind))
    assert response.status_code == 200
    assert response.json()["text"] == "SYNTHETIC 11"
    assert paths and not paths[0].exists()


@pytest.mark.parametrize("data,status", [(b"invalid", 422), (_image("GIF"), 415)])
def test_reject_invalid_and_non_image_types(client, data, status):
    assert _post(client, data).status_code == status


def test_bounds_file_and_pixel_count(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_BYTES", 16)
    assert _post(client, _image()).status_code == 413
    monkeypatch.setattr(service, "MAX_BYTES", 100000)
    monkeypatch.setattr(service, "MAX_PIXELS", 10)
    assert _post(client, _image()).status_code == 413


def test_bounds_multipart_stream_before_form_spooling(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_BYTES", 1)
    response = client.post("/v1/ocr", headers={"Authorization": "Bearer synthetic-local-test-token"}, content=b"x" * 70000)
    assert response.status_code == 413


def test_timeout_kills_native_worker(monkeypatch, tmp_path):
    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(5)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(service, "TIMEOUT", 0.01)
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.run_worker(tmp_path / "source.image"))
    assert error.value.status_code == 504
    assert process.killed


def test_no_text_is_a_valid_engine_response(client, monkeypatch):
    async def fake_worker(path):
        return {"text": "", "blocks": [], "engine": "test", "model_version": "test"}

    monkeypatch.setattr(service, "run_worker", fake_worker)
    assert _post(client, _image()).json()["text"] == ""
