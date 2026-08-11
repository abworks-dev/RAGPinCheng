from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath

from fastapi import UploadFile


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANAGED_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,127}$")


@dataclass(frozen=True, slots=True)
class StoredContentObject:
    sha256: str
    size_bytes: int
    mime_type: str
    storage_rel_path: str
    absolute_path: Path
    created: bool


class ContentStorage:
    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("content_root_must_be_path")
        self.root = root.resolve(strict=False)
        self.inbox_root = self.root / "inbox"
        self.objects_root = self.root / "objects" / "sha256"
        self.media_root = self.root / "media"
        self.artifacts_root = self.root / "transcription-artifacts"
        self.manifests_root = self.root / "manifests"
        self.published_root = self.root / "published"
        self.quarantine_root = self.root / "quarantine"
        self.views_root = self.root / "views" / "current"

    def ensure_layout(self) -> None:
        for path in (
            self.inbox_root / "web",
            self.inbox_root / "server",
            self.objects_root,
            self.media_root,
            self.artifacts_root,
            self.manifests_root,
            self.published_root,
            self.quarantine_root,
            self.views_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_filename(filename: str) -> str:
        clean = filename.strip()
        if (
            not clean
            or clean in {".", ".."}
            or PurePath(clean).name != clean
            or "/" in clean
            or "\\" in clean
            or "\x00" in clean
        ):
            raise ValueError("invalid_filename")
        return clean

    async def ingest_upload(
        self,
        upload: UploadFile,
        *,
        batch_id: str,
        max_bytes: int,
    ) -> StoredContentObject:
        if max_bytes <= 0:
            raise ValueError("invalid_max_bytes")
        self.validate_filename(upload.filename or "")
        self.ensure_layout()
        staging_dir = self.inbox_root / "web" / batch_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        temporary = staging_dir / f".{uuid.uuid4().hex}.upload"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("content_too_large")
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise ValueError("empty_content")
            sha256 = digest.hexdigest()
            target = self.objects_root / sha256[:2] / sha256
            target.parent.mkdir(parents=True, exist_ok=True)
            created = not target.exists()
            if created:
                os.replace(temporary, target)
            else:
                temporary.unlink(missing_ok=True)
            rel = target.relative_to(self.root).as_posix()
            return StoredContentObject(
                sha256=sha256,
                size_bytes=size,
                mime_type=(upload.content_type or "application/octet-stream")[:255],
                storage_rel_path=rel,
                absolute_path=target,
                created=created,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def resolve_object(self, storage_rel_path: str) -> Path:
        candidate = (self.root / storage_rel_path).resolve(strict=False)
        objects_root = self.objects_root.resolve(strict=False)
        if candidate.parent != objects_root and objects_root not in candidate.parents:
            raise ValueError("content_path_escape")
        if candidate.is_symlink():
            raise ValueError("content_symlink_rejected")
        return candidate

    def object_path_for_sha256(self, sha256: str) -> Path:
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError("invalid_sha256")
        return self.objects_root / sha256[:2] / sha256

    def published_source_path(
        self,
        content_item_id: str,
        content_version_id: str,
        filename: str,
    ) -> Path:
        if not _MANAGED_ID_RE.fullmatch(content_item_id):
            raise ValueError("invalid_content_item_id")
        if not _MANAGED_ID_RE.fullmatch(content_version_id):
            raise ValueError("invalid_content_version_id")
        clean_name = self.validate_filename(filename)
        return self.published_root / content_item_id / content_version_id / clean_name

    def materialize_published_source(
        self,
        source: Path,
        *,
        content_item_id: str,
        content_version_id: str,
        filename: str,
    ) -> Path:
        if not source.is_file() or source.is_symlink():
            raise ValueError("invalid_content_object")
        self.ensure_layout()
        target = self.published_source_path(
            content_item_id, content_version_id, filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{uuid.uuid4().hex}.materialize"
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def ingest_path(
        self,
        source: Path,
        *,
        mime_type: str,
        max_bytes: int,
    ) -> StoredContentObject:
        if not source.is_file() or source.is_symlink():
            raise ValueError("invalid_source_file")
        self.ensure_layout()
        temporary = self.objects_root / f".{uuid.uuid4().hex}.import"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("content_too_large")
                    digest.update(chunk)
                    writer.write(chunk)
            if size == 0:
                raise ValueError("empty_content")
            sha256 = digest.hexdigest()
            target = self.object_path_for_sha256(sha256)
            target.parent.mkdir(parents=True, exist_ok=True)
            created = not target.exists()
            if created:
                os.replace(temporary, target)
            else:
                temporary.unlink(missing_ok=True)
            return StoredContentObject(
                sha256=sha256,
                size_bytes=size,
                mime_type=mime_type[:255],
                storage_rel_path=target.relative_to(self.root).as_posix(),
                absolute_path=target,
                created=created,
            )
        finally:
            temporary.unlink(missing_ok=True)
