"""Phase 0 ASR sandbox — metrics utilities (R2 fix).

All metrics are pure Python (stdlib + numpy only). No GPL dependencies.
RTF convention: RTF = inference_wallclock_s / audio_duration_s
                realtime_speedup = audio_duration_s / inference_wallclock_s
                RTF < 1 means faster than realtime.

Standard code normalization:
  - case-insensitive (lowercase)
  - whitespace stripped
  - 'JGJ/T' and 'JGJ-T' both normalized to 'jgj/t'
  - year suffix optional '-YYYY' (normalized to optional '-YYYY')
  - canonical form: (family_lc, number_lc, year_or_None)
    where family in {gb, jgj, cjj}
  precision = TP / (TP + FP) on hypothesis
  recall    = TP / (TP + FN) on reference
  false_positive = FP

BIM term metrics:
  - normalization: NFKC + lowercase + strip whitespace/punctuation
  - precision = TP / (TP + FP) — detects wrong-forced-in terms (typos)
  - recall    = TP / (TP + FN)
  false_positive_detail: per-term TP/FP/FN

Segment alignment:
  - one-to-one monotone dynamic-programming sequence alignment
  - each reference matches at most one hypothesis
  - reports:
      start_drift_p50_ms, start_drift_p95_ms, start_drift_p99_ms, start_drift_max_ms
      end_drift_p50_ms,  end_drift_p95_ms,  end_drift_p99_ms,  end_drift_max_ms
      omission_rate      = (n_ref - n_matched) / n_ref
      extra_rate         = n_hyp_strictly_after_last_ref / n_hyp
      consecutive_repeat_rate = n_runs_2plus / n_hyp_runs

CER normalization (version cer-norm/1):
  - NFKC + lowercase + remove Unicode category P* (punctuation) and Z* (separator)
  - strip whitespace before scoring
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CER_NORM_VERSION = "cer-norm/1"
METRICS_SCHEMA_VERSION = "phase0-metrics/1"

# ─────────────────────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────────────────────

_PUNCT_CATS = {"P", "Z"}  # Punctuation + Separator


def _strip_punct_ws(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    return "".join(c for c in s if unicodedata.category(c)[0] not in _PUNCT_CATS)


# ─────────────────────────────────────────────────────────────────────────────
# CER (pure-Python edit distance, no Levenshtein dep)
# ─────────────────────────────────────────────────────────────────────────────


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate. Empty reference -> 1.0 if hyp non-empty, else 0.0."""
    ref = _strip_punct_ws(reference)
    hyp = _strip_punct_ws(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    if ref == hyp:
        return 0.0
    # Standard Wagner-Fischer on code points
    a = ref
    b = hyp
    la, lb = len(a), len(b)
    if la == 0:
        return float(lb)
    if lb == 0:
        # All reference characters deleted; CER = 1.0
        return 1.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb] / la


# ─────────────────────────────────────────────────────────────────────────────
# Standard codes
# ─────────────────────────────────────────────────────────────────────────────

_CODE_FAMILIES = {"gb", "jgj", "cjj"}
# (family, digits-1-to-5, optional -YYYY) — case-insensitive
_CODE_RE = re.compile(
    r"(?P<fam>GB|JGJ|CJJ)\s*[/\-]?\s*T?\s*"
    r"(?P<num>\d{1,5})"
    r"(?:\s*[/\-]\s*(?P<yr>\d{4}))?",
    re.IGNORECASE,
)


def _canon_code(m: re.Match[str]) -> tuple[str, str, str | None]:
    fam = m.group("fam").lower()
    # collapse "JGJ-T" and "JGJ/T" and "JGJT" to "jgj/t"
    fam_full = fam
    # if "t" appears immediately after fam (no slash) and we matched JGJ, treat as jgj/t
    raw = m.group(0)
    if "t" in raw.lower() and fam == "jgj":
        fam_full = "jgj/t"
    num = m.group("num")
    yr = m.group("yr")
    return fam_full, num, yr


def extract_codes(text: str) -> set[tuple[str, str]]:
    """Return set of canonical (family, number) tuples.

    Year is normalized separately via extract_codes_with_year() if needed.
    """
    return {(m.group("fam").lower(), m.group("num")) for m in _CODE_RE.finditer(text or "")}


def extract_codes_with_year(text: str) -> set[tuple[str, str, str | None]]:
    """Return set of canonical (family, number, year) tuples (year may be None)."""
    return {_canon_code(m) for m in _CODE_RE.finditer(text or "")}


@dataclass
class CodeMetrics:
    precision: float
    recall: float
    false_positive: int
    true_positive: int
    false_negative: int
    per_item: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "false_positive": self.false_positive,
            "true_positive": self.true_positive,
            "false_negative": self.false_negative,
            "per_item": self.per_item,
        }


def code_metrics(reference: str, hypothesis: str) -> CodeMetrics:
    ref_codes = extract_codes(reference)
    hyp_codes = extract_codes(hypothesis)
    tp_codes = ref_codes & hyp_codes
    fp_codes = hyp_codes - ref_codes
    fn_codes = ref_codes - hyp_codes
    n_tp, n_fp, n_fn = len(tp_codes), len(fp_codes), len(fn_codes)
    precision = n_tp / (n_tp + n_fp) if (n_tp + n_fp) else 0.0
    recall = n_tp / (n_tp + n_fn) if (n_tp + n_fn) else 0.0
    # Per-item detail uses the with-year form for human display
    ref_with_yr = extract_codes_with_year(reference)
    hyp_with_yr = extract_codes_with_year(hypothesis)
    per_item: list[dict[str, Any]] = []
    base_codes = sorted({(c[0], c[1]) for c in (ref_codes | hyp_codes)},
                        key=lambda t: (t[0], t[1]))
    for fam, num in base_codes:
        ref_yrs = sorted({y for (f, n, y) in ref_with_yr if (f, n) == (fam, num) and y})
        hyp_yrs = sorted({y for (f, n, y) in hyp_with_yr if (f, n) == (fam, num) and y})
        in_ref = (fam, num) in ref_codes
        in_hyp = (fam, num) in hyp_codes
        if in_ref and in_hyp:
            verdict = "TP"
        elif in_hyp and not in_ref:
            verdict = "FP"
        elif in_ref and not in_hyp:
            verdict = "FN"
        else:
            verdict = "TN"
        per_item.append({
            "code": f"{fam.upper()} {num}",
            "ref_years": ref_yrs,
            "hyp_years": hyp_yrs,
            "year_match": (set(ref_yrs) == set(hyp_yrs)) if (ref_yrs or hyp_yrs) else None,
            "in_reference": in_ref,
            "in_hypothesis": in_hyp,
            "verdict": verdict,
        })
    return CodeMetrics(precision, recall, len(fp_codes), n_tp, n_fn, per_item)


# ─────────────────────────────────────────────────────────────────────────────
# BIM terms
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BIM_TERMS: tuple[str, ...] = (
    "钢结构", "螺栓连接", "焊缝", "高强螺栓", "抗滑移系数",
    "GB 50017", "GB 50205", "JGJ 99", "Q355", "Q460",
    "摩擦型", "承压型", "端距", "边距", "摩擦面",
    "扭矩系数", "初拧", "终拧", "火焰切割", "超声波探伤",
)


def _bim_norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    return "".join(c for c in s if unicodedata.category(c)[0] not in _PUNCT_CATS)


@dataclass
class BimTermMetrics:
    precision: float
    recall: float
    false_positive_detail: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "false_positive_detail": self.false_positive_detail,
        }


def bim_term_metrics(
    reference: str,
    hypothesis: str,
    terms: Iterable[str] = DEFAULT_BIM_TERMS,
) -> BimTermMetrics:
    ref_n = _bim_norm(reference)
    hyp_n = _bim_norm(hypothesis)
    n_tp = n_fp = n_fn = 0
    detail: list[dict[str, Any]] = []
    for t in terms:
        tn = _bim_norm(t)
        in_ref = bool(tn) and tn in ref_n
        in_hyp = bool(tn) and tn in hyp_n
        if in_ref and in_hyp:
            verdict = "TP"; n_tp += 1
        elif in_hyp and not in_ref:
            verdict = "FP"; n_fp += 1
        elif in_ref and not in_hyp:
            verdict = "FN"; n_fn += 1
        else:
            verdict = "TN"
        detail.append({"term": t, "in_reference": in_ref, "in_hypothesis": in_hyp, "verdict": verdict})
    precision = n_tp / (n_tp + n_fp) if (n_tp + n_fp) else 0.0
    recall = n_tp / (n_tp + n_fn) if (n_tp + n_fn) else 0.0
    return BimTermMetrics(precision, recall, detail)


# ─────────────────────────────────────────────────────────────────────────────
# Segment alignment (monotone, one-to-one)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class SegmentMetrics:
    n_ref: int
    n_hyp: int
    n_matched: int
    start_drift_p50_ms: float
    start_drift_p95_ms: float
    start_drift_p99_ms: float
    start_drift_max_ms: float
    end_drift_p50_ms: float
    end_drift_p95_ms: float
    end_drift_p99_ms: float
    end_drift_max_ms: float
    omission_rate: float
    extra_rate: float
    consecutive_repeat_rate: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_ref": self.n_ref,
            "n_hyp": self.n_hyp,
            "n_matched": self.n_matched,
            "start_drift_p50_ms": self.start_drift_p50_ms,
            "start_drift_p95_ms": self.start_drift_p95_ms,
            "start_drift_p99_ms": self.start_drift_p99_ms,
            "start_drift_max_ms": self.start_drift_max_ms,
            "end_drift_p50_ms": self.end_drift_p50_ms,
            "end_drift_p95_ms": self.end_drift_p95_ms,
            "end_drift_p99_ms": self.end_drift_p99_ms,
            "end_drift_max_ms": self.end_drift_max_ms,
            "omission_rate": self.omission_rate,
            "extra_rate": self.extra_rate,
            "consecutive_repeat_rate": self.consecutive_repeat_rate,
        }


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def _monotone_one_to_one(
    ref: Sequence[Segment], hyp: Sequence[Segment]
) -> list[tuple[int, int, float, float]]:
    """Minimum-cost monotone alignment with explicit omission/extra gaps."""
    n, m = len(ref), len(hyp)
    if not n or not m:
        return []
    durations = [max(1, s.end_ms - s.start_ms) for s in (*ref, *hyp)]
    gap_cost = max(1000.0, float(np.median(np.asarray(durations, dtype=np.float64))))
    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    prev: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        dp[i][0] = i * gap_cost
        prev[i][0] = (i - 1, 0, "skip_ref")
    for j in range(1, m + 1):
        dp[0][j] = j * gap_cost
        prev[0][j] = (0, j - 1, "skip_hyp")
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            r, h = ref[i - 1], hyp[j - 1]
            match_cost = abs(h.start_ms - r.start_ms) + abs(h.end_ms - r.end_ms)
            choices = (
                (dp[i - 1][j - 1] + match_cost, i - 1, j - 1, "match"),
                (dp[i - 1][j] + gap_cost, i - 1, j, "skip_ref"),
                (dp[i][j - 1] + gap_cost, i, j - 1, "skip_hyp"),
            )
            cost, pi, pj, action = min(choices, key=lambda x: x[0])
            dp[i][j] = cost
            prev[i][j] = (pi, pj, action)
    pairs: list[tuple[int, int, float, float]] = []
    i, j = n, m
    while i or j:
        step = prev[i][j]
        if step is None:
            break
        pi, pj, action = step
        if action == "match":
            r, h = ref[i - 1], hyp[j - 1]
            pairs.append((i - 1, j - 1,
                          abs(h.start_ms - r.start_ms),
                          abs(h.end_ms - r.end_ms)))
        i, j = pi, pj
    pairs.reverse()
    return pairs


def segment_metrics(reference: Sequence[Segment], hypothesis: Sequence[Segment]) -> SegmentMetrics:
    if not reference:
        return SegmentMetrics(0, len(hypothesis), 0, 0, 0, 0, 0, 0, 0, 0, 0,
                             0.0, 0.0, 0.0)
    if not hypothesis:
        return SegmentMetrics(len(reference), 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 0,
                             1.0, 0.0, 0.0)
    pairs = _monotone_one_to_one(reference, hypothesis)
    n_matched = len(pairs)
    starts = [d[2] for d in pairs]
    ends = [d[3] for d in pairs]
    matched_hyp_idxs = {d[1] for d in pairs}
    n_hyp = len(hypothesis)
    n_ref = len(reference)
    omission_rate = (n_ref - n_matched) / n_ref
    # extra_rate = hyp not matched to any ref
    extra_rate = (n_hyp - n_matched) / n_hyp
    # consecutive repeat: count adjacent hyp segments whose normalized text equals
    runs = 1
    n_runs_2plus = 0
    prev = _bim_norm(hypothesis[0].text)
    for h in hypothesis[1:]:
        n = _bim_norm(h.text)
        if n and n == prev:
            runs += 1
        else:
            if runs >= 2:
                n_runs_2plus += runs
            runs = 1
            prev = n
    if runs >= 2:
        n_runs_2plus += runs
    consecutive_repeat_rate = n_runs_2plus / n_hyp
    return SegmentMetrics(
        n_ref=n_ref, n_hyp=n_hyp, n_matched=n_matched,
        start_drift_p50_ms=_percentile(starts, 50),
        start_drift_p95_ms=_percentile(starts, 95),
        start_drift_p99_ms=_percentile(starts, 99),
        start_drift_max_ms=max(starts) if starts else 0.0,
        end_drift_p50_ms=_percentile(ends, 50),
        end_drift_p95_ms=_percentile(ends, 95),
        end_drift_p99_ms=_percentile(ends, 99),
        end_drift_max_ms=max(ends) if ends else 0.0,
        omission_rate=omission_rate,
        extra_rate=extra_rate,
        consecutive_repeat_rate=consecutive_repeat_rate,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RTF
# ─────────────────────────────────────────────────────────────────────────────


def rtf(audio_duration_s: float, inference_wallclock_s: float) -> float:
    """RTF = inference_wallclock_s / audio_duration_s. <1 means faster than realtime."""
    if audio_duration_s <= 0:
        return float("inf")
    return inference_wallclock_s / audio_duration_s


def realtime_speedup(audio_duration_s: float, inference_wallclock_s: float) -> float:
    """realtime_speedup = audio_duration_s / inference_wallclock_s. >1 means faster than realtime."""
    if inference_wallclock_s <= 0:
        return float("inf")
    return audio_duration_s / inference_wallclock_s
