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
from .answer_policy import AnswerPolicy, POLICY_DEFAULT_VERSION
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
    policy: AnswerPolicy | None = None,
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
    # Keep environment fallback behavior observable for callers/tests that
    # override the legacy module constants. Persisted admin policies carry a
    # distinct version and take precedence for real requests.
    persisted = policy is not None and policy.policy_version != POLICY_DEFAULT_VERSION
    enabled = policy.relevance_gate_enabled if persisted else RELEVANCE_GATE_ENABLED
    min_score = policy.relevance_min_score if persisted else RELEVANCE_GATE_MIN_SCORE
    min_rrf = policy.relevance_min_rrf if persisted else RELEVANCE_GATE_MIN_RRF
    min_margin = policy.relevance_min_margin if persisted else 0.0
    if min_margin < 0:
        min_margin = 0.0
    if not ranked or not eligible:
        pass
    elif not enabled:
        reason = "disabled"
    elif top1 is not None and top1 < min_score:
        action, reason = "low_confidence", "top1_score"
    elif top1_rrf is not None and top1_rrf < min_rrf:
        action, reason = "low_confidence", "top1_rrf"
    elif margin is not None and margin < min_margin:
        action, reason = "low_confidence", "score_margin"
    elif enabled and eligible:
        reason = "thresholds_passed"

    return RelevanceDecision(
        action=action,
        reason=reason,
        eligible=eligible,
        enabled=enabled,
        policy_version=policy.policy_version if policy is not None else RELEVANCE_GATE_POLICY_VERSION,
        rerank_provider=RERANK_PROVIDER,
        reranker_model=RERANKER_MODEL,
        source_count=len(ranked),
        top1_score=top1,
        top2_score=top2,
        score_margin=margin,
        top1_rrf=top1_rrf,
    )
