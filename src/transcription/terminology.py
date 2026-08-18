"""Deterministic, versioned terminology correction for transcript text."""
from __future__ import annotations

import re

from .types import TerminologyCorrectionConfig

BIM_ENGINEERING_TERMS_V1 = (
    "Revit",
    "Navisworks",
    "AutoCAD",
    "BIM",
    "BIM-2026-0805",
    "12.5",
    "208",
    "95%",
)


_RULES_V1 = (
    (re.compile(r"(?<![A-Za-z0-9])auto[ \t]+cad(?![A-Za-z0-9])", re.IGNORECASE), "AutoCAD"),
    (re.compile(r"(?<![A-Za-z0-9])b[ \t]+i[ \t]+m(?![A-Za-z0-9])", re.IGNORECASE), "BIM"),
    (re.compile(r"(?<![A-Za-z0-9])revit(?![A-Za-z0-9])", re.IGNORECASE), "Revit"),
    (re.compile(r"(?<![A-Za-z0-9])navisworks(?![A-Za-z0-9])", re.IGNORECASE), "Navisworks"),
)
_BIM_CODE = re.compile(
    r"(?<![A-Za-z0-9])BIM[ \t]*[- ][ \t]*(\d{4})[ \t]*[- ][ \t]*(\d{4})(?!\d)",
    re.IGNORECASE,
)
_STANDARD_CODE = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>GB(?:/T)?|JGJ|DG/TJ)[ \t]*(?P<number>\d{3,6})[ \t]*(?:-| )[ \t]*(?P<year>\d{4})(?!\d)",
    re.IGNORECASE,
)
_DECIMAL = re.compile(r"(?<!\d)(\d+)[ \t]*\.[ \t]*(\d+)(?!\d)")
_PERCENT = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)[ \t]+%(?![A-Za-z0-9])")
_PROTECTED = re.compile(
    r"(?<![A-Za-z0-9])(?:AutoCAD|Navisworks|Revit|BIM(?:-\d{4}-\d{4})?|GB(?:/T)? \d{3,6}-\d{4}|JGJ \d{3,6}-\d{4}|DG/TJ \d{3,6}-\d{4}|12\.5|208|95%)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def correct_terminology(
    text: str, config: TerminologyCorrectionConfig | None
) -> tuple[str, bool]:
    if config is None or config.rule_set_id == "none":
        return text, False
    corrected = text
    for pattern, replacement in _RULES_V1:
        corrected = pattern.sub(replacement, corrected)
    corrected = _BIM_CODE.sub(lambda match: f"BIM-{match.group(1)}-{match.group(2)}", corrected)
    corrected = _STANDARD_CODE.sub(
        lambda match: f"{match.group('prefix').upper()} {match.group('number')}-{match.group('year')}",
        corrected,
    )
    corrected = _DECIMAL.sub(lambda match: f"{match.group(1)}.{match.group(2)}", corrected)
    corrected = _PERCENT.sub(lambda match: f"{match.group(1)}%", corrected)
    return corrected, corrected != text


def protected_terminology_spans(
    text: str, config: TerminologyCorrectionConfig | None
) -> tuple[tuple[int, int], ...]:
    if config is None or config.rule_set_id == "none":
        return ()
    return tuple((match.start(), match.end()) for match in _PROTECTED.finditer(text))
