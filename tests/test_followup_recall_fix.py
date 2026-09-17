"""Tests for follow-up recall fix: teaching-video enumeration restricts recall
to transcripts so "详细点列出教学视频" is not diluted by spec pdfs/pptx."""
from __future__ import annotations

from src.session import _teaching_video_doc_types


def test_teaching_video_signal_returns_transcript_filter():
    assert _teaching_video_doc_types("有哪些教学视频") == ["transcript"]
    assert _teaching_video_doc_types("总共有哪些培训视频") == ["transcript"]
    assert _teaching_video_doc_types("知识库中有哪些教学视频，请详细列出每个视频的内容和主题") == ["transcript"]


def test_section_or_spec_signal_leaves_filter_open():
    assert _teaching_video_doc_types("有哪些章节需要学习") is None
    assert _teaching_video_doc_types("GB 50016 有哪些规范条文") is None
    assert _teaching_video_doc_types("列出教学视频的相关规范清单") is None


def test_no_video_signal_returns_none():
    assert _teaching_video_doc_types("虹吸雨水管的处理原则是什么") is None
    assert _teaching_video_doc_types("中心模型完成后的操作流程") is None
    assert _teaching_video_doc_types("") is None


def test_fresh_retrieve_passes_doc_types_for_teaching_video_enumeration(monkeypatch):
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
    s._fresh_retrieve("有哪些教学视频", categories=None, enumeration=True)
    assert calls and calls[0]["doc_types"] == ["transcript"]

    s2 = session_mod.ChatSession()
    s2._fresh_retrieve("GB 50016 有哪些规范条文", categories=None, enumeration=False)
    assert calls[-1]["doc_types"] is None