"""Sample parents from parents.sqlite into src/eval/sampled_parents.json.

Run after the index is built. The output is consumed by the Q/A
synthesizer (run via Claude Code's Agent tool — no API key needed).

Usage:
    python scripts/sample_for_eval.py                 # default quotas, seed=42
    python scripts/sample_for_eval.py --seed 7
    python scripts/sample_for_eval.py --factual 80 --table-formula 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eval.sample import ALLOWED_CATEGORIES, DEFAULT_QUOTAS, write_sampled

OUT_PATH = Path(__file__).resolve().parent.parent / "src" / "eval" / "sampled_parents.json"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--factual", type=int, default=DEFAULT_QUOTAS["factual"])
    p.add_argument("--table-formula", type=int, default=DEFAULT_QUOTAS["table_formula"])
    p.add_argument("--code-lookup", type=int, default=DEFAULT_QUOTAS["code_lookup"])
    p.add_argument("--transcript", type=int, default=DEFAULT_QUOTAS["transcript"])
    p.add_argument(
        "--categories", type=str, default=",".join(sorted(ALLOWED_CATEGORIES)),
        help="Comma-separated category whitelist (default: 设计规范,客户标准). "
             "Pass 'all' to disable the filter (legacy behavior).",
    )
    p.add_argument(
        "--no-noise-filter", action="store_true",
        help="Disable the image-link / table-scaffolding debris filter.",
    )
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()

    quotas = {
        "factual": args.factual,
        "table_formula": args.table_formula,
        "code_lookup": args.code_lookup,
        "transcript": args.transcript,
    }
    if args.categories.strip().lower() == "all":
        allowed = None
    else:
        allowed = frozenset(
            c.strip() for c in args.categories.split(",") if c.strip()
        )
    counts = write_sampled(
        args.out, seed=args.seed, quotas=quotas,
        allowed_categories=allowed,
        apply_noise_filter=not args.no_noise_filter,
    )
    cat_desc = "all" if allowed is None else "、".join(sorted(allowed))
    print(f"[sample] seed={args.seed} categories={cat_desc} "
          f"noise_filter={not args.no_noise_filter} → {args.out}")
    for kind, want in quotas.items():
        got = counts.get(kind, 0)
        flag = "" if got == want else f"  (wanted {want})"
        print(f"  {kind:<14} {got}{flag}")
    total = sum(counts.values())
    print(f"  total          {total}")


if __name__ == "__main__":
    main()
