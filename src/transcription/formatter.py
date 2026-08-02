"""Deterministic automatic transcript Markdown formatter."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .canonical import CanonicalTranscript
from .types import ContractValidationError, require_string

_SPEAKER_LINE_RE = re.compile(r"^#*\s*说话[人⼈]\s+\d+\s+\d{1,2}:\d{2}(?::\d{2})?\s*$")


@dataclass(frozen=True, slots=True)
class FormatterContext:
    title: str

    def __post_init__(self) -> None:
        value = require_string(self.title, "title").strip()
        if not value or "\n" in value or "\r" in value:
            raise ContractValidationError("invalid_title", "title")
        if _SPEAKER_LINE_RE.fullmatch(value):
            raise ContractValidationError("formatter_speaker_marker_collision", "title")
        object.__setattr__(self, "title", value)


def _timestamp(start_ms: int) -> str:
    if start_ms < 0 or start_ms >= 360_000_000:
        raise ContractValidationError("formatter_timestamp_out_of_range", "start_ms")
    total_seconds = start_ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _body_has_collision(text: str) -> bool:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return any(_SPEAKER_LINE_RE.fullmatch(line.strip()) for line in normalized.split("\n"))


def format_transcript(canonical: CanonicalTranscript, *, title: str) -> bytes:
    if type(canonical) is not CanonicalTranscript:
        raise ContractValidationError("invalid_canonical", "canonical")
    context = FormatterContext(title)
    bodies: list[str] = []
    for segment in canonical.segments:
        if _body_has_collision(segment.text):
            raise ContractValidationError("formatter_speaker_marker_collision", f"segments[{segment.id}].text")
        body = segment.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not body:
            raise ContractValidationError("empty_segment", f"segments[{segment.id}].text")
        bodies.append(f"说话人 {segment.id + 1} {_timestamp(segment.start_ms)}\n{body}")
    if not bodies:
        raise ContractValidationError("empty_canonical", "canonical.segments")
    return (f"# {context.title}\n\n" + "\n\n".join(bodies) + "\n").encode("utf-8")
