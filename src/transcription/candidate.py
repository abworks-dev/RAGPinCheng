"""Provider candidate segment values, before Canonical normalization."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import (
    ContractValidationError,
    TimeUnit,
    reject_unknown_fields,
    require_exact_enum,
    require_int,
    require_string,
    validate_confidence,
    validate_decimal_time,
)


@dataclass(frozen=True, slots=True)
class CandidateSegment:
    original_position: int
    start_value: str
    end_value: str
    time_unit: TimeUnit
    text: str
    confidence: int | float | None = None

    def __post_init__(self) -> None:
        require_int(self.original_position, "candidate_segment.original_position")
        validate_decimal_time(self.start_value, "candidate_segment.start_value")
        validate_decimal_time(self.end_value, "candidate_segment.end_value")
        require_exact_enum(self.time_unit, TimeUnit, "candidate_segment.time_unit")
        require_string(self.text, "candidate_segment.text", nonempty=False)
        validate_confidence(self.confidence, "candidate_segment.confidence")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "original_position": self.original_position,
            "start_value": self.start_value,
            "end_value": self.end_value,
            "time_unit": self.time_unit.value,
            "text": self.text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "CandidateSegment":
        obj = reject_unknown_fields(
            data,
            {"original_position", "start_value", "end_value", "time_unit", "text", "confidence"},
            "candidate_segment",
        )
        try:
            unit = TimeUnit(obj["time_unit"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid_time_unit", "candidate_segment.time_unit") from exc
        return cls(
            obj["original_position"],
            obj["start_value"],
            obj["end_value"],
            unit,
            obj["text"],
            obj["confidence"],
        )
