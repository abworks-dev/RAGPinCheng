from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_internal_crcmod_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_internal_crcmod_wheel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT_SHA = "b" * 40
RUN_ID = "987654"


def _bundle(root: Path) -> dict:
    root.mkdir()
    wheel = root / "crcmod-1.7-py3-none-any.whl"
    dist = "crcmod-1.7.dist-info"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("crcmod/__init__.py", "__version__ = '1.7'\n")
        archive.writestr(
            f"{dist}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{dist}/METADATA",
            "Metadata-Version: 2.1\nName: crcmod\nVersion: 1.7\n",
        )
        archive.writestr(f"{dist}/RECORD", "")
    manifest = {
        "schema_version": MODULE.SCHEMA_VERSION,
        "package_name": "crcmod",
        "package_version": "1.7",
        "commit_sha": COMMIT_SHA,
        "run_id": RUN_ID,
        "source": {
            "file_name": MODULE.SDIST_FILE_NAME,
            "sha256": MODULE.SDIST_SHA256,
            "size_bytes": MODULE.SDIST_SIZE_BYTES,
            "packagetype": "sdist",
        },
        "build": {
            "python": "3.11",
            "setuptools": "80.9.0",
            "wheel": "0.45.1",
            "source_date_epoch": int(MODULE.SOURCE_DATE_EPOCH),
            "repetitions": 2,
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


def test_crcmod_source_and_build_contract_are_fixed():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert MODULE.PACKAGE_REQUIREMENT == "crcmod==1.7"
    assert MODULE.SDIST_SHA256 == "dc7051a0db5f2bd48665a990d3ec1cc305a466a77358ca4492826f41f283601e"
    assert MODULE.SDIST_SIZE_BYTES == 89670
    assert MODULE.SDIST_URL == "https://files.pythonhosted.org/packages/6b/b0/e595ce2a2527e169c3bcd6c33d2473c1918e0b7f6826a043ca1245dd4e5b/crcmod-1.7.tar.gz"
    assert "BUILD_REPETITIONS = 2" in source
    assert "network access is disabled during wheel build" in source
    assert '"CC": "false"' in source
    assert "--package" not in source and "--source-url" not in source


def test_crcmod_bundle_round_trips_and_rejects_tampering(tmp_path: Path):
    root = tmp_path / "bundle"
    expected = _bundle(root)
    assert MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID) == expected
    wheel = next(root.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(MODULE.WheelContractError, match="payload identity"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_crcmod_manifest_rejects_unknown_fields(tmp_path: Path):
    root = tmp_path / "bundle"
    manifest = _bundle(root)
    manifest["unexpected"] = None
    (root / "internal-wheel-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MODULE.WheelContractError, match="fields"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)
