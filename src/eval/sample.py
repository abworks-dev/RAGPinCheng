"""Weighted sampling from parents.sqlite for the eval golden set.

Produces `sampled_parents.json`: a list of parent records bucketed by
question-kind. A separate synthesizer (run via the Agent tool, not an API
key) reads this file and generates Q/A drafts per kind.

Sampling rules (Phase-1 rebuild: 规范/标准/图集 only, ~85 retrieval items):
- factual (40):       prose-only parents, length-weighted so tiny header
                      stubs don't dominate.
- table_formula (20): parents whose text contains an HTML table, pipe table,
                      or $$...$$ block.
- code_lookup (25):   parents whose text matches a standard-code pattern
                      (GB/JGJ/DBJ/CECS/GB-T) followed by digits — our
                      strongest material (abundant GB standards).
- transcript (0):     excluded in Phase 1 (company-process phase covers it).

Phase-1 filters (both on by default): only 设计规范 + 客户标准 categories
(ALLOWED_CATEGORIES); drop image-link / table-scaffolding debris blocks
(_is_noise_parent). Pass allowed_categories=None / apply_noise_filter=False
to restore legacy behavior.

multi_turn and no_answer are NOT sampled here — the user writes those by
hand directly into golden.jsonl.

Sampling is deterministic given a seed. Re-running with the same seed
returns the same parents so reviewers can rerun synthesis if needed.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import PARENTS_DB

# Reuse the same regexes that chunk.py uses, so what we sample as a
# table/formula parent matches what the chunker treats as one.
HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
PIPE_TABLE_RE = re.compile(r"(\n\|[^\n]*\|(?:\n\|[^\n]*\|)+)", re.MULTILINE)
FORMULA_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)

# Standard-code identifier: GB / GB/T / JGJ / DBJ / CECS followed by digits.
# Matches "GB 50017", "GB/T 50018-2002", "JGJ 99", etc.
CODE_RE = re.compile(r"\b(?:GB(?:\s*/\s*T)?|JGJ|DBJ|CECS)\s*\d+", re.IGNORECASE)

# Floor on parent text length — sub-100-char parents are usually heading
# stubs, ToC fragments, or page-number debris. They generate poor Qs.
MIN_PARENT_CHARS = 200

# Phase-1 golden-set rebuild: only sample from "规范/标准/图集" categories.
# Company-internal process docs (BIM modeling, MEP, etc.) are excluded from
# Phase 1 — they need a different question type and will be covered in a
# later project phase.
ALLOWED_CATEGORIES = frozenset({"设计规范", "客户标准"})

# Reject a parent block when more than this fraction of its text is made up
# of image-markdown links (e.g. `![](images/xxx.jpg)`). These are OCR/PDF
# rendering artifacts — they contain no human-readable knowledge.
MAX_IMAGE_LINK_RATIO = 0.15

# Reject a parent block when ≥ N consecutive lines are all table-header
# content (starts with `<td>`, `<th>`, or a pipe-table delimiter). These
# are fragmented table-scaffolding blocks, not standalone knowledge.
MAX_CONSECUTIVE_TABLE_HEADER_LINES = 8

# Regex to detect image-markdown links: `![]( ... )`
IMAGE_LINK_RE = re.compile(r"!\[.*?\]\(.*?\)")

DEFAULT_QUOTAS: dict[str, int] = {
    "factual": 40,         # Phase 1: fewer, stricter (only prose from 规范+客户标准)
    "table_formula": 20,   # tables in standards/client docs
    "code_lookup": 25,     # our strongest material — abundant GB standards
    "transcript": 0,       # Phase 1: excluded (belongs to company-process phase)
}


@dataclass
class ParentRow:
    parent_id: str
    doc_title: str
    section_path: str
    doc_type: str
    category: str
    text: str
    start_time: str | None
    text_len: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "doc_title": self.doc_title,
            "section_path": self.section_path,
            "doc_type": self.doc_type,
            "category": self.category,
            "start_time": self.start_time,
            "text_len": self.text_len,
            "text": self.text,
        }


def _load_parents(db_path: Path = PARENTS_DB) -> list[ParentRow]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT parent_id, doc_title, section_path, doc_type, category, "
            "text, start_time FROM parents"
        ).fetchall()
    finally:
        conn.close()
    out: list[ParentRow] = []
    for pid, title, sec, dt, cat, text, st in rows:
        text = text or ""
        out.append(ParentRow(
            parent_id=pid,
            doc_title=title or "",
            section_path=sec or "",
            doc_type=dt or "pdf",
            category=cat or "",
            text=text,
            start_time=st,
            text_len=len(text),
        ))
    return out


def _has_table_or_formula(text: str) -> bool:
    return bool(
        HTML_TABLE_RE.search(text)
        or PIPE_TABLE_RE.search(text)
        or FORMULA_RE.search(text)
    )


def _has_code(text: str) -> bool:
    return bool(CODE_RE.search(text))


def _image_link_ratio(text: str) -> float:
    """Fraction of the text length taken up by image-markdown links."""
    if not text:
        return 0.0
    img_chars = sum(len(m.group(0)) for m in IMAGE_LINK_RE.finditer(text))
    return img_chars / len(text)


def _is_table_header_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # HTML table scaffolding, or a markdown pipe-table row.
    return (
        s.startswith("<td")
        or s.startswith("<th")
        or s.startswith("<tr")
        or s.startswith("<table")
        or (s.startswith("|") and s.endswith("|"))
    )


def _max_consecutive_table_header_lines(text: str) -> int:
    best = cur = 0
    for line in text.splitlines():
        if _is_table_header_line(line):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _is_noise_parent(text: str) -> bool:
    """True if the parent is rendering/OCR debris rather than knowledge.

    Filters the noise patterns observed in the Phase-1 sampling preview:
      - blocks dominated by image-markdown links (化粪池图集 `![](images/…)`);
      - long runs of pure table-scaffolding rows (signature/index blocks,
        header-only fragments like the 图集 审核/校对 boxes).
    Length is handled separately by MIN_PARENT_CHARS. This is intentionally
    conservative — it targets clearly-unusable blocks, not merely short ones.
    """
    if _image_link_ratio(text) > MAX_IMAGE_LINK_RATIO:
        return True
    if _max_consecutive_table_header_lines(text) > MAX_CONSECUTIVE_TABLE_HEADER_LINES:
        return True
    return False


def _weighted_sample(
    rng: random.Random,
    candidates: list[ParentRow],
    k: int,
    weight_fn=lambda p: 1.0,
) -> list[ParentRow]:
    """Sample k distinct parents weighted by `weight_fn`, no replacement."""
    if not candidates or k <= 0:
        return []
    k = min(k, len(candidates))
    weights = [max(weight_fn(p), 1e-9) for p in candidates]
    # rng.sample doesn't support weights; do weighted-without-replacement
    # via the classic "Efraimidis-Spirakis" key trick.
    keys = [(rng.random() ** (1.0 / w), p) for w, p in zip(weights, candidates)]
    keys.sort(key=lambda kp: kp[0], reverse=True)
    return [p for _, p in keys[:k]]


def _spread_across_docs(
    rng: random.Random, candidates: list[ParentRow], k: int
) -> list[ParentRow]:
    """Sample k parents, biased to spread across distinct doc_titles.

    Strategy: bucket by doc_title, round-robin pick one from each bucket
    in random order, repeat until k filled or pool exhausted.
    """
    if not candidates or k <= 0:
        return []
    buckets: dict[str, list[ParentRow]] = {}
    for p in candidates:
        buckets.setdefault(p.doc_title, []).append(p)
    for lst in buckets.values():
        rng.shuffle(lst)
    titles = list(buckets.keys())
    rng.shuffle(titles)
    out: list[ParentRow] = []
    while titles and len(out) < k:
        next_titles: list[str] = []
        for t in titles:
            if buckets[t]:
                out.append(buckets[t].pop())
                if len(out) >= k:
                    break
            if buckets[t]:
                next_titles.append(t)
        titles = next_titles
    return out


def sample_parents(
    seed: int = 42,
    quotas: dict[str, int] | None = None,
    allowed_categories: frozenset[str] | None = ALLOWED_CATEGORIES,
    apply_noise_filter: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Return a dict of `{kind: [parent_payload, ...]}`, ready to dump as JSON.

    Buckets are disjoint by parent_id — a parent picked for table_formula
    won't reappear in factual or code_lookup. Order of allocation:
    transcript → table_formula → code_lookup → factual (most-restrictive
    pools first so they don't starve).

    Phase-1 filters (both defaulted on for the rebuild):
      - allowed_categories: keep only parents whose category is in this set
        (设计规范 + 客户标准). Pass None to disable (legacy behavior).
      - apply_noise_filter: drop image-link-dominated / table-scaffolding
        debris blocks (see _is_noise_parent). Pass False to disable.
    """
    quotas = quotas or DEFAULT_QUOTAS
    rng = random.Random(seed)
    all_parents = [p for p in _load_parents() if p.text_len >= MIN_PARENT_CHARS]
    if allowed_categories is not None:
        all_parents = [p for p in all_parents if p.category in allowed_categories]
    if apply_noise_filter:
        all_parents = [p for p in all_parents if not _is_noise_parent(p.text)]

    taken: set[str] = set()

    def _avail(pool: list[ParentRow]) -> list[ParentRow]:
        return [p for p in pool if p.parent_id not in taken]

    # 1) Transcripts — spread across distinct transcript files.
    transcripts = [p for p in all_parents if p.doc_type == "transcript"]
    transcript_pick = _spread_across_docs(rng, transcripts, quotas["transcript"])
    for p in transcript_pick:
        taken.add(p.parent_id)

    # 2) Table/formula — PDF only; transcripts are flat prose.
    pdf_pool = [p for p in all_parents if p.doc_type == "pdf"]
    tf_candidates = _avail([p for p in pdf_pool if _has_table_or_formula(p.text)])
    tf_pick = _weighted_sample(
        rng, tf_candidates, quotas["table_formula"],
        weight_fn=lambda p: min(p.text_len, 4000),
    )
    for p in tf_pick:
        taken.add(p.parent_id)

    # 3) Code-lookup — parents that mention a standard code identifier.
    code_candidates = _avail([p for p in pdf_pool if _has_code(p.text)])
    code_pick = _weighted_sample(
        rng, code_candidates, quotas["code_lookup"],
        weight_fn=lambda p: 1.0,
    )
    for p in code_pick:
        taken.add(p.parent_id)

    # 4) Factual — remaining PDF prose, length-weighted (cap at 4k chars so
    #    a single giant parent doesn't dominate).
    factual_candidates = _avail([
        p for p in pdf_pool if not _has_table_or_formula(p.text)
    ])
    factual_pick = _weighted_sample(
        rng, factual_candidates, quotas["factual"],
        weight_fn=lambda p: min(p.text_len, 4000),
    )

    return {
        "transcript": [p.to_payload() for p in transcript_pick],
        "table_formula": [p.to_payload() for p in tf_pick],
        "code_lookup": [p.to_payload() for p in code_pick],
        "factual": [p.to_payload() for p in factual_pick],
    }


def write_sampled(
    out_path: Path,
    seed: int = 42,
    quotas: dict[str, int] | None = None,
    allowed_categories: frozenset[str] | None = ALLOWED_CATEGORIES,
    apply_noise_filter: bool = True,
) -> dict[str, int]:
    """Sample and write JSON. Returns a per-kind count summary."""
    buckets = sample_parents(
        seed=seed,
        quotas=quotas,
        allowed_categories=allowed_categories,
        apply_noise_filter=apply_noise_filter,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seed": seed,
        "allowed_categories": sorted(allowed_categories) if allowed_categories else None,
        "noise_filter": apply_noise_filter,
        "buckets": buckets,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {k: len(v) for k, v in buckets.items()}
