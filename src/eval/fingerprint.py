"""Stable fingerprint of an index's parent_id set.

The fingerprint is parent_count + sha256 over the sorted, newline-joined
parent_ids. Two indexes with the same fingerprint have an identical
parent_id set; a golden set labelled against one is valid against the other.

Used by:
  - scripts/relabel_golden.py  : "fingerprint" subcommand + sidecar freeze
  - scripts/run_eval_retrieval.py : startup staleness check vs the sidecar

The frozen-at baseline is a per-environment anchor; when the live fingerprint
drifts from it, the golden set may have gone stale (re-indexed corpus,
different parent_id hashes) and should be re-labelled. This module never
silently re-labels — it only surfaces the drift.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

# Conventional sidecar location, relative to repo root.
FINGERPRINT_PATH = Path(__file__).resolve().parent / "golden.fingerprint.json"


def compute_fingerprint(parent_ids: list[str]) -> dict:
    """Return {"parent_count": N, "parent_id_sha256": "..."} for a list of ids.

    Input is sorted and newline-joined before hashing; the caller's order
    is irrelevant. Empty input hashes to the SHA256 of the empty string and
    yields parent_count=0, which is a valid (if useless) fingerprint.
    """
    h = hashlib.sha256("\n".join(sorted(parent_ids)).encode("utf-8")).hexdigest()
    return {"parent_count": len(parent_ids), "parent_id_sha256": h}


def load_baseline(path: Path = FINGERPRINT_PATH) -> dict | None:
    """Read the frozen fingerprint sidecar. None if the file doesn't exist."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_baseline(fp: dict, path: Path = FINGERPRINT_PATH) -> None:
    """Freeze `fp` (with a frozen_at timestamp) as the staleness baseline.

    Caller decides when to call this — typically right after a golden-set
    rebuild/relabelling, never implicitly.
    """
    payload = {
        "parent_count": fp["parent_count"],
        "parent_id_sha256": fp["parent_id_sha256"],
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compare(live: dict, baseline: dict) -> dict:
    """Return a small diff dict describing how `live` and `baseline` differ.

    Keys: match (bool), count_delta (int, live - baseline), sha256_changed
    (bool). Missing fields in either side are treated as no info rather than
    errors.
    """
    lc = live.get("parent_count")
    bc = baseline.get("parent_count")
    ls = live.get("parent_id_sha256")
    bs = baseline.get("parent_id_sha256")
    return {
        "match": lc == bc and ls == bs,
        "count_delta": (lc - bc) if (lc is not None and bc is not None) else None,
        "sha256_changed": (ls != bs) if (ls is not None and bs is not None) else None,
        "live": live,
        "baseline": baseline,
    }
