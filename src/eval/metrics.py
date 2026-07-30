"""Retrieval metrics computed against EvalItem.expected_parent_ids.

All metrics are parent-id set-based: a retrieved parent is "relevant" iff
its parent_id is in `expected_parent_ids`. This matches the A:1 grading
choice — no string matching, no LLM judge, deterministic.

Multi-turn and no_answer items are excluded by the runner before metrics
are computed (they need different grading).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalEvalRow:
    """One graded retrieval result for one EvalItem."""
    item_id: str
    kind: str
    expected: list[str]
    retrieved: list[str]   # parent_ids in rank order
    hit_rank: int | None   # 1-based rank of first relevant parent, or None


def grade_one(expected: list[str], retrieved: list[str]) -> int | None:
    """Return 1-based rank of the first relevant parent, or None if absent."""
    expected_set = set(expected)
    for i, pid in enumerate(retrieved, 1):
        if pid in expected_set:
            return i
    return None


def grade_comparison(
    expected_sides: list[list[str]], retrieved: list[str], k: int
) -> dict:
    """Grade a comparison item: EVERY side must be hit within top-k.

    Unlike grade_one (any-hit), a comparison "compare A vs B" is only useful
    when both A's and B's parents are retrieved — otherwise the LLM sees one
    side and can't compare. So we require at least one hit per side.

    Returns a dict:
      - sides_total:   number of sides
      - sides_hit:     how many sides had >=1 parent in retrieved[:k]
      - both_hit:      True iff every side was hit (the headline metric)
      - side_ranks:    per-side best 1-based rank in retrieved (None if missed)
    """
    top = retrieved[:k]
    side_ranks: list[int | None] = []
    for side in expected_sides:
        side_set = set(side)
        rank = next((i for i, pid in enumerate(top, 1) if pid in side_set), None)
        side_ranks.append(rank)
    sides_total = len(expected_sides)
    sides_hit = sum(1 for r in side_ranks if r is not None)
    return {
        "sides_total": sides_total,
        "sides_hit": sides_hit,
        "both_hit": sides_total > 0 and sides_hit == sides_total,
        "side_ranks": side_ranks,
    }


def recall_at_k(rows: list[RetrievalEvalRow], k: int) -> float:
    """Fraction of items where any expected parent appeared in top-k."""
    if not rows:
        return 0.0
    hits = sum(
        1 for r in rows if r.hit_rank is not None and r.hit_rank <= k
    )
    return hits / len(rows)


def mrr_at_k(rows: list[RetrievalEvalRow], k: int) -> float:
    """Mean Reciprocal Rank at k. Misses (or hits past k) contribute 0."""
    if not rows:
        return 0.0
    total = 0.0
    for r in rows:
        if r.hit_rank is not None and r.hit_rank <= k:
            total += 1.0 / r.hit_rank
    return total / len(rows)


def summarize(rows: list[RetrievalEvalRow]) -> dict[str, float]:
    """Standard summary block: recall@5/20, MRR@10, plus per-kind recall@5."""
    overall = {
        "n": len(rows),
        "recall@5": recall_at_k(rows, 5),
        "recall@20": recall_at_k(rows, 20),
        "mrr@10": mrr_at_k(rows, 10),
    }
    by_kind: dict[str, list[RetrievalEvalRow]] = {}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)
    for kind, kind_rows in by_kind.items():
        overall[f"recall@5[{kind}]"] = recall_at_k(kind_rows, 5)
        overall[f"n[{kind}]"] = len(kind_rows)
    return overall
