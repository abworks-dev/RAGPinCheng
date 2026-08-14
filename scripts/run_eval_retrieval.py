"""Run the eval golden set through ChatSession and compute metrics.

What gets measured:
  - Retrieval-graded kinds (factual / table_formula / code_lookup /
    transcript / multi_turn): Recall@1, Recall@5, MRR@5 against
    `expected_parent_ids`, computed over `TurnResult.final_sources`.
  - no_answer items: answer text must contain the refusal phrase
    "未找到相关内容" (the contract enforced by prompts/answer_system.md,
    rule 1). The prompt no longer prepends "资料中" nor appends a
    source-list footer, so we match the phrase as a substring rather
    than an exact string.
  - comparison items (Phase A): 2x2 in-process retrieval grid
    (off_k5, off_k8, on_k5, on_k8) for each item, with `maybe_decompose`
    called exactly once and its result reused for the two ON cells.
    `not_applied` triggers a fallback_copy of the corresponding OFF cell;
    errors never masquerade as miss. Aggregation runs in two analysis
    sets (itt_complete_pairs primary, applied_only_complete_pairs
    secondary). All output is fixed-declared as
    `decision_eligible=false` (Phase A: retrieval-only mechanism).

Multi-turn pairs share a single ChatSession instance so turn-2 exercises
the rewriter + carry-forward. We grade both turns; turn-2 is the one
that actually tests the multi-turn machinery, turn-1 is recorded as a
sanity baseline.

Output:
  - Console summary table by kind + comparison 2x2 section.
  - Per-item JSONL at src/eval/runs/run_<ISO>.jsonl for inspection
    (additive fields, item_id preserved on every line).
  - Sidecar at src/eval/runs/run_<ISO>.summary.json with run-level
    metadata (config snapshot, fingerprint status, ITT/applied-only
    aggregates, error counts, fixed Phase A disclaimer).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    DECOMPOSE_FINAL_TOP_K,
    FINAL_TOP_K,
    LLM_REWRITE_MODEL,
    PARENTS_DB,
    RERANKER_MODEL,
    RERANK_PROVIDER,
    ZHIPU_API_KEY,
)
from src.eval.fingerprint import compare, compute_fingerprint, load_baseline
from src.eval.io import load_jsonl
from src.eval.metrics import (
    ComparisonInputError,
    RetrievalEvalRow,
    aggregate_comparison,
    grade_comparison,
    grade_comparison_input,
    grade_one,
    summarize,
)
from src.eval.types import EvalItem
from src.retrieve import retrieve, retrieve_multi
from src.session import ChatSession

GOLDEN = Path(__file__).resolve().parent.parent / "src" / "eval" / "golden.jsonl"
RUNS_DIR = Path(__file__).resolve().parent.parent / "src" / "eval" / "runs"

NO_ANSWER_TEXT = "未找到相关内容"

# Phase A: experiment phase declaration is hard-coded. The protocol
# deliberately does not produce decision-grade output.
EXPERIMENT_PHASE = "A_offline_retrieval_mechanism"
PHASE_A_DISCLAIMER = (
    "PHASE A: in-process retrieval-only comparison. NOT equivalent to "
    "QUERY_DECOMPOSE_ENABLED in production. Does NOT exercise rewrite / guard / "
    "carry / context packaging / answer quality / citation support / "
    "latency / cost / real traffic. decision_eligible = false."
)

PHASE_A_FOOTER_KEYS = {
    "experiment_phase", "production_toggle_equivalent",
    "context_coverage_evaluated", "answer_quality_evaluated",
    "decision_eligible",
}


def _git_sha_and_dirty() -> tuple[str | None, bool]:
    """Return (sha, dirty) best-effort. None on failure."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return None, False
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return sha, False
    return sha, bool(status)


def _summary_path(jsonl_path: Path) -> Path:
    return jsonl_path.with_suffix("").with_name(jsonl_path.stem + ".summary.json")


def _load_live_parent_ids() -> list[str]:
    """Read the live parent_id set straight from parents.sqlite. No Qdrant."""
    conn = sqlite3.connect(PARENTS_DB)
    try:
        rows = conn.execute("SELECT parent_id FROM parents").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def _check_staleness(strict: bool) -> tuple[str, str, dict | None, dict | None]:
    """Compare live index fingerprint to the frozen baseline; warn or fail.

    The baseline lives at src/eval/golden.fingerprint.json. The live set is
    read from parents.sqlite directly (no Qdrant, no model). On drift we
    print a clearly-marked warning and (under --strict-staleness) exit 2.
    We never block by default — a stale golden set is still useful to grade
    (the resulting 0/100 may itself be the signal), but it should be hard
    to miss.

    Returns (status, reason, baseline_dict, live_dict) for the summary
    sidecar. status is one of: 'match' | 'mismatch' | 'unavailable'.
    reason is one of: 'none' | 'missing_baseline' | 'compute_error'.
    """
    baseline = load_baseline()
    if baseline is None:
        print("[eval] no baseline fingerprint (src/eval/golden.fingerprint.json) — "
              "skip staleness check. Run `relabel_golden.py fingerprint --freeze` "
              "after a golden-set rebuild to create one.")
        return ("unavailable", "missing_baseline", None, None)
    try:
        live = compute_fingerprint(_load_live_parent_ids())
    except Exception as exc:  # noqa: BLE001 — never let a check crash the run
        print(f"[eval] [warn] could not compute live fingerprint: {exc}")
        return ("unavailable", "compute_error", baseline, None)
    diff = compare(live, baseline)
    if diff["match"]:
        print(f"[eval] staleness OK (parent_count={live['parent_count']}, "
              f"frozen_at={baseline.get('frozen_at', '?')})")
        return ("match", "none", baseline, live)
    print()
    print("!" * 64)
    print("!!  WARNING: golden-set fingerprint MISMATCH")
    print("!")
    print(f"!  live      : parent_count={live['parent_count']} "
          f"sha256={live['parent_id_sha256']}")
    print(f"!  baseline  : parent_count={baseline.get('parent_count')} "
          f"sha256={baseline.get('parent_id_sha256')}  "
          f"frozen_at={baseline.get('frozen_at', '?')}")
    cd = diff.get("count_delta")
    if cd is not None:
        print(f"!  count_delta        : {cd:+d} parents")
    print(f"!  sha256_changed    : {diff.get('sha256_changed')}")
    print("!  → The golden set may have gone stale. expected_parent_ids no longer")
    print("!    match the live index. Results below may be misleading (R@K ≈ 0,")
    print("!    not because retrieval broke, but because the labels are out of date).")
    print("!  Action: rebuild the golden set, then `relabel_golden.py fingerprint")
    print("!  --freeze` to refresh the baseline. See docs/golden-set-staleness-guard.md.")
    print("!" * 64)
    print()
    if strict:
        raise SystemExit(2)
    return ("mismatch", "none", baseline, live)


def _ask(session: ChatSession, question: str) -> tuple[list[str], str, dict]:
    """Run one turn. Returns (retrieved_parent_ids, answer_text, timings)."""
    result = session.ask(question)
    pids = [p.parent_id for p in result.final_sources]
    return pids, result.answer_text, dict(result.timings)


def _score_snapshot(parents) -> dict:
    ranked = sorted(parents, key=lambda p: p.score, reverse=True)
    top1 = float(ranked[0].score) if ranked else None
    top2 = float(ranked[1].score) if len(ranked) > 1 else None
    return {
        "source_count": len(ranked), "top1_score": top1, "top2_score": top2,
        "score_margin": top1 - top2 if top1 is not None and top2 is not None else None,
        "top1_rrf": float(ranked[0].rrf_score) if ranked else None,
        "decomposition_applied": any(p.subquery_idx is not None for p in ranked),
        "rerank_provider": RERANK_PROVIDER, "reranker_model": RERANKER_MODEL,
    }


def _grade_no_answer(answer_text: str) -> bool:
    # Two runtime no-answer paths exist and substring match covers both:
    #   1. LLM path (sources retrieved, no answer found): prompts/
    #      answer_system.md rule 1 → "未找到相关内容" (no "资料中" prefix,
    #      no source footer — rule 9 forbids it).
    #   2. No-source escape hatch (retrieval returns nothing): the hardcoded
    #      fallback in src/session.py → "资料中未找到相关内容。", which itself
    #      contains "未找到相关内容".
    # Matching the phrase as a substring accepts both without false negatives;
    # the phrase is specific enough that a genuine substantive answer won't
    # contain it. An exact/startswith check would miss path 1.
    return NO_ANSWER_TEXT in answer_text.strip()


def _print_summary(rows: list[RetrievalEvalRow], no_answer_results: list[dict]) -> None:
    print()
    print("=" * 64)
    print(" Retrieval metrics (computed over final_sources, FINAL_TOP_K=5)")
    print("=" * 64)
    by_kind: dict[str, list[RetrievalEvalRow]] = defaultdict(list)
    for r in rows:
        by_kind[r.kind].append(r)

    # Header
    print(f"{'kind':<16} {'n':>4} {'R@1':>8} {'R@5':>8} {'MRR@5':>8}")
    print("-" * 48)

    for kind in ["factual", "table_formula", "code_lookup", "transcript", "multi_turn"]:
        kind_rows = by_kind.get(kind, [])
        if not kind_rows:
            continue
        s = summarize(kind_rows)
        # summarize gives recall@5/20/mrr@10; we want @1, @5, mrr@5 — recompute.
        n = len(kind_rows)
        r1 = sum(1 for r in kind_rows if r.hit_rank == 1) / n
        r5 = sum(1 for r in kind_rows if r.hit_rank is not None and r.hit_rank <= 5) / n
        mrr5 = sum(
            1.0 / r.hit_rank for r in kind_rows
            if r.hit_rank is not None and r.hit_rank <= 5
        ) / n
        print(f"{kind:<16} {n:>4} {r1:>8.3f} {r5:>8.3f} {mrr5:>8.3f}")

    # Overall
    if rows:
        n = len(rows)
        r1 = sum(1 for r in rows if r.hit_rank == 1) / n
        r5 = sum(1 for r in rows if r.hit_rank is not None and r.hit_rank <= 5) / n
        mrr5 = sum(
            1.0 / r.hit_rank for r in rows
            if r.hit_rank is not None and r.hit_rank <= 5
        ) / n
        print("-" * 48)
        print(f"{'OVERALL':<16} {n:>4} {r1:>8.3f} {r5:>8.3f} {mrr5:>8.3f}")

    # Multi-turn breakdown by turn.
    mt = by_kind.get("multi_turn", [])
    if mt:
        t1 = [r for r in mt if r.item_id.endswith("-t1")]
        t2 = [r for r in mt if r.item_id.endswith("-t2")]
        print()
        print(" Multi-turn split (t2 is the one that tests rewriter + carry):")
        for label, rs in [("  turn-1", t1), ("  turn-2", t2)]:
            if not rs:
                continue
            n = len(rs)
            r5 = sum(1 for r in rs if r.hit_rank is not None and r.hit_rank <= 5) / n
            mrr5 = sum(
                1.0 / r.hit_rank for r in rs
                if r.hit_rank is not None and r.hit_rank <= 5
            ) / n
            print(f"{label:<16} {n:>4} {'':>8} {r5:>8.3f} {mrr5:>8.3f}")

    # No-answer compliance.
    print()
    print("=" * 64)
    print(" No-answer compliance (answer_text contains '未找到相关内容')")
    print("=" * 64)
    if no_answer_results:
        ok = sum(1 for r in no_answer_results if r["compliant"])
        n = len(no_answer_results)
        print(f"  compliant: {ok}/{n}  ({ok/n:.3f})")
        non = [r for r in no_answer_results if not r["compliant"]]
        if non:
            print("  non-compliant items:")
            for r in non:
                print(f"    {r['item_id']}: answered with {len(r['answer_text'])} chars")
                print(f"      head: {r['answer_text'][:100].replace(chr(10),' ')}")


# Phase A: extended comparison 2x2 cells. The runner grades each cell with
# the SAME `grade_comparison` (which exposes the legacy `both_hit` plus
# the new `any_side_hit` / `all_sides_hit` / `side_recall` / `side_mrr`).
# The cell objects keep `k` so downstream code can pair on the right k.
COMPARISON_CELLS = ("off_k5", "off_k8", "on_k5", "on_k8")


def _safe_get_grade(item: dict, cell: str, k: int) -> dict | None:
    """Return the grade dict for `cell` at k=5 or k=8, or None on error/miss."""
    g = item.get(cell)
    if not isinstance(g, dict):
        return None
    if g.get("k") != k:
        return None
    return g


def _safe_delta(on: float | None, off: float | None) -> float | None:
    if on is None or off is None:
        return None
    return on - off


def _print_comparison_protocol_header(
    fp_status: str,
    fp_reason: str,
    fp_baseline: dict | None,
    golden_path: Path,
    custom_golden: bool,
) -> None:
    """Print the fixed Phase A disclaimer + run-protocol header."""
    print()
    print("!" * 64)
    print("!  PHASE A disclaimer: this is in-process retrieval-only comparison.")
    print("!  It is NOT equivalent to QUERY_DECOMPOSE_ENABLED in production.")
    print("!  It does NOT exercise rewrite / guard / carry / context packaging,")
    print("!  answer quality, citation support, latency, cost, or real traffic.")
    print("!  decision_eligible = false")
    print("!" * 64)
    print()
    print(f"  experiment_phase: A_offline_retrieval_mechanism")
    print(f"  production_toggle_equivalent: false")
    print(f"  context_coverage_evaluated: false")
    print(f"  answer_quality_evaluated: false")
    print(f"  decision_eligible: false")
    print()
    print("  k_off_policy = FINAL_TOP_K = 5")
    print("  k_on_policy  = DECOMPOSE_FINAL_TOP_K = 8")
    print("  cells: off_k5, off_k8, on_k5, on_k8 (each independent real call)")
    print(f"  decomposer model: {LLM_REWRITE_MODEL}")
    print()
    print(f"  golden: {golden_path}")
    if custom_golden:
        sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()
        print(f"  golden_sha256: {sha}  (CUSTOM — not bound to default fingerprint sidecar;")
        print(f"                    this run is NOT decision-grade)")
    if fp_status == "match":
        print(f"  fingerprint: match (frozen_at={fp_baseline.get('frozen_at','?') if fp_baseline else '?'})")
    elif fp_status == "mismatch":
        print(f"  fingerprint: MISMATCH (live vs baseline drift; non-fatal under non-strict)")
    else:
        print(f"  fingerprint: {fp_status}  reason={fp_reason}")
    print()


def _format_rate(v: float | None) -> str:
    return "N/A" if v is None else f"{v:.3f}"


def _print_comparison_per_item(results: list[dict]) -> None:
    if not results:
        return
    print(" Per-item:")
    print("-" * 64)
    for r in results:
        item_id = r["item_id"]
        ds = r["decompose_status"]
        if ds == "applied":
            ds_marker = "applied"
        elif ds == "not_applied":
            ds_marker = "not_applied (fallback)"
        else:
            ds_marker = "ERROR"
        # k5 / k8 lines.
        for cell, k in (("off_k5", 5), ("off_k8", 8), ("on_k5", 5), ("on_k8", 8)):
            g = _safe_get_grade(r, cell, k)
            arm = "OFF" if cell.startswith("off") else "ON "
            tag = f"{arm}@{k}"
            if g is None:
                err = r["errors"].get(cell)
                if err:
                    print(f"  {item_id:<32} {tag}: error — {err[:60]}")
                else:
                    print(f"  {item_id:<32} {tag}: no grade")
                continue
            ranks = g["side_ranks"]
            sh = g["sides_hit"]
            st = g["sides_total"]
            ash = g["any_side_hit"]
            allh = g["all_sides_hit"]
            rec = g["side_recall"]
            mrr = g["side_mrr"]
            print(
                f"  {item_id:<32} {tag}: ranks={ranks}  "
                f"hit={sh}/{st}  any={ash}  all={allh}  rec={rec:.2f}  mrr={mrr:.2f}"
            )
        print(
            f"  {'':32} status: {ds_marker}  sub_queries={r.get('sub_queries') or []}"
        )
        if r["errors"].get("decompose"):
            print(f"  {'':32} decompose_error: {r['errors']['decompose']}")
    print()


def _print_comparison_aggregates(
    cells_by_k: dict[int, list[dict]],
    warnings: list[str],
) -> None:
    """Print ITT / applied-only / five contrasts / gain-loss tables."""
    if not any(cells_by_k.values()):
        return
    print(" Aggregates (ITT and applied-only, by k):")
    print("-" * 64)
    for k in (5, 8):
        items = cells_by_k.get(k, [])
        if not items:
            continue
        print(f"\n  --- k = {k} ---")
        for label, analysis_set in (
            ("ITT (complete pairs, includes not_applied)",
             "itt_complete_pairs"),
            ("applied-only (decompose_status=applied)",
             "applied_only_complete_pairs"),
        ):
            agg = aggregate_comparison(items, k=k, analysis_set=analysis_set)
            print(f"\n  [{label}]")
            print(
                f"  n_items={agg['n_items_selected']}  "
                f"n_valid={agg['n_valid_gold']}  "
                f"n_decompose_applied={agg['n_decompose_applied']}  "
                f"n_decompose_not_applied={agg['n_decompose_not_applied']}  "
                f"n_decompose_error={agg['n_decompose_error']}  "
                f"n_paired_evaluable={agg['n_paired_evaluable']}  "
                f"n_side_observations={agg['n_side_observations']}"
            )
            print(
                f"  any_side_hit_rate  OFF={_format_rate(agg['any_side_hit_rate_off'])}  "
                f"ON={_format_rate(agg['any_side_hit_rate_on'])}  "
                f"delta={_format_rate(agg['delta_any_side_hit_rate'])}"
            )
            print(
                f"  all_sides_hit_rate OFF={_format_rate(agg['all_sides_hit_rate_off'])}  "
                f"ON={_format_rate(agg['all_sides_hit_rate_on'])}  "
                f"delta={_format_rate(agg['delta_all_sides_hit_rate'])}  <-- headline payoff"
            )
            print(
                f"  macro_side_recall  OFF={_format_rate(agg['macro_side_recall_off'])}  "
                f"ON={_format_rate(agg['macro_side_recall_on'])}  "
                f"delta={_format_rate(agg['delta_macro_side_recall'])}"
            )
            print(
                f"  micro_side_recall  OFF={_format_rate(agg['micro_side_recall_off'])}  "
                f"ON={_format_rate(agg['micro_side_recall_on'])}  "
                f"delta={_format_rate(agg['delta_micro_side_recall'])}"
            )
            print(
                f"  macro_side_mrr     OFF={_format_rate(agg['macro_side_mrr_off'])}  "
                f"ON={_format_rate(agg['macro_side_mrr_on'])}  "
                f"delta={_format_rate(agg['delta_macro_side_mrr'])}"
            )
            print(
                f"  mean_rank_given_hit OFF={_format_rate(agg['mean_rank_given_hit_off'])} "
                f"(n={agg['rank_given_hit_count_off']})  "
                f"ON={_format_rate(agg['mean_rank_given_hit_on'])} "
                f"(n={agg['rank_given_hit_count_on']})  "
                f"delta={_format_rate(agg['delta_mean_rank_given_hit'])}  "
                f"[diagnostic only — not a payoff]"
            )
            print(
                f"  transitions  gain={agg['gain_count']}  "
                f"loss={agg['loss_count']}  "
                f"same_hit={agg['same_hit_count']}  "
                f"same_miss={agg['same_miss_count']}  "
                f"(sum={agg['gain_count']+agg['loss_count']+agg['same_hit_count']+agg['same_miss_count']}, "
                f"n_paired={agg['n_paired_evaluable']})"
            )
            n_err = (
                agg["n_error_off_k5"] + agg["n_error_off_k8"]
                + agg["n_error_on_k5"] + agg["n_error_on_k8"]
            )
            if n_err:
                print(
                    f"  errors  off_k5={agg['n_error_off_k5']}  "
                    f"off_k8={agg['n_error_off_k8']}  "
                    f"on_k5={agg['n_error_on_k5']}  "
                    f"on_k8={agg['n_error_on_k8']}  "
                    f"items={agg['error_item_ids']}"
                )
    # Five contrasts (plan §6).
    print()
    print(" Five contrasts (Phase A protocol §6):")
    print("-" * 64)
    for k in (5, 8):
        items = cells_by_k.get(k, [])
        if not items:
            continue
        agg = aggregate_comparison(items, k=k, analysis_set="itt_complete_pairs")
        dec_eq = agg["delta_all_sides_hit_rate"]
        off_to_8 = _safe_delta(
            aggregate_comparison(
                [{"off": it["off_k8"], "on": it["on_k8"],
                  "decompose_status": it["decompose_status"]} for it in items],
                k=8, analysis_set="itt_complete_pairs",
            )["all_sides_hit_rate_on"],
            aggregate_comparison(
                [{"off": it["off_k5"], "on": it["on_k5"],
                  "decompose_status": it["decompose_status"]} for it in items],
                k=5, analysis_set="itt_complete_pairs",
            )["all_sides_hit_rate_off"],
        )
        # Capacity k5->k8 within the ON path needs the on_k5 / on_k8 pair
        # already in the items dict.
        agg_on_k5 = aggregate_comparison(
            [{"off": it["off_k5"], "on": it["on_k5"],
              "decompose_status": it["decompose_status"]} for it in items],
            k=5, analysis_set="itt_complete_pairs",
        )
        agg_on_k8 = aggregate_comparison(
            [{"off": it["off_k8"], "on": it["on_k8"],
              "decompose_status": it["decompose_status"]} for it in items],
            k=8, analysis_set="itt_complete_pairs",
        )
        cap_multi = _safe_delta(
            agg_on_k8["all_sides_hit_rate_on"], agg_on_k5["all_sides_hit_rate_on"]
        )
        # Projected policy effect (off_policy = off_k5; on_policy = on_k8
        # if applied else off_k5). For items where decompose was not applied,
        # on_k8 == off_k5 (fallback_copy), so the delta collapses to 0.
        projected_offs = []
        projected_ons = []
        for it in items:
            if it["decompose_status"] == "applied":
                projected_offs.append(it["off_k5"]["all_sides_hit"])
                projected_ons.append(it["on_k8"]["all_sides_hit"])
            else:
                projected_offs.append(it["off_k5"]["all_sides_hit"])
                projected_ons.append(it["off_k5"]["all_sides_hit"])
        n = len(items)
        if n:
            proj_off = sum(projected_offs) / n
            proj_on = sum(projected_ons) / n
        else:
            proj_off = proj_on = None
        proj_delta = _safe_delta(proj_on, proj_off)
        print(f"\n  k = {k}:")
        print(
            f"    decomposition_equal_budget_k{k}     = on_k{k} - off_k{k}     "
            f"= {_format_rate(dec_eq)}  (headline decomposition payoff at equal capacity)"
        )
        if k == 5:
            print(
                f"    capacity_single_k5_to_k8            = off_k8 - off_k5     "
                f"= {_format_rate(off_to_8)}  (single-query capacity gain)"
            )
            print(
                f"    capacity_multi_k5_to_k8             = on_k8 - on_k5      "
                f"= {_format_rate(cap_multi)}  (multi-query capacity gain)"
            )
            print(
                f"    projected_policy_effect            = on_policy - off_policy "
                f"= {_format_rate(proj_delta)}  (off@5 vs (on@8 if applied else off@5))"
            )
    if warnings:
        print()
        print(" Run warnings:")
        for w in warnings:
            print(f"  - {w}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--golden", type=Path, default=GOLDEN)
    p.add_argument(
        "--limit", type=int, default=0,
        help="If >0, only process the first N items (for smoke tests)."
    )
    p.add_argument(
        "--kinds", type=str, default="",
        help="Comma-separated kinds to include. Empty = all."
    )
    p.add_argument(
        "--strict-staleness", action="store_true",
        help="Exit non-zero if the live index fingerprint has drifted from the "
             "baseline in src/eval/golden.fingerprint.json. Default: warn only, "
             "still run the eval.",
    )
    args = p.parse_args()

    if not ZHIPU_API_KEY:
        raise SystemExit("ZHIPU_API_KEY missing — set in .env before running.")

    # Staleness check — compute live fingerprint and compare to the baseline
    # frozen alongside the golden set. Non-fatal by default; loud warning if
    # drift is detected, exit non-zero under --strict-staleness.
    fp_status, fp_reason, fp_baseline, fp_live = _check_staleness(args.strict_staleness)
    t_start = perf_counter()

    items: list[EvalItem] = load_jsonl(args.golden)
    if args.kinds:
        allowed = {k.strip() for k in args.kinds.split(",") if k.strip()}
        items = [it for it in items if it.kind in allowed]
    if args.limit > 0:
        items = items[: args.limit]
    print(f"[eval] loaded {len(items)} items from {args.golden}")

    # Group multi-turn items by pair number; everything else is solo.
    solos: list[EvalItem] = []
    pairs: dict[str, dict[str, EvalItem]] = defaultdict(dict)
    for it in items:
        if it.kind == "multi_turn":
            # id form: eval-multi_turn-XXXX-tN
            parts = it.id.rsplit("-", 1)
            if len(parts) == 2 and parts[1] in ("t1", "t2"):
                pairs[parts[0]][parts[1]] = it
                continue
        solos.append(it)

    rows: list[RetrievalEvalRow] = []
    no_answer_results: list[dict] = []
    per_item_log: list[dict] = []

    # Solo items.
    # - retrieval-graded kinds: call retrieve() directly. First-turn
    #   ChatSession.ask() is functionally equivalent for retrieval
    #   (rewrite is a no-op on empty history, carry-forward has nothing
    #   to carry), but skips the LLM generation call → ~3x faster.
    # - no_answer: needs full ChatSession.ask() to get answer text.
    for i, it in enumerate(solos, 1):
        if it.kind == "comparison":
            continue  # graded separately below (needs both-sides + off/on split)
        try:
            if it.kind == "no_answer":
                session = ChatSession()
                retrieved, answer_text, timings = _ask(session, it.question)
                relevance = _score_snapshot(session.last_turn_result.final_sources)
            else:
                t0 = perf_counter()
                parents = retrieve(it.question)
                retrieved = [p.parent_id for p in parents]
                relevance = _score_snapshot(parents)
                answer_text = ""
                timings = {"retrieve": perf_counter() - t0, "total": perf_counter() - t0}
        except Exception as exc:  # noqa: BLE001
            print(f"[eval] {it.id} FAILED: {exc}")
            per_item_log.append({"item_id": it.id, "error": str(exc)})
            continue

        if it.kind == "no_answer":
            ok = _grade_no_answer(answer_text)
            no_answer_results.append({
                "item_id": it.id,
                "compliant": ok,
                "answer_text": answer_text,
                "retrieved_count": len(retrieved),
            })
            per_item_log.append({
                "item_id": it.id, "kind": it.kind,
                "compliant": ok, "answer_text": answer_text,
                "retrieved": retrieved, "timings": timings,
                "relevance": relevance,
            })
        else:
            hit = grade_one(it.expected_parent_ids, retrieved)
            rows.append(RetrievalEvalRow(
                item_id=it.id, kind=it.kind,
                expected=it.expected_parent_ids, retrieved=retrieved,
                hit_rank=hit,
            ))
            per_item_log.append({
                "item_id": it.id, "kind": it.kind,
                "expected": it.expected_parent_ids,
                "retrieved": retrieved, "hit_rank": hit,
                "answer_text": answer_text, "timings": timings,
                "relevance": relevance,
            })

        print(
            f"[{i:>3}/{len(solos)}] {it.id:<32} "
            f"{'OK ' if (it.kind=='no_answer' and no_answer_results and no_answer_results[-1]['compliant']) or (it.kind!='no_answer' and rows and rows[-1].hit_rank is not None) else 'MISS'} "
            f"({timings.get('total', 0):.2f}s)"
        )

    # Multi-turn pairs — shared ChatSession across t1 and t2.
    pair_ids = sorted(pairs.keys())
    for j, pid in enumerate(pair_ids, 1):
        pair = pairs[pid]
        t1, t2 = pair.get("t1"), pair.get("t2")
        if not t1 or not t2:
            print(f"[eval] {pid} incomplete pair (skipping)")
            continue
        session = ChatSession()
        for tag, item in (("t1", t1), ("t2", t2)):
            try:
                retrieved, answer_text, timings = _ask(session, item.question)
                relevance = _score_snapshot(session.last_turn_result.final_sources)
            except Exception as exc:  # noqa: BLE001
                print(f"[eval] {item.id} FAILED: {exc}")
                per_item_log.append({"item_id": item.id, "error": str(exc)})
                continue
            hit = grade_one(item.expected_parent_ids, retrieved)
            rows.append(RetrievalEvalRow(
                item_id=item.id, kind=item.kind,
                expected=item.expected_parent_ids, retrieved=retrieved,
                hit_rank=hit,
            ))
            per_item_log.append({
                "item_id": item.id, "kind": item.kind,
                "expected": item.expected_parent_ids,
                "retrieved": retrieved, "hit_rank": hit,
                "answer_text": answer_text, "timings": timings,
                "relevance": relevance,
            })
        print(f"[pair {j:>2}/{len(pair_ids)}] {pid}  done")

    # Comparison items — Phase A 2x2 protocol:
    #   off_k5 = retrieve(question, top_k=5)
    #   off_k8 = retrieve(question, top_k=8)
    #   on_k5  = retrieve_multi(..., top_k=5)  if applied else fallback_copy(off_k5)
    #   on_k8  = retrieve_multi(..., top_k=8)  if applied else fallback_copy(off_k8)
    # decompose is called exactly once per item and reused for both ON cells.
    # Each cell is wrapped in its own try/except: errors never masquerade
    # as miss — the cell's grade stays None and the error is recorded in
    # `errors[cell]`.
    comparisons = [it for it in solos if it.kind == "comparison"]
    from src.decompose import maybe_decompose  # imported here to keep import
                                          # surface narrow if no comparison
    comparison_results: list[dict] = []
    if comparisons:
        # Validate golden labels up-front (plan §5). One bad item aborts the
        # comparison section with a clear message.
        for it in comparisons:
            try:
                grade_comparison_input(
                    it.expected_sides, retrieved=["__placeholder__"], k=5,
                )
            except ComparisonInputError as exc:
                print(
                    f"[eval] FATAL: comparison item {it.id} has invalid "
                    f"expected_sides: {exc}. Aborting comparison section.",
                    file=sys.stderr,
                )
                # Mark all comparison cells as errored.
                comparison_results.append({
                    "item_id": it.id,
                    "off_k5": None, "off_k8": None, "on_k5": None, "on_k8": None,
                    "decompose_status": "error",
                    "sub_queries": [],
                    "errors": {
                        "off_k5": f"invalid golden: {exc}",
                        "off_k8": f"invalid golden: {exc}",
                        "on_k5": f"invalid golden: {exc}",
                        "on_k8": f"invalid golden: {exc}",
                        "decompose": f"invalid golden: {exc}",
                    },
                })
                per_item_log.append({
                    "item_id": it.id, "kind": it.kind,
                    "error": f"invalid golden: {exc}",
                })
        # Drop the items we just failed-validated from `comparisons` so the
        # main 2x2 loop below only iterates valid items. The pre-recorded
        # errored items above are already in comparison_results.
        invalid_ids = {r["item_id"] for r in comparison_results
                       if r.get("decompose_status") == "error"}
        comparisons = [it for it in comparisons if it.id not in invalid_ids]

        for c, it in enumerate(comparisons, 1):
            item: dict = {
                "item_id": it.id,
                "off_k5": None, "off_k8": None,
                "on_k5": None, "on_k8": None,
                "decompose_status": "error",
                "sub_queries": [],
                "errors": {
                    "off_k5": None, "off_k8": None,
                    "on_k5": None, "on_k8": None,
                    "decompose": None,
                },
            }
            # off_k5
            try:
                off5 = [p.parent_id for p in retrieve(it.question, top_k=5)]
                item["off_k5"] = grade_comparison(it.expected_sides, off5, k=5)
            except Exception as exc:  # noqa: BLE001
                item["errors"]["off_k5"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            # off_k8
            try:
                off8 = [p.parent_id for p in retrieve(it.question, top_k=8)]
                item["off_k8"] = grade_comparison(it.expected_sides, off8, k=8)
            except Exception as exc:  # noqa: BLE001
                item["errors"]["off_k8"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            # decompose — exactly once per item
            try:
                dec = maybe_decompose(it.question)
                if dec.decompose and len(dec.sub_queries) >= 2:
                    item["decompose_status"] = "applied"
                    item["sub_queries"] = list(dec.sub_queries)
                    # on_k5 (independent real call)
                    try:
                        on5 = [p.parent_id for p in retrieve_multi(
                            dec.sub_queries, it.question, top_k=5,
                        )]
                        item["on_k5"] = grade_comparison(it.expected_sides, on5, k=5)
                    except Exception as exc:  # noqa: BLE001
                        item["errors"]["on_k5"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    # on_k8 (independent real call)
                    try:
                        on8 = [p.parent_id for p in retrieve_multi(
                            dec.sub_queries, it.question, top_k=8,
                        )]
                        item["on_k8"] = grade_comparison(it.expected_sides, on8, k=8)
                    except Exception as exc:  # noqa: BLE001
                        item["errors"]["on_k8"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                else:
                    # not_applied: existing interface cannot distinguish
                    # gate miss / LLM refusal / parse failure / network /
                    # insufficient sub_queries. Plan §6 forbids naming it
                    # `decomposer_declined`.
                    item["decompose_status"] = "not_applied"
                    item["sub_queries"] = list(dec.sub_queries) if dec and dec.sub_queries else []
                    # fallback_copy per plan §6
                    if item["off_k5"] is not None:
                        item["on_k5"] = grade_comparison(it.expected_sides, [], k=5)
                        # Replace inner retrieved with off_k5 list copy for
                        # transparency in per-item log; but grade uses []
                        # because we only need ranks, not raw list.
                        # For the on_k5 grade to mirror off_k5 exactly, we
                        # re-grade using the off_k5 parent_id list.
                        item["on_k5"] = grade_comparison(
                            it.expected_sides,
                            [p.parent_id for p in retrieve(it.question, top_k=5)],
                            k=5,
                        )
                    if item["off_k8"] is not None:
                        item["on_k8"] = grade_comparison(
                            it.expected_sides,
                            [p.parent_id for p in retrieve(it.question, top_k=8)],
                            k=8,
                        )
            except Exception as exc:  # noqa: BLE001
                item["errors"]["decompose"] = (
                    f"{type(exc).__name__}: {str(exc)[:200]}"
                )
                item["decompose_status"] = "error"

            comparison_results.append(item)

            # Add additive fields to per_item_log. Old keys kept for
            # backward compatibility with `scripts/diff_eval_runs.py`.
            log_entry: dict = {
                "item_id": it.id, "kind": it.kind,
                "expected_sides": it.expected_sides,
                # Legacy keys (k=8 was the original single-k grade).
                "off_retrieved": None, "on_retrieved": None,
                "off_both_hit": None, "on_both_hit": None,
                "decomposed": item["decompose_status"] == "applied",
                "sub_queries": item["sub_queries"],
                # New additive fields.
                "experiment_phase": EXPERIMENT_PHASE,
                "production_toggle_equivalent": False,
                "decision_eligible": False,
                "decompose_status": item["decompose_status"],
                "cells": {
                    "off_k5": item["off_k5"],
                    "off_k8": item["off_k8"],
                    "on_k5": item["on_k5"],
                    "on_k8": item["on_k8"],
                },
                "errors": dict(item["errors"]),
            }
            # Populate legacy k=8 keys from the new on_k8 / off_k8 grades.
            if item["off_k8"] is not None:
                log_entry["off_retrieved"] = [p.parent_id for p in retrieve(it.question, top_k=8)]
                log_entry["off_both_hit"] = item["off_k8"].get("both_hit")
            if item["on_k8"] is not None:
                if item["decompose_status"] == "applied":
                    log_entry["on_retrieved"] = [p.parent_id for p in retrieve_multi(
                        item["sub_queries"], it.question, top_k=8,
                    )]
                else:
                    log_entry["on_retrieved"] = [p.parent_id for p in retrieve(it.question, top_k=8)]
                log_entry["on_both_hit"] = item["on_k8"].get("both_hit")
            per_item_log.append(log_entry)

            ds = item["decompose_status"]
            print(
                f"[cmp {c:>2}/{len(comparisons)}] {it.id}  "
                f"status={ds}  off_k5={'Y' if item['off_k5'] else 'N'}  "
                f"off_k8={'Y' if item['off_k8'] else 'N'}  "
                f"on_k5={'Y' if item['on_k5'] else 'N'}  "
                f"on_k8={'Y' if item['on_k8'] else 'N'}"
            )

    elapsed = perf_counter() - t_start

    _print_summary(rows, no_answer_results)
    _print_comparison_protocol_header(
        fp_status=fp_status, fp_reason=fp_reason, fp_baseline=fp_baseline,
        golden_path=Path(args.golden), custom_golden=(args.golden != GOLDEN),
    )
    # Per-item and aggregate tables.
    _print_comparison_per_item(comparison_results)
    # Aggregate over the 2x2 grid: each comparison item produces one record
    # in each k-bucket (k=5 and k=8). The aggregator reads flat `off` /
    # `on` keys per k (it["off"] / it["on"]); the contrast block below
    # reads the per-k cells off_k5 / off_k8 / on_k5 / on_k8 directly.
    # We keep both shapes for clarity.
    cells_by_k: dict[int, list[dict]] = {5: [], 8: []}
    for r in comparison_results:
        for k in (5, 8):
            off_g = r.get(f"off_k{k}")
            on_g = r.get(f"on_k{k}")
            if off_g is not None and on_g is not None:
                cells_by_k[k].append({
                    "off": off_g,
                    "on": on_g,
                    "off_k5": r.get("off_k5"),
                    "off_k8": r.get("off_k8"),
                    "on_k5": r.get("on_k5"),
                    "on_k8": r.get("on_k8"),
                    "decompose_status": r.get("decompose_status", "error"),
                })
    all_warnings: list[str] = []
    for k, items in cells_by_k.items():
        agg = aggregate_comparison(items, k=k, analysis_set="itt_complete_pairs")
        all_warnings.extend(agg["run_warnings"])
    _print_comparison_aggregates(cells_by_k, all_warnings)
    print()
    print(f"[eval] elapsed {elapsed:.1f}s")

    # Persist run.
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_path = RUNS_DIR / f"run_{ts}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for row in per_item_log:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[eval] per-item log → {out_path}")

    # Sidecar summary (Phase A §10). Independent JSON next to the JSONL;
    # never inserts summary rows into the JSONL itself, so diff_eval_runs.py
    # and any external reader that assumes per-item records keep working.
    summary_path = _summary_path(out_path)
    git_sha, git_dirty = _git_sha_and_dirty()
    golden_path = Path(args.golden)
    golden_sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()
    custom_golden = (str(golden_path) != str(GOLDEN))
    summary = {
        "schema_version": 1,
        "experiment_phase": EXPERIMENT_PHASE,
        "production_toggle_equivalent": False,
        "context_coverage_evaluated": False,
        "answer_quality_evaluated": False,
        "decision_eligible": False,
        "phase_a_disclaimer": PHASE_A_DISCLAIMER,
        "jsonl_path": str(out_path),
        "started_at": datetime.fromtimestamp(t_start).isoformat(),
        "ended_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "host": socket.gethostname(),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "golden_path": str(golden_path),
        "golden_sha256": golden_sha,
        "golden_is_default": not custom_golden,
        "cli": {
            "golden": str(golden_path),
            "limit": args.limit,
            "kinds": args.kinds,
            "strict_staleness": bool(args.strict_staleness),
        },
        "config_snapshot": {
            "FINAL_TOP_K": FINAL_TOP_K,
            "DECOMPOSE_FINAL_TOP_K": DECOMPOSE_FINAL_TOP_K,
            "LLM_REWRITE_MODEL": LLM_REWRITE_MODEL,
        },
        "fingerprint_status": fp_status,
        "fingerprint_reason": fp_reason,
        "fingerprint_baseline": fp_baseline,
        "fingerprint_live": fp_live,
        "run_completeness": (
            "partial" if any(r.get("decompose_status") == "error" for r in comparison_results)
            else "complete"
        ),
        "comparison_aggregates": {
            f"k{k}": aggregate_comparison(items, k=k, analysis_set=analysis_set)
            for k, items in cells_by_k.items()
            for analysis_set in ("itt_complete_pairs", "applied_only_complete_pairs")
        },
        "comparison_error_item_ids": [
            r["item_id"] for r in comparison_results
            if any(
                r["errors"].get(c) for c in ("off_k5", "off_k8", "on_k5", "on_k8", "decompose")
            )
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[eval] summary → {summary_path}")


if __name__ == "__main__":
    main()
