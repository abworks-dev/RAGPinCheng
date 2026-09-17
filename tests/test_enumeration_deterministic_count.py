"""Tests for deterministic enumeration count injection.

The answer must report the exact number of distinct inventory items computed
from retrieval (never recomputed by the LLM, which was observed to drift:
4 / 9 / 10 for the same list)."""
from __future__ import annotations

from src.generate import _prepare_generation, _build_enumeration_context
from src.retrieve import RetrievedParent


def _parent(index: int) -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{index}",
        doc_title=f"机电管综培训（{index}）",
        category="教学视频",
        section_path="transcript",
        source_path=f"/m/v{index}.mp4",
        text=f"说话人 1 00:00:0{index}\n视频 {index} 正文\n",
        score=1.0,
        matched_children=[],
        doc_type="transcript",
        start_time=f"00:00:0{index}",
        media_id=f"media-{index}",
        rrf_score=0.0,
    )


def test_enumeration_prep_injects_deterministic_total():
    parents = [_parent(i) for i in range(1, 10)]  # 9 distinct videos
    prep = _prepare_generation(
        "机电管综培训总共有几个培训视频",
        parents,
        history=None,
        budget=10_000,
        enumeration=True,
    )
    user_msg = prep.messages[-1]["content"]
    assert "条目总数（以此为准，请如实列出并引用）：9" in user_msg


def test_non_enumeration_prep_has_no_total_line():
    parents = [_parent(1)]
    prep = _prepare_generation(
        "虹吸雨水管的处理原则是什么",
        parents,
        history=None,
        budget=10_000,
        enumeration=False,
    )
    user_msg = prep.messages[-1]["content"]
    assert "条目总数" not in user_msg


def test_enumeration_context_distinct_docs_equals_injected_total():
    parents = [_parent(1) for _ in range(3)] + [_parent(2), _parent(3)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    total = len({p.doc_title for p in used})
    assert total == 3  # deduped by doc_title