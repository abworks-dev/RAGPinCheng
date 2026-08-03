from __future__ import annotations

import hashlib
import json

import pytest

from asr_service.model_cache import (
    MODEL_MANIFEST_VERSION,
    SENSEVOICE_MODEL_ID,
    SENSEVOICE_REVISION,
    validate_sensevoice_cache,
)


def make_cache(tmp_path, *, mutate=None):
    root = tmp_path / "models"
    model = root / "SenseVoiceSmall" / SENSEVOICE_REVISION
    model.mkdir(parents=True)
    content = b"offline-model-bytes"
    artifact = model / "model.bin"
    artifact.write_bytes(content)
    payload = {
        "schema_version": MODEL_MANIFEST_VERSION,
        "model_id": SENSEVOICE_MODEL_ID,
        "model_revision": SENSEVOICE_REVISION,
        "model_path": f"SenseVoiceSmall/{SENSEVOICE_REVISION}",
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
