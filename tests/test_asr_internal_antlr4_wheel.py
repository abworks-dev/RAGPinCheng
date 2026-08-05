from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_internal_antlr4_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_internal_antlr4_python3_runtime_wheel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT_SHA = "b" * 40
RUN_ID = "987654"


def _bundle(root: Path) -> dict:
    root.mkdir()
    wheel = root / "antlr4_python3_runtime-4.9.3-py3-none-any.whl"
    dist = "antlr4_python3_runtime-4.9.3.dist-info"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("antlr4_python3_runtime/__init__.py", "__version__ = '4.9.3'\n")
        archive.writestr(
            f"{dist}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            f"{dist}/METADATA",
            "Metadata-Version: 2.1\nName: antlr4-python3-runtime\nVersion: 4.9.3\n",
        )
        archive.writestr(f"{dist}/RECORD", "")
    manifest = {
        "schema_version": MODULE.SCHEMA_VERSION,
        "package_name": "antlr4-python3-runtime",
        "package_version": "4.9.3",
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


def test_antlr4_python3_runtime_source_and_build_contract_are_fixed():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert MODULE.PACKAGE_REQUIREMENT == "antlr4-python3-runtime==4.9.3"
    assert MODULE.SDIST_SHA256 == "f224469b4168294902bb1efa80a8bf7855f24c99aef99cbefc1bcd3cce77881b"
    assert MODULE.SDIST_SIZE_BYTES == 117034
    assert MODULE.SDIST_URL == "https://files.pythonhosted.org/packages/3e/38/7859ff46355f76f8d19459005ca000b6e7012f2f1ca597746cbcd1fbfe5e/antlr4-python3-runtime-4.9.3.tar.gz"
    assert "BUILD_REPETITIONS = 2" in source
    assert "network access is disabled during wheel build" in source
    assert "--package" not in source and "--source-url" not in source


def test_antlr4_python3_runtime_bundle_round_trips_and_rejects_tampering(tmp_path: Path):
    root = tmp_path / "bundle"
    expected = _bundle(root)
    assert MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID) == expected
    wheel = next(root.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(MODULE.WheelContractError, match="payload identity"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_antlr4_python3_runtime_manifest_rejects_unknown_fields(tmp_path: Path):
    root = tmp_path / "bundle"
    manifest = _bundle(root)
    manifest["unexpected"] = None
    (root / "internal-wheel-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MODULE.WheelContractError, match="fields"):
        MODULE.validate_bundle(root, commit_sha=COMMIT_SHA, run_id=RUN_ID)
