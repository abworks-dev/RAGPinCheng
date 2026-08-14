"""Default-off relevance gate for answer generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import (
    RELEVANCE_GATE_ENABLED,
    RELEVANCE_GATE_MIN_RRF,
    RELEVANCE_GATE_MIN_SCORE,
    RELEVANCE_GATE_POLICY_VERSION,
    RERANKER_MODEL,
    RERANK_PROVIDER,
)
from .retrieve import RetrievedParent

LOW_CONFIDENCE_MESSAGE = "未找到足够相关的资料。请补充具体的构件、规范、软件操作或项目资料名称。"

@dataclass(frozen=True)
class RelevanceDecision:
    action: str
    reason: str
    eligible: bool
    enabled: bool
    policy_version: str
    rerank_provider: str
    reranker_model: str
    source_count: int
    top1_score: float | None
    top2_score: float | None
    score_margin: float | None
    top1_rrf: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_relevance(
    sources: list[RetrievedParent],
    *,
    has_history: bool,
    decomposition_applied: bool,
) -> RelevanceDecision:
    ranked = sorted(sources, key=lambda p: p.score, reverse=True)
    top1 = float(ranked[0].score) if ranked else None
    top2 = float(ranked[1].score) if len(ranked) > 1 else None
    margin = top1 - top2 if top1 is not None and top2 is not None else None
    top1_rrf = float(ranked[0].rrf_score) if ranked else None
    eligible = bool(ranked) and not has_history and not decomposition_applied
    action = "allow"
    reason = "disabled"
    if not ranked:
        reason = "no_sources"
    elif not eligible:
        reason = "ineligible_path"
    elif not RELEVANCE_GATE_ENABLED:
        reason = "disabled"
    elif top1 is not None and top1 < RELEVANCE_GATE_MIN_SCORE:
        action, reason = "low_confidence", "top1_score"
    elif top1_rrf is not None and top1_rrf < RELEVANCE_GATE_MIN_RRF:
        action, reason = "low_confidence", "top1_rrf"
    else:
        reason = "thresholds_passed"

    return RelevanceDecision(
        action=action,
        reason=reason,
        eligible=eligible,
        enabled=RELEVANCE_GATE_ENABLED,
        policy_version=RELEVANCE_GATE_POLICY_VERSION,
        rerank_provider=RERANK_PROVIDER,
        reranker_model=RERANKER_MODEL,
        source_count=len(ranked),
        top1_score=top1,
        top2_score=top2,
        score_margin=margin,
        top1_rrf=top1_rrf,
    )
