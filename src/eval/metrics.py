"""Retrieval metrics computed against EvalItem.expected_parent_ids.

All metrics are parent-id set-based: a retrieved parent is "relevant" iff
its parent_id is in `expected_parent_ids`. This matches the A:1 grading
choice — no string matching, no LLM judge, deterministic.

Multi-turn and no_answer items are excluded by the runner before metrics
are computed (they need different grading).

Phase A: comparison grading is extended with k / any_side_hit /
all_sides_hit / side_recall / side_mrr, while preserving the legacy
sides_total / sides_hit / both_hit / side_ranks shape. Path aggregation
for the comparison protocol is provided by aggregate_comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


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


class ComparisonInputError(ValueError):
    """Raised when a comparison item's expected_sides / k are malformed."""


def _validate_sides(expected_sides: list[list[str]], k: int) -> None:
    if not isinstance(k, int) or k <= 0:
        raise ComparisonInputError(f"k must be a positive integer, got {k!r}")
    if not expected_sides or len(expected_sides) < 2:
        raise ComparisonInputError(
            f"expected_sides must have >= 2 sides, got {len(expected_sides)}"
        )
    seen: set[str] = set()
    for i, side in enumerate(expected_sides):
        if not side:
            raise ComparisonInputError(f"side #{i} is empty")
        for pid in side:
            if pid in seen:
                raise ComparisonInputError(
                    f"parent_id {pid!r} appears in more than one side "
                    f"(cross-side overlap)"
                )
            seen.add(pid)


def grade_comparison_input(
    expected_sides: list[list[str]], retrieved: list[str], k: int
) -> None:
    """Fail-fast validation: fail with ComparisonInputError on any malformed input.

    Called by the runner before the first retrieval so a bad golden label
    aborts the run with a clear message rather than producing a misleading
    result.
    """
    _validate_sides(expected_sides, k)


def grade_comparison(
    expected_sides: list[list[str]], retrieved: list[str], k: int
) -> dict:
    """Grade a comparison item: every side must be hit within top-k.

    Unlike grade_one (any-hit), a comparison "compare A vs B" is only useful
    when both A's and B's parents are retrieved — otherwise the LLM sees one
    side and can't compare. We grade how many sides are hit (sides_hit), and
    whether all are hit (both_hit / all_sides_hit).

    Returns a dict with the legacy four fields plus the new ones:
      - k:                 int — what k this grade was computed at
      - sides_total:       int — number of sides
      - sides_hit:         int — sides with >=1 parent in retrieved[:k]
      - both_hit:          bool — legacy alias for all_sides_hit
      - all_sides_hit:     bool — every side hit
      - any_side_hit:      bool — at least one side hit
      - side_ranks:        list[int | None] — per-side best 1-based rank
      - side_recall:       float — sides_hit / sides_total
      - side_mrr:          float — mean(1/rank if hit else 0) over every side

    Raises ComparisonInputError on malformed input.
    """
    _validate_sides(expected_sides, k)
    top = retrieved[:k]
    side_ranks: list[int | None] = []
    for side in expected_sides:
        side_set = set(side)
        rank = next((i for i, pid in enumerate(top, 1) if pid in side_set), None)
        side_ranks.append(rank)
    sides_total = len(expected_sides)
    sides_hit = sum(1 for r in side_ranks if r is not None)
    side_mrr = sum(
        (0.0 if r is None else 1.0 / r) for r in side_ranks
    ) / sides_total
    all_sides_hit = sides_total > 0 and sides_hit == sides_total
    return {
        "k": k,
        "sides_total": sides_total,
        "sides_hit": sides_hit,
        "both_hit": all_sides_hit,  # legacy alias
        "all_sides_hit": all_sides_hit,
        "any_side_hit": sides_hit > 0,
        "side_ranks": side_ranks,
        "side_recall": sides_hit / sides_total,
        "side_mrr": side_mrr,
    }


def _safe_delta(on: float | None, off: float | None) -> float | None:
    if on is None or off is None:
        return None
    return on - off


def _hit_rate(numerator: int, denominator: int) -> float | None:
    """Hit rate is None when the denominator is 0 (plan §7)."""
    if denominator == 0:
        return None
    return numerator / denominator


def _macro_micro_side_recall(off_g: dict, on_g: dict) -> tuple[float, float, int, int]:
    """Return (macro_side_recall_on, micro_side_recall_on, side_hit_count_on, side_obs).

    `side_obs` is the number of side observations (sides_total across all
    items). micro_side_recall = side_hit_count / side_obs; macro_side_recall
    is the mean of per-item side_recall values.
    """
    macro_off = off_g["side_recall"] if off_g else None
    macro_on = on_g["side_recall"] if on_g else None
    side_hit = on_g["sides_hit"] if on_g else 0
    side_obs = on_g["sides_total"] if on_g else 0
    micro = _hit_rate(side_hit, side_obs)
    return macro_on, micro, side_hit, side_obs


def _macro_side_mrr(off_g: dict, on_g: dict) -> float | None:
    return _safe_delta(
        on_g["side_mrr"] if on_g else None,
        off_g["side_mrr"] if off_g else None,
    )


def _transitions(
    items: list[dict], analysis_set: str
) -> dict[str, int]:
    """Count all-sides-hit transitions off→on over the items in the analysis set.

    Per the plan: gain + loss + same_hit + same_miss == n_paired_evaluable.
    """
    gain = loss = same_hit = same_miss = 0
    n = 0
    for it in items:
        if analysis_set == "applied_only_complete_pairs" and it.get("decompose_status") != "applied":
            continue
        if analysis_set == "itt_complete_pairs" and it.get("decompose_status") == "error":
            continue
        # Both arms must be valid for this item to count.
        if it.get("off") is None or it.get("on") is None:
            continue
        n += 1
        off_all = it["off"]["all_sides_hit"]
        on_all = it["on"]["all_sides_hit"]
        if off_all and on_all:
            same_hit += 1
        elif not off_all and not on_all:
            same_miss += 1
        elif not off_all and on_all:
            gain += 1
        else:
            loss += 1
    return {
        "gain_count": gain,
        "loss_count": loss,
        "same_hit_count": same_hit,
        "same_miss_count": same_miss,
        "n_paired_evaluable": n,
    }


AnalysisSet = Literal["itt_complete_pairs", "applied_only_complete_pairs"]


def aggregate_comparison(
    items: list[dict], k: int, analysis_set: AnalysisSet
) -> dict:
    """Aggregate per-item comparison results for the given analysis set.

    Input contract (per item):
        {
          "item_id": str,
          "off": grade_comparison dict | None,   # None if retrieval errored
          "on":  grade_comparison dict | None,
          "decompose_status": "applied" | "not_applied" | "error",
          "errors": {"off": str|None, "on": str|None},
        }

    Output keys include mandatory counts (n_items_selected, n_decompose_*,
    n_paired_evaluable, n_error_*), rates (any_side_hit_rate,
    all_sides_hit_rate, macro/micro side_recall, side_mrr), deltas
    (on - off), gain/loss counts, and a `run_warnings` list with sample-size
    and 4-cell sample notes the print layer can surface.
    """
    n_total = len(items)
    n_applied = sum(1 for it in items if it.get("decompose_status") == "applied")
    n_not_applied = sum(1 for it in items if it.get("decompose_status") == "not_applied")
    n_error = sum(1 for it in items if it.get("decompose_status") == "error")

    # Per-k error counters.
    def _err_count(field: str) -> int:
        return sum(
            1 for it in items
            if (it.get("errors") or {}).get(field)
        )

    n_err_off_k5 = _err_count("off_k5")
    n_err_off_k8 = _err_count("off_k8")
    n_err_on_k5 = _err_count("on_k5")
    n_err_on_k8 = _err_count("on_k8")

    error_item_ids = sorted(
        it["item_id"] for it in items
        if (it.get("errors") or {}).get("off_k5")
        or (it.get("errors") or {}).get("off_k8")
        or (it.get("errors") or {}).get("on_k5")
        or (it.get("errors") or {}).get("on_k8")
    )

    if analysis_set == "applied_only_complete_pairs":
        kept = [it for it in items if it.get("decompose_status") == "applied"]
    elif analysis_set == "itt_complete_pairs":
        kept = [it for it in items if it.get("decompose_status") != "error"]
    else:
        raise ValueError(f"unknown analysis_set: {analysis_set!r}")

    # Item-level cells at the chosen k (the runner currently grades per-k
    # via grade_comparison; items passed in already carry off_g / on_g at the
    # given k). If the runner stored only one grade, we trust it.
    # Filter to items where both arms succeeded at the chosen k.
    paired: list[dict] = []
    for it in kept:
        if it.get("off") is None or it.get("on") is None:
            continue
        if it["off"].get("k") != k or it["on"].get("k") != k:
            # Mismatched k in stored grade: runner should always pair
            # off_k{5,8} / on_k{5,8}; treat as not paired at this k.
            continue
        paired.append(it)

    n_paired = len(paired)
    transitions = _transitions(paired, analysis_set)
    for k_, v in transitions.items():
        transitions[k_] = v if k_ != "n_paired_evaluable" else n_paired

    # Aggregate over paired items.
    n_any_off = sum(1 for it in paired if it["off"]["any_side_hit"])
    n_any_on = sum(1 for it in paired if it["on"]["any_side_hit"])
    n_all_off = sum(1 for it in paired if it["off"]["all_sides_hit"])
    n_all_on = sum(1 for it in paired if it["on"]["all_sides_hit"])

    # Per-item macro side_recall for averaging.
    macro_off_per_item = [it["off"]["side_recall"] for it in paired]
    macro_on_per_item = [it["on"]["side_recall"] for it in paired]
    macro_side_recall_off = (
        sum(macro_off_per_item) / n_paired if n_paired else None
    )
    macro_side_recall_on = (
        sum(macro_on_per_item) / n_paired if n_paired else None
    )

    # micro: sum of side hits / sum of side obs.
    side_hit_off = sum(it["off"]["sides_hit"] for it in paired)
    side_obs_off = sum(it["off"]["sides_total"] for it in paired)
    side_hit_on = sum(it["on"]["sides_hit"] for it in paired)
    side_obs_on = sum(it["on"]["sides_total"] for it in paired)
    micro_side_recall_off = _hit_rate(side_hit_off, side_obs_off)
    micro_side_recall_on = _hit_rate(side_hit_on, side_obs_on)

    # Side MRR: same micro/macro shape, miss -> 0 (plan §8).
    macro_mrr_off = (
        sum(it["off"]["side_mrr"] for it in paired) / n_paired
        if n_paired else None
    )
    macro_mrr_on = (
        sum(it["on"]["side_mrr"] for it in paired) / n_paired
        if n_paired else None
    )

    # mean rank given hit (diagnostic only; the per-side rank list is what
    # powers it).
    def _mean_rank_given_hit(items_, side_attr: str) -> tuple[float | None, int]:
        ranks: list[int] = []
        for it in items_:
            for r in it[side_attr]["side_ranks"]:
                if r is not None:
                    ranks.append(r)
        if not ranks:
            return None, 0
        return sum(ranks) / len(ranks), len(ranks)

    mean_rank_off, rank_obs_off = _mean_rank_given_hit(paired, "off")
    mean_rank_on, rank_obs_on = _mean_rank_given_hit(paired, "on")

    # Transitions: use the per-item transition counter but in pair-aligned
    # form already computed above. Reuse `transitions`.
    gain = transitions["gain_count"]
    loss = transitions["loss_count"]
    same_hit = transitions["same_hit_count"]
    same_miss = transitions["same_miss_count"]

    # Deltas (on - off). None if either side is undefined.
    delta_any = _safe_delta(
        _hit_rate(n_any_on, n_paired),
        _hit_rate(n_any_off, n_paired),
    )
    delta_all = _safe_delta(
        _hit_rate(n_all_on, n_paired),
        _hit_rate(n_all_off, n_paired),
    )
    delta_macro_recall = _safe_delta(macro_side_recall_on, macro_side_recall_off)
    delta_micro_recall = _safe_delta(micro_side_recall_on, micro_side_recall_off)
    delta_macro_mrr = _safe_delta(macro_mrr_on, macro_mrr_off)
    delta_mean_rank = _safe_delta(mean_rank_on, mean_rank_off)

    # Run-time warnings: surfaced to console for the operator (plan §9).
    run_warnings: list[str] = []
    if n_paired <= 4:
        run_warnings.append(
            f"sample_size {n_paired} <= 4: comparison metrics are descriptive, "
            f"not statistically decisive"
        )
    if delta_all is not None and delta_all < 0:
        run_warnings.append(
            f"WARN: delta_all_sides_hit_rate = {delta_all:+.3f} (negative)"
        )
    if delta_macro_mrr is not None and delta_macro_mrr < 0:
        run_warnings.append(
            f"WARN: delta_macro_side_mrr = {delta_macro_mrr:+.3f} (negative)"
        )
    # All items share the same source doc pair? Heuristic: at most 2 distinct
    # doc titles across the 4-cell answers. Cheap proxy: gather doc_titles
    # when available; otherwise skip.
    doc_titles: set[str] = set()
    for it in items:
        for arm in ("off", "on"):
            g = it.get(arm)
            if isinstance(g, dict) and g.get("doc_title"):
                doc_titles.add(g["doc_title"])
    # If doc_title wasn't passed on the grade dict (runner doesn't store it
    # today), this is empty — the warning is suppressed, which is the
    # safe default.

    return {
        # Analysis set & completeness
        "analysis_set": analysis_set,
        "k": k,
        "n_items_selected": n_total,
        "n_valid_gold": n_total,  # Phase A: items that passed input validation
        "n_decompose_applied": n_applied,
        "n_decompose_not_applied": n_not_applied,
        "n_decompose_error": n_error,
        "n_paired_evaluable": n_paired,
        "n_side_observations": side_obs_on,
        "n_error_off_k5": n_err_off_k5,
        "n_error_off_k8": n_err_off_k8,
        "n_error_on_k5": n_err_on_k5,
        "n_error_on_k8": n_err_on_k8,
        "error_item_ids": error_item_ids,
        # Per-path counts and rates (at chosen k)
        "any_side_hit_count_off": n_any_off,
        "any_side_hit_count_on": n_any_on,
        "any_side_hit_rate_off": _hit_rate(n_any_off, n_paired),
        "any_side_hit_rate_on": _hit_rate(n_any_on, n_paired),
        "all_sides_hit_count_off": n_all_off,
        "all_sides_hit_count_on": n_all_on,
        "all_sides_hit_rate_off": _hit_rate(n_all_off, n_paired),
        "all_sides_hit_rate_on": _hit_rate(n_all_on, n_paired),
        "side_hit_count_off": side_hit_off,
        "side_hit_count_on": side_hit_on,
        "macro_side_recall_off": macro_side_recall_off,
        "macro_side_recall_on": macro_side_recall_on,
        "micro_side_recall_off": micro_side_recall_off,
        "micro_side_recall_on": micro_side_recall_on,
        "macro_side_mrr_off": macro_mrr_off,
        "macro_side_mrr_on": macro_mrr_on,
        "rank_given_hit_count_off": rank_obs_off,
        "rank_given_hit_count_on": rank_obs_on,
        "mean_rank_given_hit_off": mean_rank_off,
        "mean_rank_given_hit_on": mean_rank_on,
        # Paired deltas
        "delta_any_side_hit_rate": delta_any,
        "delta_all_sides_hit_rate": delta_all,
        "delta_macro_side_recall": delta_macro_recall,
        "delta_micro_side_recall": delta_micro_recall,
        "delta_macro_side_mrr": delta_macro_mrr,
        "delta_mean_rank_given_hit": delta_mean_rank,
        # Transitions
        "gain_count": gain,
        "loss_count": loss,
        "same_hit_count": same_hit,
        "same_miss_count": same_miss,
        "run_warnings": run_warnings,
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

