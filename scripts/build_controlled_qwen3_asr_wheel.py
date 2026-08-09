"""Build and validate the fixed Chinese-only Qwen3-ASR wheel."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from email import policy
from email.generator import BytesGenerator
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


SCHEMA_VERSION = "qwen3-asr-controlled-wheel-manifest/1"
PACKAGE_NAME = "qwen-asr"
UPSTREAM_VERSION = "0.0.6"
PACKAGE_VERSION = "0.0.6+ragpincheng.zh1"
PACKAGE_REQUIREMENT = f"{PACKAGE_NAME}=={PACKAGE_VERSION}"
UPSTREAM_FILE_NAME = "qwen_asr-0.0.6-py3-none-any.whl"
OUTPUT_FILE_NAME = "qwen_asr-0.0.6+ragpincheng.zh1-py3-none-any.whl"
UPSTREAM_SHA256 = "b9c55a38413298f3a990a4475467399daec6e8f4172363053fc42e2166c2dfd3"
UPSTREAM_SIZE_BYTES = 141603
UPSTREAM_URL = (
    "https://files.pythonhosted.org/packages/01/12/"
    "d3027a7e4dc2eea0b12a4bf8414a7109f055004e1771666e01d8859d3ca0/"
    "qwen_asr-0.0.6-py3-none-any.whl"
)
UPSTREAM_CODE_PAYLOAD_SHA256 = (
    "9565c7e2204ac3b06745d03a845b12e05166b954a84309ae70774ec984edeba6"
)
UPSTREAM_LICENSE_SHA256 = (
    "a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655"
)
REMOVED_REQUIREMENT = "soynlp==0.0.493"
SOURCE_DATE_EPOCH = 1577836800
BUILD_REPETITIONS = 2

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[0-9]{1,20}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NATIVE_SUFFIXES = (".dll", ".exe", ".pyd", ".so", ".dylib")
_UPSTREAM_DIST_INFO = f"qwen_asr-{UPSTREAM_VERSION}.dist-info"
_OUTPUT_DIST_INFO = f"qwen_asr-{PACKAGE_VERSION}.dist-info"
_EXPECTED_REQUIREMENTS = (
    "transformers==4.57.6",
    "nagisa==0.2.11",
    "soynlp==0.0.493",
    "accelerate==1.12.0",
    "qwen-omni-utils",
    "librosa",
    "soundfile",
    "sox",
    "gradio",
    "flask",
    "pytz",
    'vllm==0.14.0; extra == "vllm"',
)


class WheelContractError(RuntimeError):
    """Raised when the fixed source or controlled wheel violates its contract."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _safe_wheel_path(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise WheelContractError("Wheel contains an invalid path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise WheelContractError("Wheel contains a path escape")
    return path


def _code_payload_sha256(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    code_names = sorted(name for name in files if name.startswith("qwen_asr/"))
    if not code_names:
        raise WheelContractError("Qwen wheel contains no package code")
    for name in code_names:
        payload = files[name]
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
    return digest.hexdigest()


def _read_wheel(path: Path) -> dict[str, bytes]:
    if not path.is_file() or path.suffix.lower() != ".whl":
        raise WheelContractError("Wheel file is missing")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            for entry in archive.infolist():
                _safe_wheel_path(entry.filename)
                if entry.filename in files:
                    raise WheelContractError("Wheel contains a duplicate path")
                unix_type = (entry.external_attr >> 16) & 0o170000
                if unix_type == 0o120000:
                    raise WheelContractError("Wheel contains a symbolic link")
                if entry.is_dir():
                    continue
                if entry.filename.lower().endswith(_NATIVE_SUFFIXES):
                    raise WheelContractError("Wheel contains a native executable")
                files[entry.filename] = archive.read(entry)
    except zipfile.BadZipFile as exc:
        raise WheelContractError("Wheel is not a valid ZIP archive") from exc
    if not files:
        raise WheelContractError("Wheel is empty")
    return files


def _parse_metadata(value: bytes):
    try:
        return BytesParser(policy=policy.compat32).parsebytes(value)
    except Exception as exc:
        raise WheelContractError("Wheel METADATA is invalid") from exc


def _assert_metadata(message, *, version: str, requirements: tuple[str, ...]) -> None:
    if message.get("Name", "").lower().replace("_", "-") != PACKAGE_NAME:
        raise WheelContractError("Wheel package name mismatch")
    if message.get("Version") != version:
        raise WheelContractError("Wheel package version mismatch")
    if message.get("License") != "Apache-2.0":
        raise WheelContractError("Qwen wheel license declaration changed")
    if message.get_all("License-File", []) != ["LICENSE"]:
        raise WheelContractError("Qwen wheel license file declaration changed")
    if tuple(message.get_all("Requires-Dist", [])) != requirements:
        raise WheelContractError("Qwen wheel dependency metadata changed")


def _assert_wheel_tags(files: dict[str, bytes], *, dist_info: str) -> list[str]:
    wheel_name = f"{dist_info}/WHEEL"
    if wheel_name not in files:
        raise WheelContractError("Wheel tag metadata is missing")
    wheel_text = files[wheel_name].decode("utf-8")
    if "Root-Is-Purelib: true" not in wheel_text:
        raise WheelContractError("Qwen wheel is not pure Python")
    tags = sorted(
        line.split(":", 1)[1].strip()
        for line in wheel_text.splitlines()
        if line.startswith("Tag:")
    )
    if tags != ["py3-none-any"]:
        raise WheelContractError("Qwen wheel tags changed")
    return tags


def validate_upstream_wheel(path: Path) -> dict[str, bytes]:
    if path.name != UPSTREAM_FILE_NAME:
        raise WheelContractError("Fixed upstream Qwen wheel filename mismatch")
    if path.stat().st_size != UPSTREAM_SIZE_BYTES or sha256_file(path) != UPSTREAM_SHA256:
        raise WheelContractError("Fixed upstream Qwen wheel identity mismatch")
    files = _read_wheel(path)
    metadata_name = f"{_UPSTREAM_DIST_INFO}/METADATA"
    license_name = f"{_UPSTREAM_DIST_INFO}/licenses/LICENSE"
    if metadata_name not in files or license_name not in files:
        raise WheelContractError("Fixed upstream Qwen metadata or LICENSE is missing")
    _assert_metadata(
        _parse_metadata(files[metadata_name]),
        version=UPSTREAM_VERSION,
        requirements=_EXPECTED_REQUIREMENTS,
    )
    _assert_wheel_tags(files, dist_info=_UPSTREAM_DIST_INFO)
    if _code_payload_sha256(files) != UPSTREAM_CODE_PAYLOAD_SHA256:
        raise WheelContractError("Fixed upstream Qwen code payload changed")
    if sha256_bytes(files[license_name]) != UPSTREAM_LICENSE_SHA256:
        raise WheelContractError("Fixed upstream Qwen LICENSE changed")
    return files


def _controlled_metadata(upstream: bytes) -> bytes:
    message = _parse_metadata(upstream)
    _assert_metadata(
        message,
        version=UPSTREAM_VERSION,
        requirements=_EXPECTED_REQUIREMENTS,
    )
    message.replace_header("Version", PACKAGE_VERSION)
    del message["Requires-Dist"]
    for requirement in _EXPECTED_REQUIREMENTS:
        if requirement != REMOVED_REQUIREMENT:
            message["Requires-Dist"] = requirement
    output = io.BytesIO()
    BytesGenerator(
        output,
        policy=policy.compat32.clone(linesep="\n", max_line_length=0),
    ).flatten(message, unixfrom=False)
    controlled = output.getvalue()
    _assert_metadata(
        _parse_metadata(controlled),
        version=PACKAGE_VERSION,
        requirements=tuple(
            item for item in _EXPECTED_REQUIREMENTS if item != REMOVED_REQUIREMENT
        ),
    )
    return controlled


def _record_payload(files: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(files):
        payload = files[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={encoded.decode('ascii')}", len(payload)))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")


def _write_deterministic_wheel(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(
        path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def rewrite_wheel(upstream_path: Path, destination: Path) -> None:
    upstream = validate_upstream_wheel(upstream_path)
    controlled: dict[str, bytes] = {}
    for name, payload in upstream.items():
        if name == f"{_UPSTREAM_DIST_INFO}/RECORD":
            continue
        output_name = name
        if name.startswith(f"{_UPSTREAM_DIST_INFO}/"):
            output_name = f"{_OUTPUT_DIST_INFO}/{name.split('/', 1)[1]}"
        if name == f"{_UPSTREAM_DIST_INFO}/METADATA":
            payload = _controlled_metadata(payload)
        controlled[output_name] = payload
    record_name = f"{_OUTPUT_DIST_INFO}/RECORD"
    controlled[record_name] = _record_payload(controlled, record_name)
    _write_deterministic_wheel(destination, controlled)
    inspect_controlled_wheel(destination)


def _validate_record(files: dict[str, bytes], record_name: str) -> None:
    try:
        rows = list(csv.reader(io.StringIO(files[record_name].decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise WheelContractError("Controlled wheel RECORD is invalid") from exc
    expected_names = set(files)
    actual_names = {row[0] for row in rows if len(row) == 3}
    if actual_names != expected_names or len(rows) != len(files):
        raise WheelContractError("Controlled wheel RECORD file set mismatch")
    for name, digest, size in rows:
        if name == record_name:
            if digest or size:
                raise WheelContractError("Controlled wheel RECORD self-entry is invalid")
            continue
        payload = files[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        if digest != f"sha256={encoded.decode('ascii')}" or size != str(len(payload)):
            raise WheelContractError("Controlled wheel RECORD integrity mismatch")


def inspect_controlled_wheel(path: Path) -> list[str]:
    if path.name != OUTPUT_FILE_NAME:
        raise WheelContractError("Controlled Qwen wheel filename mismatch")
    files = _read_wheel(path)
    metadata_name = f"{_OUTPUT_DIST_INFO}/METADATA"
    license_name = f"{_OUTPUT_DIST_INFO}/licenses/LICENSE"
    record_name = f"{_OUTPUT_DIST_INFO}/RECORD"
    if metadata_name not in files or license_name not in files or record_name not in files:
        raise WheelContractError("Controlled Qwen metadata, LICENSE, or RECORD is missing")
    _assert_metadata(
        _parse_metadata(files[metadata_name]),
        version=PACKAGE_VERSION,
        requirements=tuple(
            item for item in _EXPECTED_REQUIREMENTS if item != REMOVED_REQUIREMENT
        ),
    )
    tags = _assert_wheel_tags(files, dist_info=_OUTPUT_DIST_INFO)
    if _code_payload_sha256(files) != UPSTREAM_CODE_PAYLOAD_SHA256:
        raise WheelContractError("Controlled Qwen code differs from upstream")
    if sha256_bytes(files[license_name]) != UPSTREAM_LICENSE_SHA256:
        raise WheelContractError("Controlled Qwen LICENSE differs from upstream")
    _validate_record(files, record_name)
    return tags


def _strict_object(value: Any, *, expected_keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise WheelContractError(f"{label} fields do not match the fixed schema")
    return value


def validate_bundle(bundle_dir: Path, *, commit_sha: str, run_id: str) -> dict[str, Any]:
    if not _COMMIT_RE.fullmatch(commit_sha):
        raise WheelContractError("commit_sha must be a lowercase full SHA")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise WheelContractError("run_id must contain only digits")
    if not bundle_dir.is_dir() or bundle_dir.is_symlink():
        raise WheelContractError("Controlled Qwen wheel bundle is missing")
    manifest_path = bundle_dir / "internal-wheel-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise WheelContractError("Controlled Qwen wheel Manifest is missing")
    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WheelContractError("Controlled Qwen wheel Manifest is invalid JSON") from exc
    manifest = _strict_object(
        manifest_value,
        expected_keys={
            "schema_version", "package_name", "package_version", "commit_sha",
            "run_id", "source", "patch", "build", "wheel",
        },
        label="Manifest",
    )
    source = _strict_object(
        manifest["source"],
        expected_keys={"file_name", "sha256", "size_bytes", "packagetype", "url"},
        label="Manifest source",
    )
    patch = _strict_object(
        manifest["patch"],
        expected_keys={
            "language", "unsupported_languages", "removed_requires_dist",
            "code_payload_sha256", "license_sha256",
        },
        label="Manifest patch",
    )
    build = _strict_object(
        manifest["build"],
        expected_keys={"python", "source_date_epoch", "repetitions"},
        label="Manifest build",
    )
    wheel = _strict_object(
        manifest["wheel"],
        expected_keys={"file_name", "sha256", "size_bytes", "tags"},
        label="Manifest wheel",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["package_name"] != PACKAGE_NAME
        or manifest["package_version"] != PACKAGE_VERSION
        or manifest["commit_sha"] != commit_sha
        or manifest["run_id"] != run_id
    ):
        raise WheelContractError("Controlled Qwen wheel Manifest identity mismatch")
    if source != {
        "file_name": UPSTREAM_FILE_NAME,
        "sha256": UPSTREAM_SHA256,
        "size_bytes": UPSTREAM_SIZE_BYTES,
        "packagetype": "bdist_wheel",
        "url": UPSTREAM_URL,
    }:
        raise WheelContractError("Controlled Qwen source identity mismatch")
    if patch != {
        "language": "Chinese",
        "unsupported_languages": ["Korean"],
        "removed_requires_dist": [REMOVED_REQUIREMENT],
        "code_payload_sha256": UPSTREAM_CODE_PAYLOAD_SHA256,
        "license_sha256": UPSTREAM_LICENSE_SHA256,
    }:
        raise WheelContractError("Controlled Qwen metadata patch identity mismatch")
    if build != {
        "python": "3.11",
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "repetitions": BUILD_REPETITIONS,
    }:
        raise WheelContractError("Controlled Qwen build identity mismatch")
    if wheel.get("file_name") != OUTPUT_FILE_NAME:
        raise WheelContractError("Controlled Qwen wheel payload name mismatch")
    wheel_path = bundle_dir / OUTPUT_FILE_NAME
    if (
        not wheel_path.is_file()
        or wheel_path.is_symlink()
        or not isinstance(wheel.get("sha256"), str)
        or not _SHA256_RE.fullmatch(wheel["sha256"])
        or sha256_file(wheel_path) != wheel["sha256"]
        or not isinstance(wheel.get("size_bytes"), int)
        or isinstance(wheel.get("size_bytes"), bool)
        or wheel_path.stat().st_size != wheel["size_bytes"]
    ):
        raise WheelContractError("Controlled Qwen wheel payload identity mismatch")
    tags = inspect_controlled_wheel(wheel_path)
    if wheel.get("tags") != tags:
        raise WheelContractError("Controlled Qwen wheel tags mismatch")
    if {item.name for item in bundle_dir.iterdir()} != {
        "internal-wheel-manifest.json", OUTPUT_FILE_NAME
    }:
        raise WheelContractError("Controlled Qwen wheel bundle contains unexpected files")
    return manifest


def _download_fixed_wheel(destination: Path) -> None:
    request = urllib.request.Request(
        UPSTREAM_URL,
        headers={"User-Agent": "RAGPinCheng-controlled-qwen-wheel-builder/1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.geturl() != UPSTREAM_URL:
            raise WheelContractError("Fixed upstream Qwen wheel download redirected")
        content_length = response.headers.get("Content-Length")
        if content_length is None or int(content_length) != UPSTREAM_SIZE_BYTES:
            raise WheelContractError("Fixed upstream Qwen wheel response size mismatch")
        with destination.open("xb") as stream:
            remaining = UPSTREAM_SIZE_BYTES
            while remaining:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise WheelContractError("Fixed upstream Qwen wheel download was truncated")
                stream.write(chunk)
                remaining -= len(chunk)
            if response.read(1):
                raise WheelContractError("Fixed upstream Qwen wheel download exceeded fixed size")
    validate_upstream_wheel(destination)


def build_bundle(output_dir: Path, *, commit_sha: str, run_id: str) -> dict[str, Any]:
    if sys.version_info[:2] != (3, 11):
        raise WheelContractError("Controlled Qwen wheel build requires Python 3.11")
    if not _COMMIT_RE.fullmatch(commit_sha):
        raise WheelContractError("commit_sha must be a lowercase full SHA")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise WheelContractError("run_id must contain only digits")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise WheelContractError("Controlled Qwen wheel output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qwen-wheel-build-", dir=output_dir.parent) as temporary:
        root = Path(temporary)
        upstream = root / UPSTREAM_FILE_NAME
        _download_fixed_wheel(upstream)
        built: list[Path] = []
        for repetition in range(1, BUILD_REPETITIONS + 1):
            destination = root / f"build-{repetition}" / OUTPUT_FILE_NAME
            destination.parent.mkdir()
            rewrite_wheel(upstream, destination)
            built.append(destination)
        hashes = [sha256_file(path) for path in built]
        if len(set(hashes)) != 1:
            raise WheelContractError("Repeated controlled Qwen wheel builds differ")
        destination = output_dir / OUTPUT_FILE_NAME
        shutil.copyfile(built[0], destination)
        tags = inspect_controlled_wheel(destination)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "package_name": PACKAGE_NAME,
            "package_version": PACKAGE_VERSION,
            "commit_sha": commit_sha,
            "run_id": run_id,
            "source": {
                "file_name": UPSTREAM_FILE_NAME,
                "sha256": UPSTREAM_SHA256,
                "size_bytes": UPSTREAM_SIZE_BYTES,
                "packagetype": "bdist_wheel",
                "url": UPSTREAM_URL,
            },
            "patch": {
                "language": "Chinese",
                "unsupported_languages": ["Korean"],
                "removed_requires_dist": [REMOVED_REQUIREMENT],
                "code_payload_sha256": UPSTREAM_CODE_PAYLOAD_SHA256,
                "license_sha256": UPSTREAM_LICENSE_SHA256,
            },
            "build": {
                "python": "3.11",
                "source_date_epoch": SOURCE_DATE_EPOCH,
                "repetitions": BUILD_REPETITIONS,
            },
            "wheel": {
                "file_name": destination.name,
                "sha256": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
                "tags": tags,
            },
        }
        _write_json(output_dir / "internal-wheel-manifest.json", manifest)
    return validate_bundle(output_dir, commit_sha=commit_sha, run_id=run_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "validate"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--bundle-dir", type=Path, required=True)
        subparser.add_argument("--commit-sha", required=True)
        subparser.add_argument("--run-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commit_sha = args.commit_sha.lower()
    if args.command == "build":
        manifest = build_bundle(args.bundle_dir, commit_sha=commit_sha, run_id=args.run_id)
    else:
        manifest = validate_bundle(args.bundle_dir, commit_sha=commit_sha, run_id=args.run_id)
    print(json.dumps({
        "schema_version": manifest["schema_version"],
        "package": PACKAGE_REQUIREMENT,
        "wheel_sha256": manifest["wheel"]["sha256"],
        "status": "verified",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
