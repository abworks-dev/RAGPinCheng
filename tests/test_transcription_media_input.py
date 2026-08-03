from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from api.transcription_media import (
    FfmpegMediaAudioPreparer,
    FileTranscriptionInputSource,
)
from src.transcription.types import ContractValidationError

MEDIA_ID = "11111111-1111-4111-8111-111111111111"


@dataclass
class FakeRunner:
    return_code: int = 0
    calls: int = 0

    def run(self, args, *, timeout_seconds: int) -> int:
        self.calls += 1
        if self.return_code == 0:
            with wave.open(str(Path(args[-1])), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"\0\0" * 16_000)
        return self.return_code


def make_preparer(tmp_path, runner=None):
    root = (tmp_path / "media").resolve()
    media = root / MEDIA_ID
    media.mkdir(parents=True)
    (media / "original.mp4").write_bytes(b"fake-mp4")
    return FfmpegMediaAudioPreparer(root, "ffmpeg-test", 10, runner or FakeRunner())


def test_fake_ffmpeg_produces_fixed_audio_identity_and_is_reused(tmp_path):
    runner = FakeRunner()
    preparer = make_preparer(tmp_path, runner)
    first = preparer.prepare(MEDIA_ID)
    second = preparer.prepare(MEDIA_ID)
    assert first == second
    assert first.input_ref.input_kind == "audio"
    assert first.input_ref.duration_ms == 1000
    assert first.input_ref.size_bytes == first.path.stat().st_size
    assert runner.calls == 1


def test_file_input_source_streams_ordered_parts_and_checks_full_identity(tmp_path):
    prepared = make_preparer(tmp_path).prepare(MEDIA_ID)
    parts = tuple(FileTranscriptionInputSource(prepared).iter_parts(prepared.input_ref, 1024))
    content = b"".join(part.content for part in parts)
    assert [part.part_number for part in parts] == list(range(len(parts)))
    assert hashlib.sha256(content).hexdigest() == prepared.input_ref.content_sha256


def test_failed_ffmpeg_does_not_publish_prepared_audio(tmp_path):
    preparer = make_preparer(tmp_path, FakeRunner(return_code=1))
    with pytest.raises(ContractValidationError, match="media_audio_preparation_failed"):
        preparer.prepare(MEDIA_ID)
    assert not (preparer.media_root / MEDIA_ID / "prepared-audio-v1.wav").exists()
    assert not list((preparer.media_root / MEDIA_ID).glob("*.tmp.wav"))


def test_symlink_or_unknown_media_identity_fails_closed(tmp_path):
    preparer = make_preparer(tmp_path)
    with pytest.raises(ContractValidationError):
        preparer.prepare("../outside")
