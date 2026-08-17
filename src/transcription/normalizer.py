"""Deterministic ProviderCandidate to Canonical Transcript normalizer."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .canonical import CanonicalSegment, CanonicalTranscript, _build_canonical, warning_sort_key
from .profile import ProfileSnapshot, TranscriptionExecutionConfig, validate_execution_consistency
from .provider_protocol import ProviderCandidate
from .terminology import correct_terminology, protected_terminology_spans
from .types import (
    ContractValidationError,
    TimeUnit,
    TranscriptWarning,
    TranscriptWarningCode,
    TranscriptionInputRef,
    TerminologyCorrectionConfig,
)


@dataclass(slots=True)
class _WorkSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: int | float | None
    original_positions: tuple[int, ...]


_BOUNDARY_GROUPS = ("\n", "。！？!?；;", "，,", " \t")


def _to_milliseconds(value: str, unit: TimeUnit) -> int:
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ContractValidationError("invalid_decimal_time", "candidate_segment") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ContractValidationError("invalid_decimal_time", "candidate_segment")
    scaled = decimal_value if unit is TimeUnit.milliseconds else decimal_value * 1000
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _warning(
    code: TranscriptWarningCode,
    primary: int | None,
    related: tuple[int, ...] = (),
) -> TranscriptWarning:
    return TranscriptWarning(code, primary, tuple(sorted(set(related))))


def _safe_boundary(
    position: int,
    protected_spans: tuple[tuple[int, int], ...],
) -> bool:
    return not any(start < position < end for start, end in protected_spans)


def _split_raw_text(
    text: str,
    maximum: int,
    *,
    max_duration_ms: int | None = None,
    duration_ms: int | None = None,
    terminology_config: TerminologyCorrectionConfig | None = None,
) -> list[str]:
    if max_duration_ms is not None and duration_ms is not None and duration_ms > max_duration_ms:
        duration_character_limit = max(1, len(text) * max_duration_ms // duration_ms)
        maximum = min(maximum, duration_character_limit)
    pieces: list[str] = []
    remaining = text
    while len(remaining) > maximum:
        chosen = 0
        window = remaining[:maximum]
        protected_spans = protected_terminology_spans(remaining, terminology_config)
        for group in _BOUNDARY_GROUPS:
            positions = [
                index + 1
                for index, char in enumerate(window)
                if char in group and _safe_boundary(index + 1, protected_spans)
            ]
            if positions:
                chosen = positions[-1]
                break
        if chosen == 0:
            chosen = maximum
            containing = next(
                (
                    (start, end)
                    for start, end in protected_spans
                    if start < chosen < end
                ),
                None,
            )
            if containing is not None:
                chosen = containing[0] if containing[0] > 0 else containing[1]
        pieces.append(remaining[:chosen])
        remaining = remaining[chosen:]
    if remaining:
        pieces.append(remaining)
    return pieces


def _split_segment(
    segment: _WorkSegment,
    maximum: int,
    *,
    max_duration_ms: int | None = None,
    terminology_config: TerminologyCorrectionConfig | None = None,
) -> list[_WorkSegment]:
    raw_pieces = _split_raw_text(
        segment.text,
        maximum,
        max_duration_ms=max_duration_ms,
        duration_ms=segment.end_ms - segment.start_ms,
        terminology_config=terminology_config,
    )
    if len(raw_pieces) == 1:
        return [segment]
    if segment.end_ms - segment.start_ms < len(raw_pieces):
        raise ContractValidationError("insufficient_split_duration", "segments")
    total_chars = len(segment.text)
    cumulative = 0
    start = segment.start_ms
    result: list[_WorkSegment] = []
    for index, raw_piece in enumerate(raw_pieces):
        cumulative += len(raw_piece)
        end = (
            segment.end_ms
            if index == len(raw_pieces) - 1
            else segment.start_ms + ((segment.end_ms - segment.start_ms) * cumulative // total_chars)
        )
        cleaned = raw_piece.strip()
        if not cleaned or end <= start:
            raise ContractValidationError("invalid_split_segment", "segments")
        result.append(_WorkSegment(start, end, cleaned, segment.confidence, segment.original_positions))
        start = end
    return result


def normalize_candidate(
    input_ref: TranscriptionInputRef,
    candidate: ProviderCandidate,
    profile_snapshot: ProfileSnapshot,
    execution_config: TranscriptionExecutionConfig,
) -> CanonicalTranscript:
    """Normalize one strict candidate; this is the only Candidate→Canonical entry."""
    if type(candidate) is not ProviderCandidate:
        raise ContractValidationError("invalid_provider_output", "candidate")
    validate_execution_consistency(input_ref, execution_config, profile_snapshot)
    if candidate.provider_key != execution_config.provider_key:
        raise ContractValidationError("candidate_provider_mismatch", "candidate.provider_key")
    if candidate.language != execution_config.language:
        raise ContractValidationError("candidate_language_mismatch", "candidate.language")
    if candidate.duration_ms != input_ref.duration_ms:
        raise ContractValidationError("candidate_duration_mismatch", "candidate.duration_ms")

    warnings: list[TranscriptWarning] = []
    converted: list[_WorkSegment] = []
    for item in candidate.segments:
        start_ms = _to_milliseconds(item.start_value, item.time_unit)
        end_ms = _to_milliseconds(item.end_value, item.time_unit)
        if end_ms <= start_ms:
            raise ContractValidationError("invalid_segment_range", "candidate.segments")
        if end_ms > input_ref.duration_ms:
            raise ContractValidationError("segment_exceeds_duration", "candidate.segments")
        text = item.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            warnings.append(_warning(TranscriptWarningCode.empty_segment_dropped, item.original_position))
            continue
        converted.append(_WorkSegment(start_ms, end_ms, text, item.confidence, (item.original_position,)))
    if not converted:
        raise ContractValidationError("empty_candidate", "candidate.segments")

    converted.sort(key=lambda item: (item.start_ms, item.end_ms, item.original_positions[0]))
    deduplicated: list[_WorkSegment] = []
    exact_seen: dict[tuple[int, int, str], _WorkSegment] = {}
    for item in converted:
        key = (item.start_ms, item.end_ms, item.text)
        kept = exact_seen.get(key)
        if kept is not None:
            warnings.append(
                _warning(
                    TranscriptWarningCode.duplicate_segment_dropped,
                    item.original_positions[0],
                    kept.original_positions,
                )
            )
            continue
        exact_seen[key] = item
        deduplicated.append(item)

    for index, item in enumerate(deduplicated):
        overlaps = tuple(
            previous.original_positions[0]
            for previous in deduplicated[:index]
            if item.start_ms < previous.end_ms
        )
        if overlaps:
            warnings.append(
                _warning(
                    TranscriptWarningCode.segment_overlap,
                    item.original_positions[0],
                    tuple(sorted(overlaps)),
                )
            )

    config = execution_config.normalizer_config
    segmentation = execution_config.segmentation_config
    merge_gap_ms = (
        config.max_merge_gap_ms
        if segmentation is None
        else segmentation.max_merge_gap_ms
    )
    max_segment_chars = (
        config.max_segment_chars
        if segmentation is None
        else segmentation.max_segment_chars
    )
    merged: list[_WorkSegment] = []
    index = 0
    while index < len(deduplicated):
        current = deduplicated[index]
        if index + 1 < len(deduplicated):
            following = deduplicated[index + 1]
            joined_text = current.text + "\n" + following.text
            gap = following.start_ms - current.end_ms
            if (
                len(current.text) < config.min_segment_chars
                and gap >= 0
                and gap <= merge_gap_ms
                and len(joined_text) <= max_segment_chars
            ):
                positions = current.original_positions + following.original_positions
                merged.append(_WorkSegment(current.start_ms, following.end_ms, joined_text, None, positions))
                warnings.append(
                    _warning(
                        TranscriptWarningCode.short_segment_merged,
                        current.original_positions[0],
                        following.original_positions,
                    )
                )
                index += 2
                continue
        merged.append(current)
        index += 1

    corrected_segments: list[_WorkSegment] = []
    for item in merged:
        corrected_text, changed = correct_terminology(
            item.text, execution_config.terminology_config
        )
        corrected_segments.append(
            _WorkSegment(
                item.start_ms,
                item.end_ms,
                corrected_text,
                item.confidence,
                item.original_positions,
            )
        )
        if changed:
            warnings.append(
                _warning(
                    TranscriptWarningCode.terminology_corrected,
                    item.original_positions[0],
                    item.original_positions[1:],
                )
            )

    final_segments: list[_WorkSegment] = []
    for item in corrected_segments:
        pieces = _split_segment(
            item,
            max_segment_chars,
            max_duration_ms=(
                None
                if segmentation is None
                else segmentation.max_segment_duration_ms
            ),
            terminology_config=execution_config.terminology_config,
        )
        if len(pieces) > 1:
            warnings.append(
                _warning(
                    (
                        TranscriptWarningCode.duration_segment_split
                        if segmentation is not None
                        and segmentation.max_segment_duration_ms is not None
                        and item.end_ms - item.start_ms
                        > segmentation.max_segment_duration_ms
                        else TranscriptWarningCode.long_segment_split
                    ),
                    item.original_positions[0],
                    item.original_positions[1:],
                )
            )
        final_segments.extend(pieces)

    canonical_segments = tuple(
        CanonicalSegment(index, item.start_ms, item.end_ms, item.text, item.confidence)
        for index, item in enumerate(final_segments)
    )
    canonical_warnings = tuple(sorted(set(warnings), key=warning_sort_key))
    return _build_canonical(
        media_id=input_ref.media_id,
        input_sha256=input_ref.content_sha256,
        profile_snapshot=profile_snapshot,
        language=candidate.language,
        duration_ms=candidate.duration_ms,
        segments=canonical_segments,
        warnings=canonical_warnings,
        artifact_refs=candidate.artifact_refs,
    )
