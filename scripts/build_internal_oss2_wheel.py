"""Build and validate the fixed internal oss2 wheel for R3 qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


SCHEMA_VERSION = "asr-internal-wheel-manifest/1"
PACKAGE_NAME = "oss2"
PACKAGE_VERSION = "2.19.1"
PACKAGE_REQUIREMENT = f"{PACKAGE_NAME}=={PACKAGE_VERSION}"
SDIST_FILE_NAME = "oss2-2.19.1.tar.gz"
SDIST_SHA256 = "a8ab9ee7eb99e88a7e1382edc6ea641d219d585a7e074e3776e9dec9473e59c1"
SDIST_SIZE_BYTES = 298845
SDIST_URL = (
    "https://files.pythonhosted.org/packages/df/b5/"
    "f2cb1950dda46ac2284d6c950489fdacd0e743c2d79a347924d3cc44b86f/"
    "oss2-2.19.1.tar.gz"
)
SETUPTOOLS_REQUIREMENT = "setuptools==80.9.0"
WHEEL_REQUIREMENT = "wheel==0.45.1"
SOURCE_DATE_EPOCH = "1729856266"
BUILD_REPETITIONS = 2

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[0-9]{1,20}$")
_NATIVE_SUFFIXES = (".dll", ".exe", ".pyd", ".so", ".dylib")
_ROOT = f"{PACKAGE_NAME}-{PACKAGE_VERSION}"


class WheelContractError(RuntimeError):
    """Raised when the fixed source or wheel bundle violates its contract."""


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


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(
        list(arguments),
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-20:])
        raise WheelContractError(
            f"External command failed with exit code {completed.returncode}:\n{tail}"
        )


def _safe_relative_path(name: str, *, label: str) -> PurePosixPath:
    if "\\" in name:
        raise WheelContractError(f"{label} contains a backslash path")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise WheelContractError(f"{label} escapes its archive root")
    if not path.parts or path.parts[0] != _ROOT:
        raise WheelContractError(f"{label} has an unexpected top-level directory")
    return path


def validate_sdist(path: Path) -> None:
    if path.name != SDIST_FILE_NAME or not path.is_file():
        raise WheelContractError("Fixed oss2 sdist is missing")
    if sha256_file(path) != SDIST_SHA256:
        raise WheelContractError("Fixed oss2 sdist SHA-256 mismatch")
    if path.stat().st_size != SDIST_SIZE_BYTES:
        raise WheelContractError("Fixed oss2 sdist size mismatch")

    saw_setup = False
    regular_files = 0
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise WheelContractError("Fixed oss2 sdist is empty")
        for member in members:
            relative = _safe_relative_path(member.name, label="sdist member")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise WheelContractError("Fixed oss2 sdist contains a special member")
            if not (member.isfile() or member.isdir()):
                raise WheelContractError("Fixed oss2 sdist contains an unknown member")
            if member.isfile():
                regular_files += 1
                if member.size < 0:
                    raise WheelContractError("Fixed oss2 sdist contains an invalid size")
                if relative == PurePosixPath(_ROOT, "setup.py"):
                    saw_setup = member.size > 0
    if not saw_setup or regular_files == 0:
        raise WheelContractError("Fixed oss2 sdist lacks its build entrypoint")


def _extract_sdist(path: Path, destination: Path) -> Path:
    validate_sdist(path)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _safe_relative_path(member.name, label="sdist member")
        archive.extractall(destination, members=members)
    source = destination / _ROOT
    if not source.is_dir():
        raise WheelContractError("Extracted jieba source directory is missing")
    return source


def _parse_metadata(text: str) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Name", "Version"} and key not in values:
            values[key] = value.strip()
    return values.get("Name", ""), values.get("Version", "")


def inspect_wheel(path: Path) -> list[str]:
    if not path.is_file() or path.suffix.lower() != ".whl":
        raise WheelContractError("Internal wheel file is missing")

    wheel_text = ""
    metadata_text = ""
    with zipfile.ZipFile(path, mode="r") as archive:
        entries = archive.infolist()
        if not entries:
            raise WheelContractError("Internal wheel is empty")
        for entry in entries:
            if "\\" in entry.filename:
                raise WheelContractError("Internal wheel contains a backslash path")
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise WheelContractError("Internal wheel contains a path escape")
            unix_type = (entry.external_attr >> 16) & 0o170000
            if unix_type == 0o120000:
                raise WheelContractError("Internal wheel contains a symbolic link")
            if entry.filename.lower().endswith(_NATIVE_SUFFIXES):
                raise WheelContractError("Internal wheel contains a native executable")
            if entry.filename.endswith(".dist-info/WHEEL"):
                wheel_text = archive.read(entry).decode("utf-8")
            elif entry.filename.endswith(".dist-info/METADATA"):
                metadata_text = archive.read(entry).decode("utf-8")

    if "Root-Is-Purelib: true" not in wheel_text:
        raise WheelContractError("Internal wheel is not marked pure Python")
    tags = sorted(
        line.split(":", 1)[1].strip()
        for line in wheel_text.splitlines()
        if line.startswith("Tag:")
    )
    if not tags or any(not tag.endswith("-none-any") for tag in tags):
        raise WheelContractError("Internal wheel does not have a universal tag")
    name, version = _parse_metadata(metadata_text)
    if name.lower().replace("_", "-") != PACKAGE_NAME or version != PACKAGE_VERSION:
        raise WheelContractError("Internal wheel package identity mismatch")
    return tags


def _strict_object(
    value: Any,
    *,
    expected_keys: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise WheelContractError(f"{label} fields do not match the fixed schema")
    return value


def validate_bundle(
    bundle_dir: Path,
    *,
    commit_sha: str,
    run_id: str,
) -> dict[str, Any]:
    if not _COMMIT_RE.fullmatch(commit_sha):
        raise WheelContractError("commit_sha must be a lowercase full SHA")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise WheelContractError("run_id must contain only digits")
    if not bundle_dir.is_dir() or bundle_dir.is_symlink():
        raise WheelContractError("Internal wheel bundle directory is missing")

    manifest_path = bundle_dir / "internal-wheel-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise WheelContractError("Internal wheel Manifest is missing")
    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WheelContractError("Internal wheel Manifest is invalid JSON") from exc

    manifest = _strict_object(
        manifest_value,
        expected_keys={
            "schema_version",
            "package_name",
            "package_version",
            "commit_sha",
            "run_id",
            "source",
            "build",
            "wheel",
        },
        label="Manifest",
    )
    source = _strict_object(
        manifest["source"],
        expected_keys={"file_name", "sha256", "size_bytes", "packagetype"},
        label="Manifest source",
    )
    build = _strict_object(
        manifest["build"],
        expected_keys={
            "python",
            "setuptools",
            "wheel",
            "source_date_epoch",
            "repetitions",
        },
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
        raise WheelContractError("Internal wheel Manifest identity mismatch")
    if source != {
        "file_name": SDIST_FILE_NAME,
        "sha256": SDIST_SHA256,
        "size_bytes": SDIST_SIZE_BYTES,
        "packagetype": "sdist",
    }:
        raise WheelContractError("Internal wheel source identity mismatch")
    if build != {
        "python": "3.11",
        "setuptools": SETUPTOOLS_REQUIREMENT.split("==", 1)[1],
        "wheel": WHEEL_REQUIREMENT.split("==", 1)[1],
        "source_date_epoch": int(SOURCE_DATE_EPOCH),
        "repetitions": BUILD_REPETITIONS,
    }:
        raise WheelContractError("Internal wheel build identity mismatch")

    file_name = wheel.get("file_name")
    if (
        not isinstance(file_name, str)
        or Path(file_name).name != file_name
        or not file_name.endswith(".whl")
    ):
        raise WheelContractError("Internal wheel filename is invalid")
    wheel_path = bundle_dir / file_name
    if not wheel_path.is_file() or wheel_path.is_symlink():
        raise WheelContractError("Internal wheel payload is missing")
    if (
        not isinstance(wheel.get("size_bytes"), int)
        or isinstance(wheel.get("size_bytes"), bool)
        or wheel["size_bytes"] <= 0
        or wheel_path.stat().st_size != wheel["size_bytes"]
        or not isinstance(wheel.get("sha256"), str)
        or not _SHA256_RE.fullmatch(wheel["sha256"])
        or sha256_file(wheel_path) != wheel["sha256"]
    ):
        raise WheelContractError("Internal wheel payload identity mismatch")
    tags = inspect_wheel(wheel_path)
    if wheel.get("tags") != tags:
        raise WheelContractError("Internal wheel tags mismatch")

    allowed_names = {"internal-wheel-manifest.json", file_name}
    actual_names = {entry.name for entry in bundle_dir.iterdir()}
    if actual_names != allowed_names:
        raise WheelContractError("Internal wheel bundle contains unexpected files")
    return manifest


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _write_network_guard(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    (root / "sitecustomize.py").write_text(
        "import socket\n"
        "def _blocked(*args, **kwargs):\n"
        "    raise RuntimeError('network access is disabled during wheel build')\n"
        "socket.create_connection = _blocked\n"
        "_Socket = socket.socket\n"
        "class _BlockedSocket(_Socket):\n"
        "    def connect(self, *args, **kwargs):\n"
        "        return _blocked(*args, **kwargs)\n"
        "    def connect_ex(self, *args, **kwargs):\n"
        "        return _blocked(*args, **kwargs)\n"
        "socket.socket = _BlockedSocket\n",
        encoding="ascii",
        newline="\n",
    )


def _download_fixed_sdist(destination: Path) -> None:
    request = urllib.request.Request(
        SDIST_URL,
        headers={"User-Agent": "RAGPinCheng-controlled-wheel-builder/1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.geturl() != SDIST_URL:
            raise WheelContractError("Fixed oss2 sdist download redirected")
        content_length = response.headers.get("Content-Length")
        if content_length is None or int(content_length) != SDIST_SIZE_BYTES:
            raise WheelContractError("Fixed oss2 sdist response size mismatch")
        with destination.open("xb") as stream:
            remaining = SDIST_SIZE_BYTES
            while remaining:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise WheelContractError("Fixed oss2 sdist download was truncated")
                stream.write(chunk)
                remaining -= len(chunk)
            if response.read(1):
                raise WheelContractError("Fixed oss2 sdist download exceeded fixed size")
    validate_sdist(destination)


def _build_once(
    *,
    base: Path,
    sdist: Path,
    tool_wheelhouse: Path,
    repetition: int,
) -> Path:
    build_root = base / f"build-{repetition}"
    source = _extract_sdist(sdist, build_root / "source")
    venv_root = build_root / "venv"
    venv.EnvBuilder(with_pip=True, clear=False).create(venv_root)
    python = _venv_python(venv_root)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(tool_wheelhouse),
            SETUPTOOLS_REQUIREMENT,
            WHEEL_REQUIREMENT,
        ]
    )

    guard_root = build_root / "network-guard"
    _write_network_guard(guard_root)
    dist = build_root / "dist"
    dist.mkdir()
    environment = dict(os.environ)
    for name in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "NO_PROXY": "*",
            "PIP_NO_INDEX": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(guard_root),
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            "TZ": "UTC",
        }
    )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(dist),
            str(source),
        ],
        cwd=build_root,
        env=environment,
    )
    wheels = list(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise WheelContractError("Fixed oss2 build must produce exactly one wheel")
    inspect_wheel(wheels[0])
    return wheels[0]


def build_bundle(
    output_dir: Path,
    *,
    commit_sha: str,
    run_id: str,
) -> dict[str, Any]:
    if sys.version_info[:2] != (3, 11):
        raise WheelContractError("Internal wheel build requires Python 3.11")
    if not _COMMIT_RE.fullmatch(commit_sha):
        raise WheelContractError("commit_sha must be a lowercase full SHA")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise WheelContractError("run_id must contain only digits")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise WheelContractError("Internal wheel output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="oss2-wheel-build-",
        dir=output_dir.parent,
    ) as temporary:
        root = Path(temporary)
        downloads = root / "downloads"
        tools = root / "build-tools"
        downloads.mkdir()
        tools.mkdir()

        _download_fixed_sdist(downloads / SDIST_FILE_NAME)
        source_files = list(downloads.iterdir())
        if len(source_files) != 1 or source_files[0].name != SDIST_FILE_NAME:
            raise WheelContractError("PyPI did not return the fixed oss2 sdist")
        sdist = source_files[0]
        validate_sdist(sdist)

        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--no-deps",
                "--no-cache-dir",
                "--index-url",
                "https://pypi.org/simple",
                "--dest",
                str(tools),
                SETUPTOOLS_REQUIREMENT,
                WHEEL_REQUIREMENT,
            ]
        )

        built = [
            _build_once(
                base=root,
                sdist=sdist,
                tool_wheelhouse=tools,
                repetition=index,
            )
            for index in range(1, BUILD_REPETITIONS + 1)
        ]
        wheel_hashes = [sha256_file(path) for path in built]
        if len(set(wheel_hashes)) != 1:
            raise WheelContractError("Repeated oss2 wheel builds are not reproducible")
        if built[0].name != built[1].name:
            raise WheelContractError("Repeated oss2 wheel filenames differ")

        destination = output_dir / built[0].name
        shutil.copyfile(built[0], destination)
        tags = inspect_wheel(destination)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "package_name": PACKAGE_NAME,
            "package_version": PACKAGE_VERSION,
            "commit_sha": commit_sha,
            "run_id": run_id,
            "source": {
                "file_name": SDIST_FILE_NAME,
                "sha256": SDIST_SHA256,
                "size_bytes": sdist.stat().st_size,
                "packagetype": "sdist",
            },
            "build": {
                "python": "3.11",
                "setuptools": SETUPTOOLS_REQUIREMENT.split("==", 1)[1],
                "wheel": WHEEL_REQUIREMENT.split("==", 1)[1],
                "source_date_epoch": int(SOURCE_DATE_EPOCH),
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
        manifest = build_bundle(
            args.bundle_dir,
            commit_sha=commit_sha,
            run_id=args.run_id,
        )
    else:
        manifest = validate_bundle(
            args.bundle_dir,
            commit_sha=commit_sha,
            run_id=args.run_id,
        )
    print(
        json.dumps(
            {
                "schema_version": manifest["schema_version"],
                "package": PACKAGE_REQUIREMENT,
                "wheel_sha256": manifest["wheel"]["sha256"],
                "status": "verified",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
