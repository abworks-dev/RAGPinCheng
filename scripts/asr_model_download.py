"""Pinned Hugging Face download helpers shared by ASR qualification tools."""
from __future__ import annotations

import fnmatch
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit


HF_ORIGIN_IP_ENV = "ASR_LOCAL_HUGGING_FACE_ORIGIN_IP"


class AsrModelDownloadError(RuntimeError):
    """Raised when a pinned model download violates its security contract."""


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def assert_no_reparse_components(path: Path, label: str) -> None:
    logical = Path(os.path.abspath(path))
    current = Path(logical.anchor)
    for part in logical.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        if _is_reparse_point(current):
            raise AsrModelDownloadError(
                f"{label} contains a reparse point: {current}"
            )


@contextmanager
def exclusive_staging_lock(staging_root: Path):
    """Hold a process-scoped, non-blocking lock for one model staging root."""
    assert_no_reparse_components(staging_root, "model staging root")
    staging_root.mkdir(parents=True, exist_ok=True)
    assert_no_reparse_components(staging_root, "model staging root")
    lock_path = staging_root / ".prepare.lock"
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AsrModelDownloadError("model staging is already in use") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def hugging_face_origin_override():
    value = os.environ.get(HF_ORIGIN_IP_ENV, "").strip()
    if not value:
        yield
        return
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise AsrModelDownloadError("invalid local Hugging Face origin IP") from exc
    if not address.is_global:
        raise AsrModelDownloadError("local Hugging Face origin IP must be public")
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        target = value if str(host).lower() == "huggingface.co" else host
        return original(target, *args, **kwargs)

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original


def _local_hf_origin_ip() -> str:
    value = os.environ.get(HF_ORIGIN_IP_ENV, "").strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise AsrModelDownloadError("invalid local Hugging Face origin IP") from exc
    if not address.is_global or address.version != 4:
        raise AsrModelDownloadError(
            "local Hugging Face origin IP must be public IPv4"
        )
    return value


def _assert_approved_hf_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not (
            host == "huggingface.co"
            or host.endswith(".huggingface.co")
            or host == "hf.co"
            or host.endswith(".hf.co")
        )
    ):
        raise AsrModelDownloadError(
            "local model download escaped approved Hugging Face HTTPS hosts"
        )


def _safe_snapshot_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AsrModelDownloadError("unsafe Hugging Face snapshot path")
    return path


def _curl_base(origin_ip: str) -> list[str]:
    return [
        "curl.exe",
        "--ssl-revoke-best-effort",
        "--resolve",
        f"huggingface.co:443:{origin_ip}",
        "--location",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--fail",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "20",
        "--retry",
        "5",
        "--retry-all-errors",
        "--retry-delay",
        "3",
    ]


def curl_snapshot_download(**kwargs: object) -> str:
    allowed = {
        "repo_id",
        "revision",
        "local_dir",
        "local_dir_use_symlinks",
        "allow_patterns",
        "max_workers",
    }
    if set(kwargs) - allowed:
        raise AsrModelDownloadError("unsupported local snapshot download option")
    repo_id = str(kwargs["repo_id"])
    revision = str(kwargs["revision"])
    logical_local_dir = Path(os.path.abspath(str(kwargs["local_dir"])))
    assert_no_reparse_components(logical_local_dir, "local model snapshot")
    local_dir = logical_local_dir.resolve()
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo_id):
        raise AsrModelDownloadError("invalid Hugging Face repository id")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise AsrModelDownloadError("local model revision must be a commit SHA")
    if kwargs.get("local_dir_use_symlinks") not in {None, False}:
        raise AsrModelDownloadError("local model snapshots must not use symlinks")
    patterns = tuple(str(item) for item in (kwargs.get("allow_patterns") or ()))
    origin_ip = _local_hf_origin_ip()
    api_url = (
        "https://huggingface.co/api/models/"
        f"{quote(repo_id, safe='/')}/revision/{revision}"
    )
    metadata = subprocess.run(
        _curl_base(origin_ip)
        + ["--max-time", "60", "--write-out", "\n__URL__%{url_effective}", api_url],
        check=True,
        capture_output=True,
        text=True,
    )
    payload_text, final_url = metadata.stdout.rsplit("\n__URL__", 1)
    _assert_approved_hf_url(final_url.strip())
    payload = json.loads(payload_text)
    siblings = payload.get("siblings")
    if not isinstance(siblings, list) or not siblings:
        raise AsrModelDownloadError("Hugging Face snapshot metadata has no files")
    files = []
    for sibling in siblings:
        if not isinstance(sibling, dict) or not isinstance(
            sibling.get("rfilename"), str
        ):
            raise AsrModelDownloadError("invalid Hugging Face snapshot metadata")
        path = _safe_snapshot_path(sibling["rfilename"])
        if not patterns or any(
            fnmatch.fnmatchcase(path.as_posix(), item) for item in patterns
        ):
            files.append(path)
    if not files:
        raise AsrModelDownloadError("Hugging Face snapshot selection is empty")
    local_dir.mkdir(parents=True, exist_ok=True)
    assert_no_reparse_components(local_dir, "local model snapshot")
    for relative in files:
        destination = local_dir.joinpath(*relative.parts)
        if destination.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        file_url = (
            f"https://huggingface.co/{quote(repo_id, safe='/')}/resolve/"
            f"{revision}/{quote(relative.as_posix(), safe='/')}?download=true"
        )
        completed = subprocess.run(
            _curl_base(origin_ip)
            + [
                "--max-time",
                "7200",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                "--write-out",
                "%{url_effective}",
                file_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        _assert_approved_hf_url(completed.stdout.strip())
        if not partial.is_file():
            raise AsrModelDownloadError("local model download produced no file")
        os.replace(partial, destination)
    return str(local_dir)
