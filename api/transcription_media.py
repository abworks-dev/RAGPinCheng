"""Controlled MP4-to-audio preparation and file-backed provider input."""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from src.transcription.runtime_ports import InputPart
from src.transcription.types import (
    ContractValidationError,
    TranscriptionInputRef,
    require_int,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    input_ref: TranscriptionInputRef
    path: Path


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout_seconds: int) -> int: ...


@dataclass(frozen=True, slots=True)
class SubprocessCommandRunner:
    def run(self, args: Sequence[str], *, timeout_seconds: int) -> int:
        completed = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        return completed.returncode


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _validated_audio(path: Path, media_id: str) -> PreparedAudio:
    try:
        with wave.open(str(path), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getframerate() != 16_000
                or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE"
            ):
                raise ContractValidationError("invalid_prepared_audio_format", "audio")
            frames = audio.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise ContractValidationError("invalid_prepared_audio", "audio") from exc
    if frames <= 0:
        raise ContractValidationError("empty_prepared_audio", "audio")
    digest, size = _sha256_file(path)
    duration_ms = (frames * 1000 + 15_999) // 16_000
    return PreparedAudio(
        TranscriptionInputRef(media_id, "audio", digest, size, duration_ms), path
    )


@dataclass(frozen=True, slots=True)
class FfmpegMediaAudioPreparer:
    media_root: Path
    executable: str
    timeout_seconds: int
    runner: CommandRunner = SubprocessCommandRunner()

    def __post_init__(self) -> None:
        if not isinstance(self.media_root, Path) or not self.media_root.is_absolute():
            raise ContractValidationError("invalid_media_root", "media_root")
        if type(self.executable) is not str or not self.executable.strip():
            raise ContractValidationError("invalid_ffmpeg_path", "executable")
        require_int(self.timeout_seconds, "timeout_seconds", positive=True)

    def prepare(self, media_id: str) -> PreparedAudio:
        validate_uuid(media_id, "media_id")
        root = self.media_root.resolve(strict=False)
        media_dir = (root / media_id).resolve(strict=False)
        if media_dir.parent != root:
            raise ContractValidationError("media_path_escape", "media_id")
        source = media_dir / "original.mp4"
        final = media_dir / "prepared-audio-v1.wav"
        if final.exists():
            if final.is_symlink() or not final.is_file():
                raise ContractValidationError("invalid_prepared_audio_path", "audio")
            return _validated_audio(final, media_id)
        if not source.is_file() or source.is_symlink():
            raise ContractValidationError("media_input_unavailable", "media_id")

        temporary = media_dir / f".prepared-audio-{uuid.uuid4()}.tmp.wav"
        args = (
            self.executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        )
        try:
            if self.runner.run(args, timeout_seconds=self.timeout_seconds) != 0:
                raise ContractValidationError("media_audio_preparation_failed", "media_id")
            prepared = _validated_audio(temporary, media_id)
            os.replace(temporary, final)
            return PreparedAudio(prepared.input_ref, final)
        except subprocess.TimeoutExpired as exc:
            raise ContractValidationError("media_audio_preparation_timeout", "media_id") from exc
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FileTranscriptionInputSource:
    prepared: PreparedAudio

    def iter_parts(self, input_ref: TranscriptionInputRef, part_size_bytes: int):
        if input_ref != self.prepared.input_ref:
            raise ContractValidationError("input_reference_mismatch", "input_ref")
        require_int(part_size_bytes, "part_size_bytes", positive=True)
        preflight_sha256, preflight_size = _sha256_file(self.prepared.path)
        if preflight_size != input_ref.size_bytes or preflight_sha256 != input_ref.content_sha256:
            raise ContractValidationError("input_content_mismatch", "input_ref")
        digest = hashlib.sha256()
        offset = 0
        with self.prepared.path.open("rb") as handle:
            part_number = 0
            while content := handle.read(part_size_bytes):
                digest.update(content)
                yield InputPart(
                    part_number,
                    offset,
                    content,
                    hashlib.sha256(content).hexdigest(),
                )
                offset += len(content)
                part_number += 1
        if offset != input_ref.size_bytes or digest.hexdigest() != input_ref.content_sha256:
            raise ContractValidationError("input_content_mismatch", "input_ref")
