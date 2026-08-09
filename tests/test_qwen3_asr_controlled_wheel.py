from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_controlled_qwen3_asr_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_controlled_qwen3_asr_wheel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT_SHA = "c" * 40
RUN_ID = "123456"


def _metadata(version: str, requirements: tuple[str, ...]) -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        "Name: qwen-asr",
        f"Version: {version}",
        "License: Apache-2.0",
        "License-File: LICENSE",
        *(f"Requires-Dist: {item}" for item in requirements),
        "",
        "Qwen3-ASR",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _bundle(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    root.mkdir()
    wheel = root / MODULE.OUTPUT_FILE_NAME
    dist_info = f"qwen_asr-{MODULE.PACKAGE_VERSION}.dist-info"
    files = {
        "qwen_asr/__init__.py": b"__version__ = 'controlled'\n",
        f"{dist_info}/licenses/LICENSE": b"Apache License fixture\n",
        f"{dist_info}/METADATA": _metadata(
            MODULE.PACKAGE_VERSION,
            tuple(
                item
                for item in MODULE._EXPECTED_REQUIREMENTS
                if item != MODULE.REMOVED_REQUIREMENT
            ),
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist_info}/top_level.txt": b"qwen_asr\n",
    }
    code_sha = MODULE._code_payload_sha256(files)
    license_sha = MODULE.sha256_bytes(files[f"{dist_info}/licenses/LICENSE"])
    monkeypatch.setattr(MODULE, "UPSTREAM_CODE_PAYLOAD_SHA256", code_sha)
    monkeypatch.setattr(MODULE, "UPSTREAM_LICENSE_SHA256", license_sha)
    record_name = f"{dist_info}/RECORD"
    files[record_name] = MODULE._record_payload(files, record_name)
    MODULE._write_deterministic_wheel(wheel, files)
    manifest = {
        "schema_version": MODULE.SCHEMA_VERSION,
        "package_name": MODULE.PACKAGE_NAME,
        "package_version": MODULE.PACKAGE_VERSION,
        "commit_sha": COMMIT_SHA,
        "run_id": RUN_ID,
        "source": {
            "file_name": MODULE.UPSTREAM_FILE_NAME,
            "sha256": MODULE.UPSTREAM_SHA256,
            "size_bytes": MODULE.UPSTREAM_SIZE_BYTES,
            "packagetype": "bdist_wheel",
            "url": MODULE.UPSTREAM_URL,
        },
        "patch": {
            "language": "Chinese",
            "unsupported_languages": ["Korean"],
            "removed_requires_dist": [MODULE.REMOVED_REQUIREMENT],
            "code_payload_sha256": code_sha,
            "license_sha256": license_sha,
        },
        "build": {
            "python": "3.11",
            "source_date_epoch": MODULE.SOURCE_DATE_EPOCH,
            "repetitions": MODULE.BUILD_REPETITIONS,
        },
        "wheel": {
            "file_name": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "size_bytes": wheel.stat().st_size,
            "tags": ["py3-none-any"],
        },
    }
    (root / "internal-wheel-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def test_controlled_qwen_source_and_patch_contract_are_fixed():
    assert MODULE.UPSTREAM_SHA256 == (
        "b9c55a38413298f3a990a4475467399daec6e8f4172363053fc42e2166c2dfd3"
    )
    assert MODULE.UPSTREAM_SIZE_BYTES == 141603
    assert MODULE.PACKAGE_REQUIREMENT == "qwen-asr==0.0.6+ragpincheng.zh1"
    assert MODULE.REMOVED_REQUIREMENT == "soynlp==0.0.493"
    assert MODULE.BUILD_REPETITIONS == 2


def test_metadata_patch_removes_only_korean_dependency():
    upstream = _metadata(MODULE.UPSTREAM_VERSION, MODULE._EXPECTED_REQUIREMENTS)
    controlled = MODULE._parse_metadata(MODULE._controlled_metadata(upstream))
    assert controlled["Version"] == MODULE.PACKAGE_VERSION
    assert tuple(controlled.get_all("Requires-Dist", [])) == tuple(
        item
        for item in MODULE._EXPECTED_REQUIREMENTS
        if item != MODULE.REMOVED_REQUIREMENT
    )
    assert MODULE.REMOVED_REQUIREMENT not in controlled.get_all("Requires-Dist", [])


def test_controlled_qwen_bundle_round_trips_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "bundle"
    expected = _bundle(root, monkeypatch)
    assert MODULE.validate_bundle(
        root, commit_sha=COMMIT_SHA, run_id=RUN_ID
    ) == expected
    wheel = root / MODULE.OUTPUT_FILE_NAME
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(MODULE.WheelContractError, match="payload identity"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_controlled_qwen_manifest_rejects_unknown_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "bundle"
    manifest = _bundle(root, monkeypatch)
    manifest["unexpected"] = None
    (root / "internal-wheel-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(MODULE.WheelContractError, match="fields"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)
