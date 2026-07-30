"""Comparison-intent query decomposition (Phase 2 multi-hop retrieval).

Cheap two-stage gate before the expensive LLM judge:
  1. A heuristic regex gate — only queries carrying a comparison marker
     (对比 / 比较 / 区别 / 差异 / 分别 / VS ...) are candidates. This keeps the
     LLM call rate low (<5%/turn expected); everything else short-circuits to
     "don't decompose" with zero extra cost.
  2. An LLM judge (LLM_REWRITE_MODEL — cheap, latency-tolerant) that returns
     strict JSON {"decompose": bool, "sub_queries": [...]}. Any failure
     (network, parse, empty) falls back to "don't decompose" so the caller
     transparently keeps the single-query path.

Returns are always safe: on ANY doubt, decompose=False. The decomposed path is
only taken when the caller has QUERY_DECOMPOSE_ENABLED set AND this returns True.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .config import DECOMPOSE_MAX_SUBQUERIES, LLM_REWRITE_MODEL
from .prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

# Heuristic gate: only run the LLM judge when the query looks comparative.
# Kept intentionally broad on markers but cheap — a false positive here costs at
# most one LLM call that then returns decompose=False.
_COMPARE_MARKERS = re.compile(
    r"对比|比较|相比|区别|差异|不同点|异同|分别|各自|VS|vs|与.*的?区别",
)


@dataclass
class DecomposeResult:
    decompose: bool
    sub_queries: list[str] = field(default_factory=list)
    # Telemetry for grey-rollout / debugging (not user-facing).
    gate_hit: bool = False
    usage: dict = field(default_factory=dict)


def _looks_comparative(query: str) -> bool:
    return bool(_COMPARE_MARKERS.search(query))


def _parse_decompose_json(raw: str) -> tuple[bool, list[str]]:
    """Parse the LLM's JSON verdict defensively. Returns (decompose, subs).

    Tolerates code-fence wrapping and stray prose around the JSON object.
    On any parse failure returns (False, []).
    """
    text = raw.strip()
    # Strip common ```json ... ``` fences.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # If there's surrounding prose, grab the first {...} block.
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return False, []
        text = m.group(0)

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False, []
    if not isinstance(obj, dict):
        return False, []

    if not obj.get("decompose"):
        return False, []

    subs_raw = obj.get("sub_queries") or []
    if not isinstance(subs_raw, list):
        return False, []
    subs = [s.strip() for s in subs_raw if isinstance(s, str) and s.strip()]
    # Dedupe while preserving order; cap at the configured max.
    seen: set[str] = set()
    unique: list[str] = []
    for s in subs:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    unique = unique[:DECOMPOSE_MAX_SUBQUERIES]

    # A single (or zero) sub-query is not a decomposition — nothing to fan out.
    if len(unique) < 2:
        return False, []
    return True, unique


def maybe_decompose(query: str) -> DecomposeResult:
    """Decide whether `query` should be split into comparison sub-queries.

    Cheap heuristic gate first; only comparative-looking queries reach the LLM.
    Any error path returns decompose=False so the caller keeps the single-query
    retrieval unchanged.
    """
    if not query or not query.strip():
        return DecomposeResult(decompose=False)

    if not _looks_comparative(query):
        return DecomposeResult(decompose=False, gate_hit=False)

    usage: dict = {}
    try:
        # Import here to avoid a circular import (generate imports config; this
        # module is imported by session alongside generate).
        from .generate import _client, _extract_usage

        client = _client()
        user_msg = render_prompt("decompose_user", query=query)
        resp = client.chat.completions.create(
            model=LLM_REWRITE_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": load_prompt("decompose_system")},
                {"role": "user", "content": user_msg},
            ],
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = resp.choices[0].message.content or ""
        usage = _extract_usage(resp)
        usage["model"] = LLM_REWRITE_MODEL
    except Exception as exc:  # noqa: BLE001 — never let decomposition break a turn
        logger.warning("decompose LLM call failed, falling back to single query: %s", exc)
        return DecomposeResult(decompose=False, gate_hit=True, usage=usage)

    decompose, subs = _parse_decompose_json(raw)
    return DecomposeResult(
        decompose=decompose,
        sub_queries=subs,
        gate_hit=True,
        usage=usage,
    )
