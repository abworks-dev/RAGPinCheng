"""Hybrid retrieval: dense + sparse → RRF → (optional code-boost) → rerank →
top-k children → expand to parents.

Pipeline per call:
  1. RRF-fuse three prefetches in Qdrant's native query_points:
       - dense (semantic),
       - sparse (lexical),
       - code-filter sparse (only when the query mentions a standard code like
         "GB 50017" — restricts to children whose `text` literally contains
         the code, full-text-indexed payload).
     Over-fetches RERANK_TOP_K children.
  2. Optional category filter (Qdrant payload index on `category`).
  3. Cross-encoder rerank (BGE-reranker-v2-m3) over the over-fetched children
     using the full child text.
  4. Dedupe by parent_id (best-reranked child wins), expand to parents.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, replace
from functools import lru_cache

from qdrant_client import models

from .config import (
    CODE_BOOST_TOP_K,
    COLLECTION,
    DECOMPOSE_FINAL_TOP_K,
    DECOMPOSE_MIN_QUOTA_PER_SUBQUERY,
    DENSE_TOP_K,
    FINAL_TOP_K,
    RERANK_BATCH_CAP,
    RERANK_ENABLED,
    RERANK_TOP_K,
    RERANK_USE_HEADER,
    SPARSE_TOP_K,
    APP_DB_PATH,
    CONTENT_HEAD_ENFORCEMENT,
)
from .content_retrieval_visibility import (
    PublishedContentSnapshot,
    SQLitePublishedContentVisibility,
)
from .embed import encode_one
from .index import _client, _ensure_payload_indexes, fetch_parents
from .rerank import rerank_scores
from .transcription_retrieval_visibility import (
    PublishedTranscriptSnapshot,
    PublishedTranscriptVisibilityPort,
    SQLitePublishedTranscriptVisibility,
)

_DEFAULT_VISIBILITY = SQLitePublishedTranscriptVisibility(APP_DB_PATH)
_DEFAULT_CONTENT_VISIBILITY = SQLitePublishedContentVisibility(
    APP_DB_PATH, CONTENT_HEAD_ENFORCEMENT
)


@dataclass
class RetrievedParent:
    parent_id: str
    doc_title: str
    category: str
    section_path: str
    source_path: str
    text: str
    score: float
    matched_children: list[str]
    doc_type: str = "pdf"
    start_time: str | None = None
    company: str | None = None
    media_id: str | None = None  # associated video asset if any
    sheet_name: str | None = None
    cell_range: str | None = None
    slide_number: int | None = None
    paragraph_anchor: str | None = None
    transcript_version_id: str | None = None
    content_item_id: str | None = None
    content_version_id: str | None = None
    category_key: str | None = None
    rrf_score: float = 0.0
    # Which sub-query (0-based) this parent was retrieved for, in the decomposed
    # multi-query path. None for the normal single-query path. Used by
    # `_build_context` to interleave sources so every comparison side survives
    # the context budget.
    subquery_idx: int | None = None


# Matches BIM-relevant standard codes (case-insensitive, half-width and
# full-width slashes both accepted, optional separator before the number
# like "50017-2017" or "16.3-2019"):
#   National / industry:  GB, JGJ, JG, CJJ, YB, TB, JC, DBJ, DB, CECS
#                          (each with an optional /T推荐 variant)
#   Group / association:  T/CECS, T/CCES, T/CSCS, T/CCS
#   International:        ISO
# Ordering matters in the alternation: longer prefixes must come first
# (JGJ before JG, DBJ before DB) so the regex engine doesn't bite off the
# shorter match and leave a dangling letter.
# Left boundary uses (?<![A-Za-z]) instead of \b: in Python's default
# Unicode re, Chinese chars are word chars, so \b does NOT fire between
# `在` and `GB` — meaning "在GB 50017" (very common in CJK queries) was
# previously missed. Asserting "preceding char is not an ASCII letter"
# instead lets the boundary fire in CJK-adjacent contexts while still
# preventing matches inside longer Latin tokens (e.g. "subGB").
_CODE_RE = re.compile(
    r"(?<![A-Za-z])("
    r"T[/／](?:CECS|CCES|CSCS|CCS)"
    r"|"
    r"(?:GB|JGJ|JG|CJJ|YB|TB|JC|CECS|DBJ|DB)(?:[/／]T)?"
    r"|"
    r"ISO"
    r")\s*[-—/／\s]?\s*"
    r"(\d{2,5}(?:[-．\.]\d+)*)",
    re.IGNORECASE,
)


def _extract_code_variants(query: str) -> list[str]:
    """Find standard-code identifiers in the query and return literal variants.

    For each detected code we emit the no-space form ("GB50017"), the spaced
    form ("GB 50017"), and the hyphenated form when a suffix is present
    ("GB 50017-2017"). These are what we pass to Qdrant MatchText so the
    code-boost prefetch hits chunks regardless of how the original document
    typeset the code.
    """
    variants: list[str] = []
    seen: set[str] = set()
    for m in _CODE_RE.finditer(query):
        prefix = m.group(1).upper().replace("／", "/")
        number = m.group(2).replace("．", ".")
        candidates = [f"{prefix}{number}", f"{prefix} {number}"]
        if "-" in number or "." in number:
            head = re.split(r"[-\.]", number, 1)[0]
            candidates.append(f"{prefix} {head}")
            candidates.append(f"{prefix}{head}")
        for c in candidates:
            if c not in seen:
                seen.add(c)
                variants.append(c)
    return variants


@lru_cache(maxsize=1)
def _bootstrap_indexes() -> bool:
    """Ensure payload indexes exist on the live collection.

    Cached so we only pay the round-trip once per process — `create_payload_index`
    is idempotent server-side but the call is still a network hop.
    """
    client = _client()
    if client.collection_exists(COLLECTION):
        _ensure_payload_indexes(client)
    return True


def _category_filter(categories: list[str] | None) -> models.Filter | None:
    if categories is None:
        return None
    if not categories:
        return None
    managed_keys = _category_keys_for_values(categories)
    if len(managed_keys) == len(set(categories)):
        return models.Filter(
            must=[models.FieldCondition(
                key="category_key", match=models.MatchAny(any=managed_keys)
            )]
        )
    conditions: list = [
        models.FieldCondition(key="category", match=models.MatchAny(any=list(categories)))
    ]
    if managed_keys:
        conditions.append(
            models.FieldCondition(key="category_key", match=models.MatchAny(any=managed_keys))
        )
    return models.Filter(should=conditions)


def _category_keys_for_values(values: list[str]) -> list[str]:
    if not APP_DB_PATH.exists() or not values:
        return []
    conn = sqlite3.connect(f"file:{APP_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='category_nodes'"
        ).fetchone() is None:
            return []
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT category_key FROM category_nodes WHERE category_key IN ({placeholders}) "
            "AND is_active=1 AND chat_search_enabled=1 AND deleted_at IS NULL",
            values,
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def _category_keys_for_labels(labels: list[str]) -> list[str]:
    if not APP_DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{APP_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='category_nodes'"
        ).fetchone() is None:
            return []
        placeholders = ",".join("?" for _ in labels)
        return [
            str(row[0])
            for row in conn.execute(
                f"SELECT category_key FROM category_nodes WHERE display_name IN ({placeholders}) "
                "AND is_active=1 AND chat_search_enabled=1 AND deleted_at IS NULL",
                labels,
            ).fetchall()
        ]
    finally:
        conn.close()


def _managed_category_labels() -> dict[str, str]:
    if not APP_DB_PATH.exists():
        return {}
    conn = sqlite3.connect(f"file:{APP_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='category_nodes'"
        ).fetchone() is None:
            return {}
        return {
            str(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT category_key,display_name FROM category_nodes WHERE deleted_at IS NULL"
            ).fetchall()
        }
    finally:
        conn.close()


def _code_filter(code_variants: list[str]) -> models.Filter | None:
    if not code_variants:
        return None
    return models.Filter(
        should=[
            models.FieldCondition(key="text", match=models.MatchText(text=v))
            for v in code_variants
        ]
    )


def _visibility_filter(snapshot: PublishedTranscriptSnapshot) -> models.Filter:
    visible: list = [
        models.IsEmptyCondition(is_empty=models.PayloadField(key="transcript_version_id"))
    ]
    if snapshot.version_ids:
        visible.append(
            models.FieldCondition(
                key="transcript_version_id",
                match=models.MatchAny(any=sorted(snapshot.version_ids)),
            )
        )
    return models.Filter(should=visible)


def _content_visibility_filter(snapshot: PublishedContentSnapshot) -> models.Filter:
    visible: list = []
    if snapshot.enforcement == "compat":
        visible.append(
            models.IsEmptyCondition(is_empty=models.PayloadField(key="content_version_id"))
        )
    else:
        # Transcript publication has its own immutable version/head contract.
        # In strict mode it must not be rejected merely because it is not a
        # managed ordinary document with a content_version_id.
        visible.append(
            models.Filter(
                must_not=[
                    models.IsEmptyCondition(
                        is_empty=models.PayloadField(key="transcript_version_id")
                    )
                ]
            )
        )
    if snapshot.version_ids:
        visible.append(
            models.FieldCondition(
                key="content_version_id",
                match=models.MatchAny(any=sorted(snapshot.version_ids)),
            )
        )
    if not visible:
        visible.append(
            models.FieldCondition(
                key="content_version_id",
                match=models.MatchValue(value="__no_published_content__"),
            )
        )
    return models.Filter(should=visible)


def _merge_filters(*filters: models.Filter | None) -> models.Filter | None:
    """AND complete filters without dropping must-not or minimum-should semantics."""
    parts = [item for item in filters if item is not None]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return models.Filter(must=parts)


def _recall_scored(
    query: str,
    categories: list[str] | None,
    snapshot: PublishedTranscriptSnapshot | None = None,
    content_snapshot: PublishedContentSnapshot | None = None,
):
    """Run one query's dense+sparse (+code-boost) recall and rerank.

    Returns (scored, child_rrf) where:
      - scored: list of (point, rerank_or_rrf_score) ordered best-first
      - child_rrf: {child_id: rrf_score} preserved before rerank reordering
    Empty recall returns ([], {}).
    """
    _bootstrap_indexes()
    emb = encode_one(query)
    code_variants = _extract_code_variants(query)

    snapshot = snapshot or _DEFAULT_VISIBILITY.snapshot()
    content_snapshot = content_snapshot or _DEFAULT_CONTENT_VISIBILITY.snapshot()
    cat_filter = _category_filter(categories)
    visibility_filter = _visibility_filter(snapshot)
    base_filter = _merge_filters(
        cat_filter, visibility_filter, _content_visibility_filter(content_snapshot)
    )
    code_filter = _code_filter(code_variants)

    prefetch = [
        models.Prefetch(
            query=emb.dense,
            using="dense",
            limit=DENSE_TOP_K,
            filter=base_filter,
        ),
        models.Prefetch(
            query=models.SparseVector(
                indices=emb.sparse_indices, values=emb.sparse_values
            ),
            using="sparse",
            limit=SPARSE_TOP_K,
            filter=base_filter,
        ),
    ]
    if code_filter is not None:
        prefetch.append(
            models.Prefetch(
                query=models.SparseVector(
                    indices=emb.sparse_indices, values=emb.sparse_values
                ),
                using="sparse",
                limit=CODE_BOOST_TOP_K,
                filter=_merge_filters(base_filter, code_filter),
            )
        )

    client = _client()
    result = client.query_points(
        collection_name=COLLECTION,
        prefetch=prefetch,
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=RERANK_TOP_K,
        with_payload=True,
    )

    points = list(result.points)
    if not points:
        return [], {}

    # Preserve RRF score per child before reranking overwrites ordering.
    child_rrf: dict[str, float] = {str(p.id): p.score for p in points}

    # Cross-encoder rerank on child text WITH header context prepended.
    # Without the header, fragments of sibling sections (e.g. row-split
    # tables in §9.3.3.1 vs §9.3.3.2) are indistinguishable to the
    # reranker because the identifying name/code lives in section_path,
    # not the body. Dense embeddings already see `doc_title > section_path`
    # via Child.embed_text; we mirror that here so the rerank step doesn't
    # undo the disambiguation. Set RERANK_USE_HEADER=False to ablate.
    if RERANK_ENABLED:
        if RERANK_USE_HEADER:
            passages = [
                f"{p.payload.get('doc_title','')} > "
                f"{p.payload.get('section_path','')}\n\n"
                f"{p.payload.get('text','')}"
                for p in points
            ]
        else:
            passages = [p.payload["text"] for p in points]
        ce_scores = rerank_scores(query, passages)
        scored = sorted(
            zip(points, ce_scores), key=lambda x: x[1], reverse=True
        )
    else:
        scored = [(p, p.score) for p in points]

    return scored, child_rrf


def _dedup_to_parents(
    scored: list,
    child_rrf: dict[str, float],
    top_k: int,
    snapshot: PublishedTranscriptSnapshot,
    content_snapshot: PublishedContentSnapshot | None = None,
    category_labels: dict[str, str] | None = None,
) -> list[RetrievedParent]:
    """Dedupe reranked children by parent_id and expand to RetrievedParent.

    Shared by the single-query and multi-query paths so both use identical
    dedupe / best-child-time / parent-expansion semantics.
    """
    # Dedupe children by parent_id, keeping the best score per parent.
    # rrf_score for a parent = best RRF score among its matched children.
    parent_order: list[str] = []
    parent_score: dict[str, float] = {}
    parent_rrf: dict[str, float] = {}
    parent_children: dict[str, list[str]] = {}
    # Timestamp of the best-scoring child per parent (transcripts only). `scored`
    # is already ordered best-first, so the child that first admits a parent IS
    # its top hit. We surface THIS time for playback instead of the parent's
    # first-turn time, so a citation seeks to the sentence actually matched.
    parent_hit_time: dict[str, str | None] = {}
    for point, score in scored:
        pid = point.payload["parent_id"]
        snippet = point.payload["text"][:120].replace("\n", " ")
        child_id = str(point.id)
        if pid in parent_score:
            parent_children[pid].append(snippet)
            parent_rrf[pid] = max(parent_rrf[pid], child_rrf.get(child_id, 0.0))
            continue
        if len(parent_order) >= top_k:
            # Cap reached: don't admit a new parent, but keep scanning so
            # already-accepted parents can still gather child snippets above.
            continue
        parent_score[pid] = float(score)
        parent_rrf[pid] = child_rrf.get(child_id, 0.0)
        parent_order.append(pid)
        parent_children[pid] = [snippet]
        parent_hit_time[pid] = point.payload.get("start_time")
    parents = fetch_parents(parent_order)
    out: list[RetrievedParent] = []
    for pid in parent_order:
        p = parents.get(pid)
        if not p or not snapshot.allows(p.get("transcript_version_id")):
            continue
        if (
            content_snapshot
            and p.get("transcript_version_id") is None
            and not content_snapshot.allows(p.get("content_version_id"))
        ):
            continue
        out.append(
            RetrievedParent(
                parent_id=pid,
                doc_title=p["doc_title"],
                category=(category_labels or {}).get(p.get("category_key"), p["category"]),
                section_path=p["section_path"],
                source_path=p["source_path"],
                text=p["text"],
                score=parent_score[pid],
                matched_children=parent_children[pid],
                doc_type=p.get("doc_type") or "pdf",
                # Prefer the matched child's timestamp so playback seeks to the
                # sentence actually hit; fall back to the parent's first-turn
                # time when the child payload lacks one (older index / non-
                # transcript docs), which keeps legacy behavior safe.
                start_time=parent_hit_time.get(pid) or p.get("start_time"),
                company=p.get("company"),
                media_id=p.get("media_id"),
                sheet_name=p.get("sheet_name"),
                cell_range=p.get("cell_range"),
                slide_number=p.get("slide_number"),
                paragraph_anchor=p.get("paragraph_anchor"),
                transcript_version_id=p.get("transcript_version_id"),
                content_item_id=p.get("content_item_id"),
                content_version_id=p.get("content_version_id"),
                category_key=p.get("category_key"),
                rrf_score=parent_rrf.get(pid, 0.0),
            )
        )
    return out


def retrieve(
    query: str,
    top_k: int = FINAL_TOP_K,
    categories: list[str] | None = None,
    *,
    visibility: PublishedTranscriptVisibilityPort | None = None,
) -> list[RetrievedParent]:
    snapshot = (visibility or _DEFAULT_VISIBILITY).snapshot()
    content_snapshot = _DEFAULT_CONTENT_VISIBILITY.snapshot()
    category_labels = _managed_category_labels()
    scored, child_rrf = _recall_scored(
        query, categories, snapshot, content_snapshot
    )
    if not scored:
        return []
    return _dedup_to_parents(
        scored, child_rrf, top_k, snapshot, content_snapshot, category_labels
    )


def _cap_children_for_rerank(
    per_sub_children: list[list[str]],
    fused: dict[str, float],
    cap: int,
) -> list[str]:
    """Pick <= cap child_ids to send to the reranker, without starving a side.

    The multi-query union can exceed the rerank service's batch limit. We can't
    just take the global top-cap by fused score — that may drop a whole
    sub-query's head and re-create the "only one side retrieved" problem. So:
      1. Reserve the head of EACH sub-query: cap // n_sub ids per side (deduped).
      2. Fill the remaining slots by fused RRF score, best-first.
    Returns a de-duplicated list of <= cap child_ids. If the union already fits,
    the caller skips this and keeps every id.
    """
    n_sub = max(1, len(per_sub_children))
    reserve_per_sub = max(1, cap // n_sub)
    picked: list[str] = []
    seen: set[str] = set()

    # 1. Per-sub-query head reservation.
    for ordered_ids in per_sub_children:
        taken = 0
        for cid in ordered_ids:
            if taken >= reserve_per_sub or len(picked) >= cap:
                break
            if cid in seen:
                continue
            seen.add(cid)
            picked.append(cid)
            taken += 1

    # 2. Fill the rest by fused score.
    if len(picked) < cap:
        rest = sorted(
            (cid for cid in fused if cid not in seen),
            key=lambda c: fused[c],
            reverse=True,
        )
        for cid in rest:
            if len(picked) >= cap:
                break
            seen.add(cid)
            picked.append(cid)

    return picked


def retrieve_multi(
    sub_queries: list[str],
    original_query: str,
    top_k: int = DECOMPOSE_FINAL_TOP_K,
    categories: list[str] | None = None,
    min_quota_per_subquery: int = DECOMPOSE_MIN_QUOTA_PER_SUBQUERY,
    *,
    visibility: PublishedTranscriptVisibilityPort | None = None,
) -> list[RetrievedParent]:
    """Comparison-intent multi-query retrieval.

    Runs each sub-query's recall independently, then fuses:
      1. Cross-query RRF over each sub-query's child ranking (rank-based, so we
         never compare raw scores across channels).
      2. A per-sub-query minimum quota of parents, guaranteeing each side of a
         comparison survives even if another side scores higher globally.
      3. A final global rerank against the ORIGINAL user query so the returned
         order reflects the user's actual intent, then trim to `top_k`.

    Returns the same `list[RetrievedParent]` shape as `retrieve()`, so all
    downstream stages (carry-forward, generate, budget, UI) are unchanged.
    Falls back to single-query `retrieve(original_query)` when fewer than two
    usable sub-queries are provided.
    """
    subs = [s.strip() for s in sub_queries if s and s.strip()]
    if len(subs) < 2:
        return retrieve(original_query, top_k=top_k, categories=categories, visibility=visibility)

    # ── 1. Per-sub-query recall + cross-query RRF over child rankings ──────────
    RRF_K = 60  # standard RRF damping constant
    child_rrf_fused: dict[str, float] = {}
    child_point: dict[str, object] = {}
    # Track which sub-query each child was first seen in (0-based).
    child_to_subq: dict[str, int] = {}
    # Ordered child_ids per sub-query, best-first (post-rerank order).
    per_sub_children: list[list[str]] = []

    snapshot = (visibility or _DEFAULT_VISIBILITY).snapshot()
    content_snapshot = _DEFAULT_CONTENT_VISIBILITY.snapshot()
    category_labels = _managed_category_labels()
    for si, sq in enumerate(subs):
        scored, _child_rrf = _recall_scored(
            sq, categories, snapshot, content_snapshot
        )
        ordered_ids: list[str] = []
        for rank, (point, _score) in enumerate(scored):
            cid = str(point.id)
            ordered_ids.append(cid)
            child_point.setdefault(cid, point)
            if cid not in child_to_subq:
                child_to_subq[cid] = si
            child_rrf_fused[cid] = child_rrf_fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        per_sub_children.append(ordered_ids)

    if not child_point:
        return []

    # ── 2. Build a fused child ordering, then re-rank against ORIGINAL query ───
    # Cap the union before rerank: the GPU rerank service rejects > MAX_BATCH_SIZE
    # passages in one call, and the multi-query union can exceed it. Reserve each
    # sub-query's head so no side is starved, then fill by fused score.
    all_child_ids = list(child_point.keys())
    if len(all_child_ids) > RERANK_BATCH_CAP:
        all_child_ids = _cap_children_for_rerank(
            per_sub_children, child_rrf_fused, RERANK_BATCH_CAP
        )
    points = [child_point[cid] for cid in all_child_ids]

    if RERANK_ENABLED:
        if RERANK_USE_HEADER:
            passages = [
                f"{p.payload.get('doc_title','')} > "
                f"{p.payload.get('section_path','')}\n\n"
                f"{p.payload.get('text','')}"
                for p in points
            ]
        else:
            passages = [p.payload["text"] for p in points]
        ce_scores = rerank_scores(original_query, passages)
        global_score = {cid: float(s) for cid, s in zip(all_child_ids, ce_scores)}
    else:
        # No cross-encoder: fall back to fused RRF as the global score.
        global_score = {cid: child_rrf_fused[cid] for cid in all_child_ids}

    # ── 3. Quota pass: reserve top parents per sub-query, then fill by global ──
    # First, map each child to its parent and remember, per sub-query, the
    # parent order (using that sub-query's own best-first child ranking).
    def _pid(cid: str) -> str:
        return child_point[cid].payload["parent_id"]

    selected_pids: list[str] = []
    seen_pids: set[str] = set()

    # 3a. Quota — walk each sub-query's ranking, admit up to N distinct parents.
    for ordered_ids in per_sub_children:
        admitted = 0
        for cid in ordered_ids:
            if admitted >= min_quota_per_subquery:
                break
            pid = _pid(cid)
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            selected_pids.append(pid)
            admitted += 1

    # 3b. Fill remaining slots by global (original-query) rerank score.
    remaining_children = sorted(
        (cid for cid in all_child_ids if _pid(cid) not in seen_pids),
        key=lambda cid: global_score[cid],
        reverse=True,
    )
    for cid in remaining_children:
        if len(selected_pids) >= top_k:
            break
        pid = _pid(cid)
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        selected_pids.append(pid)

    selected_pids = selected_pids[:top_k]
    final_pids = set(selected_pids)

    # ── 4. Build a `scored`-shaped list over ONLY the finally-selected parents,
    # ordered best-first by global (original-query) score, then reuse the shared
    # dedupe/expansion so time/rrf/snippet semantics match the single-query path.
    # Because there are at most `top_k` distinct selected parents, the cap inside
    # `_dedup_to_parents` admits ALL of them — quota-reserved parents can't be
    # squeezed out by global score here; global score only sets their order.
    scored_children = sorted(
        (cid for cid in all_child_ids if _pid(cid) in final_pids),
        key=lambda cid: global_score[cid],
        reverse=True,
    )
    scored = [(child_point[cid], global_score[cid]) for cid in scored_children]
    child_rrf = {cid: child_rrf_fused[cid] for cid in all_child_ids}

    out = _dedup_to_parents(
        scored, child_rrf, top_k, snapshot, content_snapshot, category_labels
    )

    # Tag each returned parent with the sub-query it belongs to, so
    # `_build_context` can interleave sources and keep every comparison side in
    # the budget. A parent's sub-query = that of its best-scoring (first-admitted)
    # child, which is the child appearing earliest in `scored_children`.
    pid_to_subq: dict[str, int] = {}
    for cid in scored_children:
        pid = _pid(cid)
        if pid not in pid_to_subq:
            pid_to_subq[pid] = child_to_subq.get(cid, 0)
    return [replace(p, subquery_idx=pid_to_subq.get(p.parent_id)) for p in out]
