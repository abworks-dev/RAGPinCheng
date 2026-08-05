from __future__ import annotations

import importlib.util
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace

import requests
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_faster_whisper_model.py"
SPEC = importlib.util.spec_from_file_location(
    "prepare_faster_whisper_model_tls_test", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _assert_tls12_verified(context: ssl.SSLContext) -> None:
    assert context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_hugging_face_backend_limits_direct_and_proxy_tls() -> None:
    session = MODULE._hugging_face_backend()
    adapter = session.adapters["https://"]
    assert isinstance(adapter, MODULE._TLS12HTTPAdapter)

    context = adapter.poolmanager.connection_pool_kw["ssl_context"]
    _assert_tls12_verified(context)

    proxy_manager = adapter.proxy_manager_for("http://127.0.0.1:7897")
    assert proxy_manager.connection_pool_kw["ssl_context"] is context

    request = requests.Request(
        "HEAD", "https://huggingface.co/fixed/pinned/config.json"
    ).prepare()
    _, request_pool_kwargs = adapter.build_connection_pool_key_attributes(
        request, verify=True
    )
    assert request_pool_kwargs["ssl_context"] is context

    with pytest.raises(
        RuntimeError, match="requires default certificate verification"
    ):
        adapter.build_connection_pool_key_attributes(request, verify=False)


def test_default_downloader_registers_scoped_backend(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def configure_http_backend(*, backend_factory):
        captured["backend_factory"] = backend_factory

    def snapshot_download(**kwargs):
        captured["download_kwargs"] = kwargs
        return "download-root"

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            configure_http_backend=configure_http_backend,
            snapshot_download=snapshot_download,
        ),
    )

    result = MODULE._default_downloader(repo_id="fixed", revision="pinned")

    assert result == "download-root"
    assert captured["backend_factory"] is MODULE._hugging_face_backend
    assert captured["download_kwargs"] == {
        "repo_id": "fixed",
        "revision": "pinned",
    }
    session = captured["backend_factory"]()
    assert isinstance(session, requests.Session)
