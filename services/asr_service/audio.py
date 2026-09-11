"""In-process audio decoding and deterministic ASR window extraction."""
from __future__ import annotations

from io import BytesIO
import wave


def decode_audio_samples(content: bytes) -> object:
    """Decode the whole upload once into mono 16 kHz float32 samples.

    The production Windows host deliberately has no ffmpeg binary, so use
    faster-whisper's bundled PyAV decoder. Decoding happens once per job; every
    window is then sliced from these samples, which keeps long recordings
    linear instead of re-decoding the whole file per window.
    """
    if not content:
        raise ValueError("invalid audio content")
    try:
        import numpy as np
        from faster_whisper.audio import decode_audio

        samples = decode_audio(BytesIO(content), sampling_rate=16000)
        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        if array.size == 0:
            raise RuntimeError("audio decode returned no samples")
        return array
    except Exception as exc:
        raise RuntimeError(f"audio decode failed: {exc}") from exc


def encode_wav_window(
    samples: object,
    *,
    start_ms: int,
    end_ms: int,
) -> bytes:
    """Slice one time window out of decoded samples as mono 16 kHz WAV bytes."""
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("invalid audio window")
    try:
        import numpy as np

        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        start = int(start_ms * 16000 / 1000)
        end = min(array.size, int(end_ms * 16000 / 1000))
        if end <= start:
            raise RuntimeError("audio window contains no samples")
        pcm = np.clip(array[start:end] * 32768.0, -32768, 32767).astype(np.int16)
        output = BytesIO()
        with wave.open(output, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm.tobytes())
        return output.getvalue()
    except Exception as exc:
        raise RuntimeError(f"audio window extraction failed: {exc}") from exc
