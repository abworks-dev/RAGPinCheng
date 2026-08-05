from __future__ import annotations

import hashlib
import json

import pytest

from asr_service.model_cache import (
    FASTER_WHISPER_MODEL_ID,
    FASTER_WHISPER_RELATIVE_PATH,
    FASTER_WHISPER_REVISION,
    MODEL_MANIFEST_VERSION,
    QWEN3_ALIGNER_MODEL_ID,
    QWEN3_ALIGNER_RELATIVE_PATH,
    QWEN3_ALIGNER_REVISION,
    QWEN3_ASR_MODEL_ID,
    QWEN3_ASR_RELATIVE_PATH,
    QWEN3_ASR_REVISION,
    SENSEVOICE_MODEL_ID,
    SENSEVOICE_RELATIVE_PATH,
    SENSEVOICE_REVISION,
    validate_faster_whisper_cache,
    validate_qwen3_aligner_cache,
    validate_qwen3_asr_cache,
    validate_sensevoice_cache,
)


def make_cache(
    tmp_path,
    *,
    mutate=None,
    model_id=SENSEVOICE_MODEL_ID,
    revision=SENSEVOICE_REVISION,
    relative_path=SENSEVOICE_RELATIVE_PATH,
):
    root = tmp_path / "models"
    model = root.joinpath(*relative_path.split("/"))
    model.mkdir(parents=True)
    content = b"offline-model-bytes"
    artifact = model / "model.bin"
    artifact.write_bytes(content)
    payload = {
        "schema_version": MODEL_MANIFEST_VERSION,
        "model_id": model_id,
        "model_revision": revision,
        "model_path": relative_path,
        "files": [{
            "path": "model.bin",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    if mutate:
        mutate(payload, root, model)
    manifest = model / "model-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return root, manifest, model


def test_valid_manifest_resolves_exact_local_model_path(tmp_path):
    root, manifest, model = make_cache(tmp_path)
    status = validate_sensevoice_cache(root, manifest)
    assert status.available is True
    assert status.reason_code == "available"
    assert status.model_path == model.resolve()


def test_valid_faster_whisper_manifest_resolves_only_pinned_revision(tmp_path):
    root, manifest, model = make_cache(
        tmp_path,
        model_id=FASTER_WHISPER_MODEL_ID,
        revision=FASTER_WHISPER_REVISION,
        relative_path=FASTER_WHISPER_RELATIVE_PATH,
    )
    status = validate_faster_whisper_cache(root, manifest)
    assert status.available is True
    assert status.model_path == model.resolve()
    assert validate_sensevoice_cache(root, manifest).reason_code == (
        "model-identity-mismatch"
    )


@pytest.mark.parametrize(
    ("model_id", "revision", "relative_path", "validator", "other_validator"),
    [
        (
            QWEN3_ASR_MODEL_ID,
            QWEN3_ASR_REVISION,
            QWEN3_ASR_RELATIVE_PATH,
            validate_qwen3_asr_cache,
            validate_qwen3_aligner_cache,
        ),
        (
            QWEN3_ALIGNER_MODEL_ID,
            QWEN3_ALIGNER_REVISION,
            QWEN3_ALIGNER_RELATIVE_PATH,
            validate_qwen3_aligner_cache,
            validate_qwen3_asr_cache,
        ),
    ],
)
def test_qwen_model_manifests_are_pinned_and_not_interchangeable(
    tmp_path, model_id, revision, relative_path, validator, other_validator
):
    root, manifest, model = make_cache(
        tmp_path,
        model_id=model_id,
        revision=revision,
        relative_path=relative_path,
    )
    assert validator(root, manifest).model_path == model.resolve()
    assert other_validator(root, manifest).reason_code == "model-identity-mismatch"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload, _root, _model: payload.__setitem__("extra", None),
        lambda payload, _root, _model: payload.__setitem__("schema_version", "asr-model-manifest/2"),
        lambda payload, _root, _model: payload.__setitem__("model_revision", "0" * 40),
        lambda payload, _root, _model: payload.__setitem__("model_path", "other/path"),
        lambda payload, _root, _model: payload.__setitem__("files", []),
        lambda payload, _root, _model: payload["files"][0].__setitem__("size_bytes", True),
        lambda payload, _root, _model: payload["files"][0].__setitem__("sha256", "A" * 64),
        lambda payload, _root, _model: payload["files"][0].__setitem__("extra", "x"),
        lambda payload, _root, _model: payload["files"][0].__setitem__("path", r"nested\model.bin"),
        lambda payload, _root, _model: payload["files"][0].__setitem__("path", "/absolute/model.bin"),
    ],
)
def test_manifest_schema_and_identity_fail_closed(tmp_path, mutate):
    root, manifest, _model = make_cache(tmp_path, mutate=mutate)
    assert validate_sensevoice_cache(root, manifest).available is False


def test_tampered_file_fails_closed(tmp_path):
    root, manifest, model = make_cache(tmp_path)
    (model / "model.bin").write_bytes(b"tampered")
    assert validate_sensevoice_cache(root, manifest).reason_code == "model-file-mismatch"


def test_unlisted_model_file_fails_closed(tmp_path):
    root, manifest, model = make_cache(tmp_path)
    (model / "unlisted.bin").write_bytes(b"not-covered-by-manifest")
    assert validate_sensevoice_cache(root, manifest).reason_code == "model-file-mismatch"


def test_manifest_and_file_path_escape_cache_are_rejected(tmp_path):
    root, manifest, _model = make_cache(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    assert validate_sensevoice_cache(root, outside).reason_code == "model-manifest-outside-cache"

    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["path"] = "../../../outside.bin"
    outside_file = tmp_path / "outside.bin"
    outside_file.write_bytes(b"offline-model-bytes")
    data["files"][0]["size_bytes"] = outside_file.stat().st_size
    data["files"][0]["sha256"] = hashlib.sha256(outside_file.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert validate_sensevoice_cache(root, manifest).available is False


def test_unconfigured_or_missing_cache_is_unavailable(tmp_path):
    assert validate_sensevoice_cache(None, None).reason_code == "model-cache-unconfigured"
    assert validate_sensevoice_cache(tmp_path / "missing", tmp_path / "missing.json").available is False
    assert validate_faster_whisper_cache(None, None).reason_code == (
        "model-cache-unconfigured"
    )
    assert validate_qwen3_asr_cache(None, None).reason_code == (
        "model-cache-unconfigured"
    )
    assert validate_qwen3_aligner_cache(None, None).reason_code == (
        "model-cache-unconfigured"
    )
