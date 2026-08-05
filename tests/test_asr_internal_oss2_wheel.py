from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_internal_oss2_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_internal_oss2_wheel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT_SHA = "b" * 40
RUN_ID = "987654"


def _bundle(root: Path) -> dict:
    root.mkdir()
    wheel = root / "oss2-2.19.1-py3-none-any.whl"
    dist = "oss2-2.19.1.dist-info"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("oss2/__init__.py", "__version__ = '2.19.1'\n")
        archive.writestr(
            f"{dist}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{dist}/METADATA",
            "Metadata-Version: 2.1\nName: oss2\nVersion: 2.19.1\n",
        )
        archive.writestr(f"{dist}/RECORD", "")
    manifest = {
        "schema_version": MODULE.SCHEMA_VERSION,
        "package_name": "oss2",
        "package_version": "2.19.1",
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


def test_oss2_source_and_build_contract_are_fixed():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert MODULE.PACKAGE_REQUIREMENT == "oss2==2.19.1"
    assert MODULE.SDIST_SHA256 == "a8ab9ee7eb99e88a7e1382edc6ea641d219d585a7e074e3776e9dec9473e59c1"
    assert MODULE.SDIST_SIZE_BYTES == 298845
    assert MODULE.SDIST_URL == "https://files.pythonhosted.org/packages/df/b5/f2cb1950dda46ac2284d6c950489fdacd0e743c2d79a347924d3cc44b86f/oss2-2.19.1.tar.gz"
    assert "BUILD_REPETITIONS = 2" in source
    assert "network access is disabled during wheel build" in source
    assert "--package" not in source and "--source-url" not in source


def test_oss2_bundle_round_trips_and_rejects_tampering(tmp_path: Path):
    root = tmp_path / "bundle"
    expected = _bundle(root)
    assert MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID) == expected
    wheel = next(root.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(MODULE.WheelContractError, match="payload identity"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_oss2_manifest_rejects_unknown_fields(tmp_path: Path):
    root = tmp_path / "bundle"
    manifest = _bundle(root)
    manifest["unexpected"] = None
    (root / "internal-wheel-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MODULE.WheelContractError, match="fields"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)
