"""Controlled local artifact adapter for generated transcript Markdown."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from src.transcription.persistence import ManagedMarkdownRef, validate_relative_identity
from src.transcription.types import ContractValidationError, sha256_hex


class LocalTranscriptionArtifactStore:
    """Content-addressed Markdown storage rooted at an explicitly supplied path."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ContractValidationError("invalid_artifact_root", "root")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def write_markdown(self, content: bytes) -> ManagedMarkdownRef:
        if type(content) is not bytes:
            raise ContractValidationError("invalid_bytes", "markdown")
        digest = sha256_hex(content)
        relative = f"markdown/{digest[:2]}/{digest}.md"
        final_path = self._resolve(relative)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            existing = final_path.read_bytes()
            if len(existing) != len(content) or sha256_hex(existing) != digest:
                raise ContractValidationError("artifact_content_collision", "markdown")
            return ManagedMarkdownRef(relative, digest, len(content))

        temporary = final_path.parent / f".{digest}.{uuid.uuid4()}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if final_path.exists():
                existing = final_path.read_bytes()
                if len(existing) != len(content) or sha256_hex(existing) != digest:
                    raise ContractValidationError("artifact_content_collision", "markdown")
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, final_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return ManagedMarkdownRef(relative, digest, len(content))

    def load_verified(self, reference: ManagedMarkdownRef) -> bytes:
        if type(reference) is not ManagedMarkdownRef:
            raise ContractValidationError("invalid_markdown_ref", "reference")
        path = self._resolve(reference.relative_path)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ContractValidationError("artifact_unavailable", "reference.relative_path") from exc
        if len(content) != reference.size_bytes or sha256_hex(content) != reference.content_sha256:
            raise ContractValidationError("artifact_hash_mismatch", "reference")
        return content

    def _resolve(self, relative_path: str) -> Path:
        validate_relative_identity(relative_path)
        candidate = (self._root / Path(*relative_path.split("/"))).resolve(strict=False)
        root = self._root.resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise ContractValidationError("artifact_path_escape", "relative_path")
        return candidate
