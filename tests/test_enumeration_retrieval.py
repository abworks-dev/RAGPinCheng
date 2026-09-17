"""Tests for enumeration/count retrieval intent, the wider top-k path, and the
compact title-inventory context used to answer "list/count" questions."""
from __future__ import annotations

from src.generate import _build_enumeration_context
from src.intent import is_enumeration_intent
from src.retrieve import RetrievedParent


def _parent(index: int, *, doc_type: str = "transcript") -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{index}",
        doc_title=f"机电管综培训（{index}）",
        category="教学视频",
        section_path="transcript" if doc_type == "transcript" else f"第 {index} 节",
        source_path=f"/media/video-{index}.mp4",
        text=f"说话人 1 00:00:0{index}\n这是视频 {index} 的正文内容，描述管路调整步骤。\n",
        score=1.0,
        matched_children=["utterance"],
        doc_type=doc_type,
        start_time=f"00:00:0{index}" if doc_type == "transcript" else None,
        media_id=f"media-{index}",
    )


# ── intent detection ─────────────────────────────────────────────────────────


def test_enumeration_intent_matches_count_words():
    assert is_enumeration_intent("机电管综培训总共有几个培训视频")
    assert is_enumeration_intent("一共有多少个培训视频")
    assert is_enumeration_intent("分别列出所有章节")
    assert is_enumeration_intent("都有哪些教学视频")
    assert is_enumeration_intent("总共几个视频")


def test_enumeration_intent_matches_standalone_rewrite():
    # A vague follow-up that the rewrite expands to an enumeration form.
    assert is_enumeration_intent("继续说", standalone_query="机电管综培训总共有几个培训视频")


def test_enumeration_intent_does_not_match_plain_fact_question():
    assert not is_enumeration_intent("管综培训里的管道避让原则是什么")
    assert not is_enumeration_intent("如何做净高检查")
    assert not is_enumeration_intent("Revit 怎么创建族")


def test_enumeration_intent_ignores_short_fill():
    assert not is_enumeration_intent("")


# ── enumeration context builder ──────────────────────────────────────────────


def test_enumeration_context_lists_every_title_as_compact_source():
    parents = [_parent(i) for i in range(1, 9)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    assert len(used) == 8
    assert context.count("<source") == 8
    for i in range(1, 9):
        assert f'doc="机电管综培训（{i}）"' in context
        assert f'index="{i}"' in context


def test_enumeration_context_indexes_match_used_order():
    parents = [_parent(i) for i in range(1, 6)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    for i, p in enumerate(used, start=1):
        assert f'index="{i}"' in context
        assert f'doc="{p.doc_title}"' in context


def test_enumeration_context_first_two_keep_full_text():
    parents = [_parent(i) for i in range(1, 6)]
    context, used = _build_enumeration_context(parents, budget=10_000)
    assert "这是视频 1 的正文内容" in context
    assert "这是视频 2 的正文内容" in context
    # The remaining sources are compact inventory entries.
    assert context.count("（条目）") == 3


def test_enumeration_context_respects_budget_truncation():
    parents = [_parent(i) for i in range(1, 10)]
    context, used = _build_enumeration_context(parents, budget=400)
    # Small budget still admits multiple compact titles + a couple passages.
    assert 1 <= len(used) <= 9
    assert len(context) <= 400


def test_enumeration_context_non_transcript_uses_section():
    parents = [_parent(1, doc_type="pdf")]
    context, _ = _build_enumeration_context(parents, budget=10_000)
    assert 'type="transcript"' not in context
    assert 'section="第 1 节"' in context


# ── session _fresh_retrieve top_k routing ────────────────────────────────────


def test_fresh_retrieve_uses_enumeration_top_k(monkeypatch):
    from src import session as session_mod
    from src.config import ENUMERATION_TOP_K, FINAL_TOP_K

    calls: list[dict] = []

    def fake_retrieve(query, top_k=FINAL_TOP_K, categories=None):
        calls.append({"query": query, "top_k": top_k, "categories": categories})
        return []

    monkeypatch.setattr(session_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(session_mod, "QUERY_DECOMPOSE_ENABLED", False)

    s = session_mod.ChatSession()
    s._fresh_retrieve("机电管综培训总共有几个培训视频", categories=None, enumeration=True)
    assert calls == [{"query": "机电管综培训总共有几个培训视频", "top_k": ENUMERATION_TOP_K, "categories": None}]


def test_fresh_retrieve_uses_default_top_k_for_plain_question(monkeypatch):
    from src import session as session_mod
    from src.config import FINAL_TOP_K

    calls: list[dict] = []

    def fake_retrieve(query, top_k=FINAL_TOP_K, categories=None):
        calls.append({"query": query, "top_k": top_k})
        return []

    monkeypatch.setattr(session_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(session_mod, "QUERY_DECOMPOSE_ENABLED", False)

    s = session_mod.ChatSession()
    s._fresh_retrieve("管道避让原则是什么", categories=None, enumeration=False)
    assert calls[0]["top_k"] == FINAL_TOP_K