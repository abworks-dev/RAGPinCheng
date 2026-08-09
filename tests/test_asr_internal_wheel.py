from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_internal_jieba_wheel.py"
SPEC = importlib.util.spec_from_file_location("build_internal_jieba_wheel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COMMIT_SHA = "a" * 40
RUN_ID = "123456"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wheel(
    path: Path,
    *,
    extra_name: str | None = None,
    symlink_name: str | None = None,
) -> None:
    dist_info = "jieba-0.42.1.dist-info"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("jieba/__init__.py", "__version__ = '0.42.1'\n")
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: controlled-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        archive.writestr(
            f"{dist_info}/METADATA",
            "Metadata-Version: 2.1\nName: jieba\nVersion: 0.42.1\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
        if extra_name:
            archive.writestr(extra_name, b"unsafe")
        if symlink_name:
            entry = zipfile.ZipInfo(symlink_name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "jieba/__init__.py")


def _write_bundle(
    root: Path,
    *,
    extra_wheel_name: str | None = None,
    symlink_name: str | None = None,
) -> dict:
    root.mkdir()
    wheel = root / "jieba-0.42.1-py3-none-any.whl"
    _write_wheel(
        wheel,
        extra_name=extra_wheel_name,
        symlink_name=symlink_name,
    )
    manifest = {
        "schema_version": MODULE.SCHEMA_VERSION,
        "package_name": MODULE.PACKAGE_NAME,
        "package_version": MODULE.PACKAGE_VERSION,
        "commit_sha": COMMIT_SHA,
        "run_id": RUN_ID,
        "source": {
            "file_name": MODULE.SDIST_FILE_NAME,
            "sha256": MODULE.SDIST_SHA256,
            "size_bytes": 19214172,
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
            "sha256": _sha256(wheel),
            "size_bytes": wheel.stat().st_size,
            "tags": ["py3-none-any"],
        },
    }
    (root / "internal-wheel-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return manifest


def test_fixed_contract_has_no_free_package_or_source_inputs():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert MODULE.PACKAGE_REQUIREMENT == "jieba==0.42.1"
    assert (
        MODULE.SDIST_SHA256
        == "055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2"
    )
    assert MODULE.SETUPTOOLS_REQUIREMENT == "setuptools==80.9.0"
    assert MODULE.WHEEL_REQUIREMENT == "wheel==0.45.1"
    assert MODULE.SDIST_SIZE_BYTES == 19214172
    assert 'PRELOADED_SDIST_PATH = Path(os.environ.get("PRODUCTION_JIEBA_SDIST_PATH", ""))' in source
    assert source.count('subparser.add_argument("--bundle-dir"') == 1
    assert "--package" not in source
    assert "--version" not in source
    assert "--source-url" not in source
    assert "BUILD_REPETITIONS = 2" in source
    assert "network access is disabled during wheel build" in source
    assert "_copy_preloaded_sdist(downloads / SDIST_FILE_NAME)" in source
    assert '"--no-binary=:all:"' not in source


def test_preloaded_sdist_is_copied_only_after_source_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / MODULE.SDIST_FILE_NAME
    destination = tmp_path / "run" / MODULE.SDIST_FILE_NAME
    source.write_bytes(b"validated fixed source")
    calls: list[Path] = []

    monkeypatch.setattr(MODULE, "PRELOADED_SDIST_PATH", source)
    monkeypatch.setattr(MODULE, "validate_sdist", lambda path: calls.append(path))

    destination.parent.mkdir()
    MODULE._copy_preloaded_sdist(destination)

    assert destination.read_bytes() == source.read_bytes()
    assert calls == [source, destination]


def test_preloaded_sdist_must_exist_at_fixed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "missing" / MODULE.SDIST_FILE_NAME
    destination = tmp_path / "run" / MODULE.SDIST_FILE_NAME
    destination.parent.mkdir()
    monkeypatch.setattr(MODULE, "PRELOADED_SDIST_PATH", source)

    with pytest.raises(MODULE.WheelContractError, match="missing at"):
        MODULE._copy_preloaded_sdist(destination)

    assert not destination.exists()


def test_valid_bundle_round_trips_with_strict_manifest(tmp_path: Path):
    bundle = tmp_path / "bundle"
    expected = _write_bundle(bundle)

    actual = MODULE.validate_bundle(
        bundle,
        commit_sha=COMMIT_SHA,
        run_id=RUN_ID,
    )

    assert actual == expected


@pytest.mark.parametrize(
    ("boundary", "field"),
    (
        ("root", "extra"),
        ("source", "extra"),
        ("build", "extra"),
        ("wheel", "extra"),
    ),
)
def test_manifest_rejects_unknown_fields(
    tmp_path: Path,
    boundary: str,
    field: str,
):
    bundle = tmp_path / "bundle"
    manifest = _write_bundle(bundle)
    target = manifest if boundary == "root" else manifest[boundary]
    target[field] = None
    (bundle / "internal-wheel-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(MODULE.WheelContractError, match="fields"):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_manifest_rejects_tampered_wheel_and_extra_bundle_file(tmp_path: Path):
    bundle = tmp_path / "bundle"
    _write_bundle(bundle)
    wheel = next(bundle.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    with pytest.raises(MODULE.WheelContractError, match="payload identity"):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id=RUN_ID)

    bundle = tmp_path / "bundle-extra"
    _write_bundle(bundle)
    (bundle / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(MODULE.WheelContractError, match="unexpected files"):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id=RUN_ID)


@pytest.mark.parametrize(
    ("extra_name", "symlink_name", "message"),
    (
        ("jieba/native.pyd", None, "native executable"),
        ("../escape.py", None, "path escape"),
        (None, "jieba/link.py", "symbolic link"),
    ),
)
def test_wheel_rejects_native_path_escape_and_symlink(
    tmp_path: Path,
    extra_name: str | None,
    symlink_name: str | None,
    message: str,
):
    bundle = tmp_path / "bundle"
    _write_bundle(
        bundle,
        extra_wheel_name=extra_name,
        symlink_name=symlink_name,
    )

    with pytest.raises(MODULE.WheelContractError, match=message):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id=RUN_ID)


def test_bundle_rejects_identity_and_manifest_encoding_errors(tmp_path: Path):
    bundle = tmp_path / "bundle"
    _write_bundle(bundle)

    with pytest.raises(MODULE.WheelContractError, match="commit_sha"):
        MODULE.validate_bundle(bundle, commit_sha="ABC", run_id=RUN_ID)
    with pytest.raises(MODULE.WheelContractError, match="run_id"):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id="run-1")

    (bundle / "internal-wheel-manifest.json").write_bytes(b"\xff")
    with pytest.raises(MODULE.WheelContractError, match="invalid JSON"):
        MODULE.validate_bundle(bundle, commit_sha=COMMIT_SHA, run_id=RUN_ID)
