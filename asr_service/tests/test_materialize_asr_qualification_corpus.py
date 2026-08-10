from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from scripts import asr_qualification_manifest as contract
from scripts import materialize_asr_qualification_corpus as materializer


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00" * 32_000)


def _legacy_manifest(root: Path) -> Path:
    root.mkdir(parents=True)
    shared = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "asr-qualification-manifest.example.json"
        ).read_text(encoding="utf-8")
    )
    samples = []
    for item in shared["samples"]:
        target = root / item["path"]
        _wav(target)
        samples.append(
            {
                key: value
                for key, value in item.items()
                if key != "size_bytes"
            }
        )
        samples[-1].update(
            {
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "duration_ms": 1000,
                "self_made": True,
                "is_internal_recording": False,
                "contains_customer_data": False,
                "negative_control": item["scenario"] == "negative-control",
            }
        )
    payload = {
        "schema_version": contract.FASTER_WHISPER_LEGACY_SCHEMA_VERSION,
        "sample_set_id": materializer.APPROVED_SAMPLE_SET_ID,
        "annotation_version": "1",
        "samples": samples,
    }
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.name: (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.iterdir()
        if path.is_file()
    }


def test_materializes_shared_schema_without_modifying_source(tmp_path):
    source_root = tmp_path / "source"
    source_manifest = _legacy_manifest(source_root)
    target = (
        tmp_path
        / "qualification"
        / "shared-corpus"
        / materializer.APPROVED_SAMPLE_SET_ID
    )
    before = _snapshot(source_root)

    result = materializer.materialize(source_root, source_manifest, target, "123")

    assert result["status"] == "materialized"
    assert result["sample_set_id"] == materializer.APPROVED_SAMPLE_SET_ID
    assert result["sample_count"] == 8
    assert _snapshot(source_root) == before
    shared = contract.load_manifest(
        target / "manifest.json",
        root=target,
        allowed_schema_versions=contract.allowed_schema_versions(
            "neutral", "faster-whisper"
        ),
        manifest_source="neutral",
    )
    assert shared.schema_version == contract.SHARED_SCHEMA_VERSION
    assert shared.identity() == {
        key: result[key]
        for key in (
            "manifest_sha256",
            "sample_set_id",
            "annotation_version",
            "sample_count",
            "samples",
        )
    }


def test_existing_identical_target_is_idempotent(tmp_path):
    source_root = tmp_path / "source"
    source_manifest = _legacy_manifest(source_root)
    target = (
        tmp_path
        / "qualification"
        / "shared-corpus"
        / materializer.APPROVED_SAMPLE_SET_ID
    )
    materializer.materialize(source_root, source_manifest, target, "123")
    before = _snapshot(target)

    result = materializer.materialize(source_root, source_manifest, target, "124")

    assert result["status"] == "existing"
    assert _snapshot(target) == before


def test_rejects_conflicting_existing_target(tmp_path):
    source_root = tmp_path / "source"
    source_manifest = _legacy_manifest(source_root)
    target = (
        tmp_path
        / "qualification"
        / "shared-corpus"
        / materializer.APPROVED_SAMPLE_SET_ID
    )
    materializer.materialize(source_root, source_manifest, target, "123")
    payload = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    payload["annotation_version"] = "2"
    (target / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        materializer.materialize(source_root, source_manifest, target, "124")


def test_rejects_unapproved_source_identity(tmp_path):
    source_root = tmp_path / "source"
    source_manifest = _legacy_manifest(source_root)
    payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    payload["sample_set_id"] = "other-sample-set"
    source_manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "qualification" / "shared-corpus" / "other-sample-set"

    with pytest.raises(ValueError, match="approved faster-whisper"):
        materializer.materialize(source_root, source_manifest, target, "123")


def test_rejects_target_outside_fixed_location(tmp_path):
    source_root = tmp_path / "source"
    source_manifest = _legacy_manifest(source_root)
    target = tmp_path / materializer.APPROVED_SAMPLE_SET_ID

    with pytest.raises(ValueError, match="fixed shared corpus location"):
        materializer.materialize(source_root, source_manifest, target, "123")
