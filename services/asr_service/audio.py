"""Small, deterministic audio-window extraction helpers for ASR chunking."""
from __future__ import annotations

import subprocess


def extract_audio_window(
    content: bytes,
    *,
    start_ms: int,
    end_ms: int,
) -> bytes:
    """Decode one time window to mono 16 kHz WAV bytes."""
    if not content or start_ms < 0 or end_ms <= start_ms:
        raise ValueError("invalid audio window")
    duration_ms = end_ms - start_ms
    command = (
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_ms / 1000:.3f}",
        "-t", f"{duration_ms / 1000:.3f}",
        "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", "pipe:1",
    )
    completed = subprocess.run(
        command,
        input=content,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"audio window extraction failed: {detail[:200]}")
    return completed.stdout
