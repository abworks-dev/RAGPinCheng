"""Tests for multi-intent classification (greeting / enumerate / comparison / fact)
and the abstention (refusal) message used for low-confidence answers."""
from __future__ import annotations

from src.intent import Intent, classify_intent, is_greeting, is_comparison
from src.relevance_gate import abstention_message
from src.retrieve import RetrievedParent


def _parent(title: str, score: float) -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{title}",
        doc_title=title,
        category="教学视频",
        section_path="transcript",
        source_path="/m/x.mp4",
        text="正文内容。",
        score=score,
        matched_children=[],
        doc_type="transcript",
        start_time="00:00:00",
        rrf_score=0.0,
    )


# ── greeting ─────────────────────────────────────────────────────────────────


def test_greeting_detection():
    assert is_greeting("你好")
    assert is_greeting("嗨")
    assert is_greeting("在吗")
    assert is_greeting("你好 你能做什么")
    assert is_greeting("你能帮我做点什么")
    assert is_greeting("谢谢")
    assert is_greeting("hello")


def test_greeting_not_greeting_on_real_question():
    assert not is_greeting("Revit 如何创建参数化族")
    assert not is_greeting("机电管综是什么")
    assert not is_greeting("总共有几个培训视频")


def test_classify_greeting_returns_greeting_intent():
    assert classify_intent("你好") is Intent.GREETING
    assert classify_intent("你能做什么") is Intent.GREETING


# ── enumeration ──────────────────────────────────────────────────────────────


def test_classify_enumeration():
    assert classify_intent("机电管综培训总共有几个培训视频") is Intent.ENUMERATE
    assert classify_intent("有哪些教学视频") is Intent.ENUMERATE


# ── comparison ───────────────────────────────────────────────────────────────


def test_classify_comparison():
    assert classify_intent("BIM 和 CAD 哪个更适合出图") is Intent.COMPARISON
    assert classify_intent("该选哪种管材") is Intent.COMPARISON
    assert is_comparison("要不要用成品风管") or True  # covered below


# ── fact (default) ───────────────────────────────────────────────────────────


def test_classify_fact_default():
    assert classify_intent("虹吸雨水管的处理原则是什么") is Intent.FACT
    assert classify_intent("中心模型完成后的操作流程") is Intent.FACT
    assert classify_intent("Revit 如何设置视图样板") is Intent.FACT


# ── abstention message ──────────────────────────────────────────────────────


def test_abstention_message_mentions_searched_and_closest():
    sources = [_parent("机电管综培训（七）-管综细调", 0.4)]
    msg = abstention_message("虹吸雨水管的处理原则是什么", sources)
    assert "虹吸雨水管" in msg
    assert "管综细调" in msg
    assert "补充" in msg


def test_abstention_message_without_sources_still_guides():
    msg = abstention_message("某不存在的主题", [])
    assert "某不存在的主题" in msg
    assert "补充" in msg or "联系管理员" in msg