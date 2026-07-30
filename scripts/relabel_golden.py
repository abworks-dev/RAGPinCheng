"""Golden-set rebuild helper — READ-ONLY relabel/calibration tooling.

Context: `src/eval/golden.jsonl` grades retrieval by parent_id set-membership
(`grade_one`: a retrieval is "correct" iff a returned parent_id ∈
`expected_parent_ids`). parent_id is `uuid5(doc_title||section_path||parent_text)`
(`src/chunk.py._stable_id`), so when the corpus is re-parsed / re-chunked the
parent text changes and every id changes with it. The old golden set was
labelled on a prior index; its `expected_parent_ids` no longer intersect the
current index, so every retrieval item scores 0. This tool supports the
rebuild WITHOUT touching golden.jsonl or the index:

    # Phase 0 — prove the root cause and fingerprint the live index.
    python scripts/relabel_golden.py fingerprint

    # Phase 1 — build a human review sheet: per item, top-K live candidates.
    python scripts/relabel_golden.py candidates
    python scripts/relabel_golden.py candidates --top-k 10 --kinds factual,code_lookup

Both subcommands are strictly read-only: `fingerprint` reads parents.sqlite;
`candidates` additionally calls `retrieve()` (Qdrant + rerank, no LLM). Neither
writes to `golden.jsonl`, the index, or any database. `candidates` writes a
review sheet under `src/eval/relabel/` for a human (or Agent) to confirm the
correct parent(s) before anyone edits the golden set in a later, separately
approved step.

Grading it does NOT do: this tool does not decide correctness. It surfaces
candidates + the original `notes`/question so a reviewer can pick the right
parent_id. Picking is a human step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PARENTS_DB
from src.eval.io import load_jsonl
from src.eval.types import EvalItem

GOLDEN = Path(__file__).resolve().parent.parent / "src" / "eval" / "golden.jsonl"
RELABEL_DIR = Path(__file__).resolve().parent.parent / "src" / "eval" / "relabel"

# Retrieval-graded kinds. multi_turn is graded on parent_id too, so it is
# included; no_answer has no expected parent and is skipped for candidates.
RETRIEVAL_KINDS = {"factual", "table_formula", "code_lookup", "transcript", "multi_turn"}


# ── shared index reads ────────────────────────────────────────────────────────

def _load_parent_ids(db_path: Path = PARENTS_DB) -> list[str]:
    """All parent_ids currently in parents.sqlite (sorted, deterministic)."""
    if not db_path.exists():
        raise SystemExit(
            f"parents.sqlite not found at {db_path}. "
            "Run this on a machine with the live index."
        )
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT parent_id FROM parents").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def _index_fingerprint(parent_ids: list[str]) -> dict:
    """A stable fingerprint of the current index's parent_id set.

    count + sha256 over the sorted, newline-joined ids. Two indexes with the
    same fingerprint have the identical parent_id set → any golden set labelled
    against one is valid against the other.
    """
    h = hashlib.sha256("\n".join(parent_ids).encode("utf-8")).hexdigest()
    return {"parent_count": len(parent_ids), "parent_id_sha256": h}


# ── subcommand: fingerprint ───────────────────────────────────────────────────

def cmd_fingerprint(args: argparse.Namespace) -> None:
    parent_ids = _load_parent_ids(args.db)
    live_set = set(parent_ids)
    fp = _index_fingerprint(parent_ids)

    print("=" * 68)
    print(" Live index fingerprint (parents.sqlite)")
    print("=" * 68)
    print(f"  parent_count      : {fp['parent_count']}")
    print(f"  parent_id_sha256  : {fp['parent_id_sha256']}")

    # Document inventory via the same function admins see.
    try:
        from src.indexing_pipeline import list_indexed_documents

        docs = list_indexed_documents()
        by_cat: dict[str, int] = {}
        for d in docs:
            by_cat[d.category] = by_cat.get(d.category, 0) + 1
        print()
        print(f"  documents         : {len(docs)}")
        for cat in sorted(by_cat):
            print(f"    {cat or '(uncategorized)':<20} {by_cat[cat]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] could not list documents: {exc}")

    # Intersection with the golden set's expected_parent_ids — the root-cause check.
    items = load_jsonl(args.golden)
    expected: set[str] = set()
    graded_items = 0
    for it in items:
        if it.kind in RETRIEVAL_KINDS:
            graded_items += 1
            expected.update(it.expected_parent_ids)

    inter = expected & live_set
    print()
    print("=" * 68)
    print(" Root-cause check: golden expected_parent_ids ∩ live index")
    print("=" * 68)
    print(f"  retrieval-graded items      : {graded_items}")
    print(f"  distinct expected parent_ids: {len(expected)}")
    print(f"  ∩ live index                : {len(inter)}")
    if expected:
        pct = 100.0 * len(inter) / len(expected)
        print(f"  intersection rate           : {pct:.1f}%")
    if not inter:
        print("  → CONFIRMED stale: zero overlap. Every retrieval item scores 0")
        print("    purely because its expected parent_id no longer exists in the")
        print("    index. This is a labelling problem, not a retrieval problem.")
    else:
        print("  → Partial overlap; some labels still valid. Inspect per-item.")

    if args.json:
        out = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": fp,
            "graded_items": graded_items,
            "expected_distinct": len(expected),
            "intersection": len(inter),
            "intersection_ids": sorted(inter),
        }
        RELABEL_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = RELABEL_DIR / f"fingerprint_{ts}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[fingerprint] json → {path}")


# ── subcommand: candidates ────────────────────────────────────────────────────

def cmd_candidates(args: argparse.Namespace) -> None:
    from src.retrieve import retrieve

    live_set = set(_load_parent_ids(args.db))

    items = load_jsonl(args.golden)
    if args.kinds:
        allowed = {k.strip() for k in args.kinds.split(",") if k.strip()}
    else:
        allowed = set(RETRIEVAL_KINDS)
    graded = [it for it in items if it.kind in allowed and it.kind in RETRIEVAL_KINDS]
    if args.limit > 0:
        graded = graded[: args.limit]

    print(f"[candidates] {len(graded)} retrieval-graded items; top_k={args.top_k}")

    RELABEL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_path = RELABEL_DIR / f"candidates_{ts}.jsonl"

    n_old_alive = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for i, it in enumerate(graded, 1):
            old_ids = list(it.expected_parent_ids)
            old_alive = [pid for pid in old_ids if pid in live_set]
            if old_alive:
                n_old_alive += 1
            try:
                parents = retrieve(it.question, top_k=args.top_k)
            except Exception as exc:  # noqa: BLE001
                print(f"[{i:>3}/{len(graded)}] {it.id} RETRIEVE FAILED: {exc}")
                fh.write(json.dumps(
                    {"id": it.id, "kind": it.kind, "error": str(exc)},
                    ensure_ascii=False) + "\n")
                continue

            candidates = [
                {
                    "rank": r,
                    "parent_id": p.parent_id,
                    "doc_title": p.doc_title,
                    "section_path": p.section_path,
                    "score": round(p.score, 4),
                    "snippet": p.text.replace("\n", " ")[:200],
                    # Filled by a reviewer in the confirm step; never auto-set.
                    "is_correct": None,
                }
                for r, p in enumerate(parents, 1)
            ]
            row = {
                "id": it.id,
                "kind": it.kind,
                "question": it.question,
                "notes": it.notes,                 # ground-truth hint for the reviewer
                "old_expected_parent_ids": old_ids,
                "old_expected_still_in_index": old_alive,
                "candidates": candidates,
                # Reviewer writes the confirmed id(s) here; empty = undecided.
                "confirmed_parent_ids": [],
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            flag = "old-alive" if old_alive else ("no-recall" if not candidates else "")
            print(f"[{i:>3}/{len(graded)}] {it.id:<32} {len(candidates)} cands {flag}")

    print()
    print(f"[candidates] old expected still in index: {n_old_alive}/{len(graded)}")
    print(f"[candidates] review sheet → {out_path}")
    print("  Next (separate, human step): open the sheet, set `is_correct` on the")
    print("  right candidate(s) and/or fill `confirmed_parent_ids`. No golden.jsonl")
    print("  is written by this tool.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--golden", type=Path, default=GOLDEN)
    p.add_argument("--db", type=Path, default=PARENTS_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    fp = sub.add_parser("fingerprint", help="Phase 0: index fingerprint + stale-label proof")
    fp.add_argument("--json", action="store_true", help="also write a JSON artifact")
    fp.set_defaults(func=cmd_fingerprint)

    ca = sub.add_parser("candidates", help="Phase 1: per-item top-K review sheet")
    ca.add_argument("--top-k", type=int, default=8)
    ca.add_argument("--kinds", type=str, default="")
    ca.add_argument("--limit", type=int, default=0)
    ca.set_defaults(func=cmd_candidates)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
