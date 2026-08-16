"""Validation and normalization for editable transcript Markdown."""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.transcription.types import ContractValidationError

MAX_TRANSCRIPT_MARKDOWN_BYTES = 2 * 1024 * 1024

TRANSCRIPT_TURN_RE = re.compile(
    r"^#*\s*说话[人⼈]\s+\d+\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class ParsedTranscriptSegment:
    start_ms: int
    end_ms: int | None
    text: str


def normalize_markdown(markdown: str) -> str:
    if type(markdown) is not str:
        raise ContractValidationError("invalid_markdown_type", "markdown")
    return markdown.replace("\r\n", "\n").replace("\r", "\n")


def parse_transcript_turns(markdown: str) -> list[tuple[str, str]]:
    normalized = normalize_markdown(markdown)
    matches = list(TRANSCRIPT_TURN_RE.finditer(normalized))
    turns: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        body = normalized[body_start:body_end].strip()
        if body:
            turns.append((match.group(1), body))
    return turns


def timestamp_to_ms(timestamp: str) -> int:
    parts = [int(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        if seconds > 59:
            raise ContractValidationError("invalid_transcript_timestamp", "markdown")
        return (minutes * 60 + seconds) * 1000
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if minutes > 59 or seconds > 59:
            raise ContractValidationError("invalid_transcript_timestamp", "markdown")
        return (hours * 3600 + minutes * 60 + seconds) * 1000
    raise ContractValidationError("invalid_transcript_timestamp", "markdown")


def parse_transcript_segments(markdown: str) -> list[ParsedTranscriptSegment]:
    turns = parse_transcript_turns(markdown)
    segments: list[ParsedTranscriptSegment] = []
    for index, (timestamp, text) in enumerate(turns):
        start_ms = timestamp_to_ms(timestamp)
        next_start = (
            timestamp_to_ms(turns[index + 1][0])
            if index + 1 < len(turns)
            else None
        )
        segments.append(
            ParsedTranscriptSegment(
                start_ms=start_ms,
                end_ms=next_start if next_start is not None and next_start > start_ms else None,
                text=text,
            )
        )
    return segments


def _validate_timestamp(timestamp: str) -> None:
    parts = [int(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        _minutes, seconds = parts
        if seconds > 59:
            raise ContractValidationError("invalid_transcript_timestamp", "markdown")
        return
    _hours, minutes, seconds = parts
    if minutes > 59 or seconds > 59:
        raise ContractValidationError("invalid_transcript_timestamp", "markdown")


def validate_editable_transcript_markdown(markdown: str) -> bytes:
    normalized = normalize_markdown(markdown)
    try:
        content = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractValidationError("invalid_markdown_encoding", "markdown") from exc
    if not content:
        raise ContractValidationError("empty_transcript_markdown", "markdown")
    if len(content) > MAX_TRANSCRIPT_MARKDOWN_BYTES:
        raise ContractValidationError("transcript_markdown_too_large", "markdown")
    matches = list(TRANSCRIPT_TURN_RE.finditer(normalized))
    for match in matches:
        _validate_timestamp(match.group(1))
    if not parse_transcript_turns(normalized):
        raise ContractValidationError("transcript_turn_required", "markdown")
    return content
