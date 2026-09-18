"""Tests for ordinal/section enumeration intent (第N个视频 / 1-N / 第一到第N /
各章节) and its routing to the enumeration retrieval path."""
from __future__ import annotations

from src.intent import Intent, classify_intent, is_enumeration_intent


def test_ordinal_video_patterns():
    assert is_enumeration_intent("第十个视频是什么")
    assert is_enumeration_intent("第10个视频是什么")
    assert is_enumeration_intent("第1个视频讲了什么")
    assert classify_intent("第十个视频是什么") is Intent.ENUMERATE


def test_range_patterns():
    assert is_enumeration_intent("把1-10讲了什么列出来")
    assert is_enumeration_intent("1-10 讲了什么")
    assert is_enumeration_intent("3-5 内容是什么")
    assert classify_intent("把1-10讲了什么列出来") is Intent.ENUMERATE


def test_chinese_range_patterns():
    assert is_enumeration_intent("第一到第十个视频分别讲什么")
    assert is_enumeration_intent("第二到第五个章节内容是什么")
    assert classify_intent("第一到第十个视频分别讲什么") is Intent.ENUMERATE


def test_each_section_patterns():
    assert is_enumeration_intent("各章节分别讲什么")
    assert is_enumeration_intent("各个视频分别讲了什么")
    assert classify_intent("各章节分别讲什么") is Intent.ENUMERATE


def test_ordinary_questions_stay_fact():
    assert not is_enumeration_intent("机电管综是什么")
    assert not is_enumeration_intent("虹吸雨水管的处理原则是什么")
    assert not is_enumeration_intent("中心模型完成后的操作流程")
    assert classify_intent("机电管综是什么") is Intent.FACT


def test_fresh_retrieve_routes_section_enum_to_enumeration_topk(monkeypatch):
    from src import session as session_mod

    calls: list[dict] = []

    def fake_retrieve(query, top_k=5, categories=None, **kwargs):
        calls.append({"query": query, "top_k": top_k, "doc_types": kwargs.get("doc_types")})
        return []

    def fake_titles(*_a, **_k):
        return []

    monkeypatch.setattr(session_mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(session_mod, "retrieve_enumeration_titles", fake_titles)
    monkeypatch.setattr(session_mod, "ENUMERATION_TOP_K", 16)

    s = session_mod.ChatSession()
    s._fresh_retrieve("第十个视频是什么", categories=None, enumeration=True)
    assert calls and calls[0]["top_k"] == 16
    assert calls[0]["doc_types"] == ["transcript"]


def test_ordinal_followup_skips_rewrite_and_anchors_to_last_user_series(monkeypatch):
    """An ordinal/section follow-up must NOT run the rewrite LLM (it anchored
    to an assistant-cited title); it anchors to the last user question's series."""
    from src import session as session_mod

    s = session_mod.ChatSession()
    s.state.append_turn("机电管综培训有哪十个章节", "根据资料…[1]", sources_for_ui=[], policy_snapshot={})

    called = {"n": 0}

    def fake_rewrite(*_a, **_k):
        called["n"] += 1
        return "《20250702早上培训（要求、流程解读、命名标准）》[1]的内容是什么"

    monkeypatch.setattr(session_mod, "rewrite_query", fake_rewrite)
    resolution, _t = s._resolve_search_query("把1-10讲了什么列出来")
    assert called["n"] == 0  # rewrite skipped
    assert "机电管综培训" in resolution.standalone_query
    assert "把1-10讲了什么列出来" in resolution.standalone_query
    assert resolution.fallback_reason == "ordinal_reanchor"


def test_ordinal_anchor_prefers_previous_search_query(monkeypatch):
    """When the prior user question is open-ended, anchor on the previous turn's
    search query if it carries the concrete series the assistant retrieved."""
    from src import session as session_mod

    s = session_mod.ChatSession()
    s.state.append_turn("有什么相关的培训吗", "根据公司知识库…机电管综培训…[1]", sources_for_ui=[], policy_snapshot={})
    s.state.last_search_query = "机电管综培训 相关的培训"

    def unexpected_rewrite(*_a, **_k):
        raise AssertionError("rewrite must not be called for ordinal follow-up")

    monkeypatch.setattr(session_mod, "rewrite_query", unexpected_rewrite)
    resolution, _t = s._resolve_search_query("把1-10讲了什么列出来")
    assert "机电管综培训" in resolution.standalone_query
    assert "把1-10讲了什么列出来" in resolution.standalone_query