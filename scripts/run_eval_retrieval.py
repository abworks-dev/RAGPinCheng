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

Multi-turn pairs share a single ChatSession instance so turn-2 exercises
the rewriter + carry-forward. We grade both turns; turn-2 is the one
that actually tests the multi-turn machinery, turn-1 is recorded as a
sanity baseline.

Output:
  - Console summary table by kind.
  - Per-item JSONL at src/eval/runs/run_<ISO>.jsonl for inspection.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DECOMPOSE_FINAL_TOP_K, PARENTS_DB, ZHIPU_API_KEY
from src.eval.fingerprint import compare, compute_fingerprint, load_baseline
from src.eval.io import load_jsonl
from src.eval.metrics import RetrievalEvalRow, grade_comparison, grade_one, summarize
from src.eval.types import EvalItem
from src.retrieve import retrieve
from src.session import ChatSession

GOLDEN = Path(__file__).resolve().parent.parent / "src" / "eval" / "golden.jsonl"
RUNS_DIR = Path(__file__).resolve().parent.parent / "src" / "eval" / "runs"

NO_ANSWER_TEXT = "未找到相关内容"


def _load_live_parent_ids() -> list[str]:
    """Read the live parent_id set straight from parents.sqlite. No Qdrant."""
    conn = sqlite3.connect(PARENTS_DB)
    try:
        rows = conn.execute("SELECT parent_id FROM parents").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def _check_staleness(strict: bool) -> None:
    """Compare live index fingerprint to the frozen baseline; warn or fail.

    The baseline lives at src/eval/golden.fingerprint.json. The live set is
    read from parents.sqlite directly (no Qdrant, no model). On drift we
    print a clearly-marked warning and (under --strict-staleness) exit 2.
    We never block by default — a stale golden set is still useful to grade
    (the resulting 0/100 may itself be the signal), but it should be hard
    to miss.
    """
    baseline = load_baseline()
    if baseline is None:
        print("[eval] no baseline fingerprint (src/eval/golden.fingerprint.json) — "
              "skip staleness check. Run `relabel_golden.py fingerprint --freeze` "
              "after a golden-set rebuild to create one.")
        return
    try:
        live = compute_fingerprint(_load_live_parent_ids())
    except Exception as exc:  # noqa: BLE001 — never let a check crash the run
        print(f"[eval] [warn] could not compute live fingerprint: {exc}")
        return
    diff = compare(live, baseline)
    if diff["match"]:
        print(f"[eval] staleness OK (parent_count={live['parent_count']}, "
              f"frozen_at={baseline.get('frozen_at', '?')})")
        return
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
    print("!  --freeze` to refresh the baseline. See project-docs/golden-set-staleness-guard.md.")
    print("!" * 64)
    print()
    if strict:
        raise SystemExit(2)


def _ask(session: ChatSession, question: str) -> tuple[list[str], str, dict]:
    """Run one turn. Returns (retrieved_parent_ids, answer_text, timings)."""
    result = session.ask(question)
    pids = [p.parent_id for p in result.final_sources]
    return pids, result.answer_text, dict(result.timings)


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


def _print_comparison(results: list[dict], k: int) -> None:
    if not results:
        return
    print()
    print("=" * 64)
    print(f" Comparison both-sides coverage @{k} (off vs on decomposition)")
    print("=" * 64)
    n = len(results)
    off_both = sum(1 for r in results if r["off"]["both_hit"])
    on_both = sum(1 for r in results if r["on"]["both_hit"])
    print(f"  items: {n}")
    print(f"  both_hit  OFF (single-query): {off_both}/{n}  ({off_both/n:.3f})")
    print(f"  both_hit  ON  (decomposed) : {on_both}/{n}  ({on_both/n:.3f})")
    print(f"  decomposition payoff       : {(on_both-off_both)/n:+.3f}")
    print()
    for r in results:
        o, n_ = r["off"], r["on"]
        print(f"    {r['item_id']}  off[{o['sides_hit']}/{o['sides_total']}]"
              f" on[{n_['sides_hit']}/{n_['sides_total']}]"
              f" decomposed={r['decomposed']}")


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
    _check_staleness(args.strict_staleness)

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

    t_start = perf_counter()

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
            else:
                t0 = perf_counter()
                parents = retrieve(it.question)
                retrieved = [p.parent_id for p in parents]
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
            })
        print(f"[pair {j:>2}/{len(pair_ids)}] {pid}  done")

    # Comparison items — graded on BOTH-sides coverage, off vs on decomposition.
    # "off": single-query retrieve() (what a comparison query gets today when
    #        QUERY_DECOMPOSE_ENABLED is false).
    # "on":  retrieve_multi() over the LLM-decomposed sub-queries (the Phase-2
    #        path). The gap between the two is the decomposition payoff this
    #        golden set exists to quantify.
    comparisons = [it for it in solos if it.kind == "comparison"]
    comparison_results: list[dict] = []
    if comparisons:
        from src.decompose import maybe_decompose
        from src.retrieve import retrieve_multi
        K = DECOMPOSE_FINAL_TOP_K
        for c, it in enumerate(comparisons, 1):
            try:
                off = [p.parent_id for p in retrieve(it.question, top_k=K)]
                dec = maybe_decompose(it.question)
                if dec.decompose and len(dec.sub_queries) >= 2:
                    on = [p.parent_id for p in retrieve_multi(
                        dec.sub_queries, it.question, top_k=K)]
                else:
                    on = off  # decomposer declined; on-path == off-path
            except Exception as exc:  # noqa: BLE001
                print(f"[eval] {it.id} FAILED: {exc}")
                per_item_log.append({"item_id": it.id, "error": str(exc)})
                continue
            g_off = grade_comparison(it.expected_sides, off, K)
            g_on = grade_comparison(it.expected_sides, on, K)
            comparison_results.append({
                "item_id": it.id, "off": g_off, "on": g_on,
                "decomposed": bool(dec.decompose),
                "sub_queries": dec.sub_queries if dec.decompose else [],
            })
            per_item_log.append({
                "item_id": it.id, "kind": it.kind,
                "expected_sides": it.expected_sides,
                "off_retrieved": off, "on_retrieved": on,
                "off_both_hit": g_off["both_hit"], "on_both_hit": g_on["both_hit"],
                "decomposed": bool(dec.decompose),
                "sub_queries": dec.sub_queries if dec.decompose else [],
            })
            print(f"[cmp {c:>2}/{len(comparisons)}] {it.id}  "
                  f"off both_hit={g_off['both_hit']}  on both_hit={g_on['both_hit']}")

    elapsed = perf_counter() - t_start

    _print_summary(rows, no_answer_results)
    _print_comparison(comparison_results, DECOMPOSE_FINAL_TOP_K)
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


if __name__ == "__main__":
    main()
