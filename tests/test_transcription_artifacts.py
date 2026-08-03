from pathlib import Path

import pytest

from api.transcription_artifacts import LocalTranscriptionArtifactStore
from src.transcription.persistence import ManagedMarkdownRef
from src.transcription.types import ContractValidationError, sha256_hex


def test_markdown_is_content_addressed_and_idempotent(tmp_path):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    content = "# 自动转录\n\n说话人 1 00:00:00\n你好。\n".encode("utf-8")
    first = store.write_markdown(content)
    second = store.write_markdown(content)
    assert first == second
    assert first.relative_path == f"markdown/{first.content_sha256[:2]}/{first.content_sha256}.md"
    assert store.load_verified(first) == content


def test_store_preserves_exact_crlf_bom_bytes(tmp_path):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    content = b"\xef\xbb\xbfline1\r\nline2\r\n"
    reference = store.write_markdown(content)
    assert reference.content_sha256 == sha256_hex(content)
    assert store.load_verified(reference) == content


def test_load_rejects_tampered_content(tmp_path):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    reference = store.write_markdown(b"expected")
    path = store.root / Path(*reference.relative_path.split("/"))
    path.write_bytes(b"tampered")
    with pytest.raises(ContractValidationError):
        store.load_verified(reference)


def test_load_rejects_path_escape_without_reading_it(tmp_path):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    object.__setattr__(
        (reference := ManagedMarkdownRef("markdown/aa/" + "a" * 64 + ".md", "a" * 64, 1)),
        "relative_path",
        "../secret",
    )
    with pytest.raises(ContractValidationError):
        store.load_verified(reference)


def test_existing_hash_collision_fails_closed(tmp_path, monkeypatch):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    content = b"expected"
    reference = store.write_markdown(content)
    path = store.root / Path(*reference.relative_path.split("/"))
    path.write_bytes(b"different")
    monkeypatch.setattr("api.transcription_artifacts.sha256_hex", lambda _content: reference.content_sha256)
    with pytest.raises(ContractValidationError):
        store.write_markdown(content)


def test_atomic_replace_failure_does_not_publish_final(tmp_path, monkeypatch):
    store = LocalTranscriptionArtifactStore((tmp_path / "artifacts").resolve())
    content = b"not published"
    digest = sha256_hex(content)
    final_path = store.root / "markdown" / digest[:2] / f"{digest}.md"
    monkeypatch.setattr("api.transcription_artifacts.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(OSError):
        store.write_markdown(content)
    assert not final_path.exists()
    assert not list(final_path.parent.glob("*.tmp"))
