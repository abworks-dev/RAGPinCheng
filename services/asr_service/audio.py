"""In-process audio decoding and deterministic ASR window extraction."""
from __future__ import annotations

from io import BytesIO
import wave


def extract_audio_window(
    content: bytes,
    *,
    start_ms: int,
    end_ms: int,
) -> bytes:
    """Decode once through faster-whisper/PyAV and return a WAV window.

    The production Windows host deliberately does not require an ffmpeg binary.
    faster-whisper ships the in-process PyAV decoder, so use that path for all
    containers and hand every engine a normalized mono 16 kHz WAV.
    """
    if not content or start_ms < 0 or end_ms <= start_ms:
        raise ValueError("invalid audio window")
    try:
        import numpy as np
        from faster_whisper.audio import decode_audio

        samples = decode_audio(BytesIO(content), sampling_rate=16000)
        start = int(start_ms * 16000 / 1000)
        end = min(len(samples), int(end_ms * 16000 / 1000))
        if end <= start:
            raise RuntimeError("audio window contains no samples")
        pcm = np.clip(samples[start:end] * 32768.0, -32768, 32767).astype(np.int16)
        output = BytesIO()
        with wave.open(output, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm.tobytes())
        return output.getvalue()
    except Exception as exc:
        raise RuntimeError(f"audio window extraction failed: {exc}") from exc
