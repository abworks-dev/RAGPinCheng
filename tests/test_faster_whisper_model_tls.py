from __future__ import annotations

import importlib.util
import ssl
from pathlib import Path

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


def test_model_bin_identity_matches_pinned_hugging_face_lfs_metadata() -> None:
    assert MODULE.MODEL_BIN_SIZE_BYTES == 3_087_284_237
    assert (
        MODULE.MODEL_BIN_SHA256
        == "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
    )


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


def test_default_downloader_uses_one_session_and_fixed_files(
    tmp_path, monkeypatch
) -> None:
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    session = FakeSession()
    captured: list[tuple[object, str, Path]] = []

    def download_file(active_session, *, url, destination):
        captured.append((active_session, url, destination))
        destination.write_bytes(destination.name.encode())

    monkeypatch.setattr(MODULE, "_hugging_face_backend", lambda: session)
    monkeypatch.setattr(MODULE, "_download_fixed_file", download_file)
    download_root = tmp_path / "download"
    download_root.mkdir()

    result = MODULE._default_downloader(
        repo_id=MODULE.FASTER_WHISPER_MODEL_ID,
        revision=MODULE.FASTER_WHISPER_REVISION,
        local_dir=str(download_root),
    )

    assert result == str(download_root.resolve())
    assert session.closed is True
    assert [destination.name for _, _, destination in captured] == list(
        MODULE.FIXED_MODEL_FILES
    )
    assert {id(active_session) for active_session, _, _ in captured} == {
        id(session)
    }
    assert all(
        f"/resolve/{MODULE.FASTER_WHISPER_REVISION}/" in url
        for _, url, _ in captured
    )


def test_default_downloader_rejects_unpinned_identity(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="identity mismatch"):
        MODULE._default_downloader(
            repo_id="other/model",
            revision=MODULE.FASTER_WHISPER_REVISION,
            local_dir=str(tmp_path),
        )


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        chunks: tuple[bytes, ...] = (),
        headers: dict[str, str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.headers = headers or {}
        self.is_redirect = status_code in {301, 302, 303, 307}
        self.is_permanent_redirect = status_code == 308
        self.closed = False
        self._stream_error = stream_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, *, chunk_size: int):
        assert chunk_size == MODULE.DOWNLOAD_CHUNK_BYTES
        yield from self._chunks
        if self._stream_error is not None:
            raise self._stream_error

    def close(self) -> None:
        self.closed = True


def test_fixed_file_download_resumes_partial_file(tmp_path) -> None:
    destination = tmp_path / "model.bin"
    partial = MODULE._partial_path(destination)
    partial.write_bytes(b"abc")
    response = _FakeResponse(
        status_code=206,
        chunks=(b"def", b"ghi"),
        headers={"Content-Range": "bytes 3-8/9", "Content-Length": "6"},
    )

    class FakeSession:
        def get(self, url, **kwargs):
            assert kwargs["headers"]["Range"] == "bytes=3-"
            assert kwargs["headers"]["Accept-Encoding"] == "identity"
            return response

    MODULE._download_fixed_file(
        FakeSession(),
        url="https://huggingface.co/fixed/revision/model.bin",
        destination=destination,
    )

    assert destination.read_bytes() == b"abcdefghi"
    assert not partial.exists()
    assert response.closed is True


def test_fixed_file_download_rejects_redirect_outside_approved_hosts(
    tmp_path,
) -> None:
    response = _FakeResponse(
        status_code=302,
        headers={"Location": "https://example.invalid/model.bin"},
    )

    class FakeSession:
        def get(self, url, **kwargs):
            return response

    with pytest.raises(RuntimeError, match="escaped approved"):
        MODULE._download_fixed_file(
            FakeSession(),
            url="https://huggingface.co/fixed/revision/model.bin",
            destination=tmp_path / "model.bin",
        )

    assert response.closed is True


def test_fixed_file_download_retries_from_streamed_offset(tmp_path) -> None:
    destination = tmp_path / "model.bin"
    responses = [
        _FakeResponse(
            status_code=200,
            chunks=(b"abc",),
            stream_error=requests.ConnectionError("interrupted"),
        ),
        _FakeResponse(
            status_code=206,
            chunks=(b"def",),
            headers={"Content-Range": "bytes 3-5/6", "Content-Length": "3"},
        ),
    ]
    observed_ranges: list[str | None] = []

    class FakeSession:
        def get(self, url, **kwargs):
            observed_ranges.append(kwargs["headers"].get("Range"))
            return responses.pop(0)

    MODULE._download_fixed_file(
        FakeSession(),
        url="https://huggingface.co/fixed/revision/model.bin",
        destination=destination,
    )

    assert observed_ranges == [None, "bytes=3-"]
    assert destination.read_bytes() == b"abcdef"
