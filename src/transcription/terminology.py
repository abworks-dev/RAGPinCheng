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


# ---------------------------------------------------------------------------
# Conservative hallucinated standard/term tail cleanup.
#
# Real long-media whisperx/faster decode, prompted by high-bias hotwords, can
# append "hallucinated" filler at low-information pauses/segment ends, e.g.
#   "建筑抗震设复核", "建筑抗震设复核 钢结构",
#   "建筑抗震设复核 规范 GB 50011-2010", "净高分析规范 GB 50011-2010 建筑抗震设复核",
#   "建筑抗震设复核 净高分析规范 GB 50011-2014"   <- GB 50011-2014 does not exist
# where the speaker never actually said those terms/numbers. This cleanup is
# deliberately CONSERVATIVE: it DROPS a whole segment only when its text is
# composed exclusively of the recognised bias words/phrases, standard-code
# tokens, and generic 规范/编号 connectors — i.e. no other meaningful content.
# Any segment containing real speech (measurements, verbs, other Han) is left
# untouched, so genuine citations are preserved. Fabricated code *years* are
# handled implicitly: a fake edition like GB 50011-2014 only ever survives
# inside a bias-only line, which is dropped here; we never rewrite an embedded
# year inside real speech. No engine, no qualification gate, no GPU threshold
# is changed.
# ---------------------------------------------------------------------------
# "Noise words" that appear only as hallucinated filler when isolated.
_BIAS_WORDS = (
    "建筑抗震设复核",
    "建筑设计防火规范",
    "建筑抗震设计规范",
    "净高分析规范",
    "净高分析",
    "构件碰撞",
    "建筑信息模型",
    "规范编号",
    "复核",
    "钢结构",
    "焊缝",
    "螺栓",
)
# Generic connectors/code tokens that, inside an otherwise pure-bias line, are
# still hallucinated filler (e.g. "... 规范 GB 50011-2010"). A real speech
# segment always leaves other words, so removing these only triggers the drop
# when nothing meaningful remains.
_BIAS_CONNECTORS = ("规范", "编号")
_BIAS_STANDARD_CODE = re.compile(
    r"(?:GB(?:/T)?|JGJ|DG/TJ)\s*\d{3,6}\s*(?:-| )\s*\d{0,4}|"
    r"(?:GB(?:/T)?|JGJ|DG/TJ)\s*\d{3,6}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
def _is_bias_only(text: str) -> bool:
    """True if text is composed only of recognised bias words/phrases, standard
    code tokens, generic 规范/编号 connectors, whitespace and commas — i.e. no
    other meaningful Han/ASCII/numeral content (real speech)."""
    candidate = text
    for phrase in sorted(_BIAS_WORDS, key=len, reverse=True):
        candidate = candidate.replace(phrase, " ")
    for connector in _BIAS_CONNECTORS:
        candidate = candidate.replace(connector, " ")
    candidate = _BIAS_STANDARD_CODE.sub(" ", candidate)
    candidate = candidate.replace("，", " ").replace(",", " ").replace("/", " ").replace("／", " ")
    candidate = re.sub(r"[\s。.、·—\-:：\u3000]", "", candidate)
    return candidate == ""


def clean_hallucinated_standard(text: str) -> tuple[str, bool]:
    """Conservative scrub of hallucinated standard/code tails.

    Returns (cleaned_text, changed). Deterministic and deliberately narrow: a
    whole segment is dropped (returns "", True) ONLY when its text is composed
    exclusively of recognised hotword-bias filler (no other meaningful content),
    e.g. "建筑抗震设复核", "建筑抗震设复核 规范 GB 50011-2010",
    "净高分析规范 GB 50011-2014". These are the pure hallucination lines the
    decode appends at pauses. Any segment containing real speech (measurements,
    verbs, other Han) is returned unchanged, so genuine citations are preserved.
    Fabricated code *years* are therefore handled implicitly: a fake edition like
    GB 50011-2014 only ever survives inside a bias-only line, which is dropped
    here; we never rewrite an embedded year inside real speech.
    """
    original = text
    if not text or not text.strip():
        return text, False
    if _is_bias_only(text.strip()):
        return "", True
    return text, False
