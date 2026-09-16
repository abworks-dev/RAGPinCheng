"""Tests for src/transcript_ai helpers and the transcript AI admin routes.

LLM calls are never executed: the helper tests use fake ``OpenAI``-like
clients, and the route tests monkeypatch ``generate_summary`` /
``generate_review_suggestions``.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from src import transcript_ai
from src.transcript_ai import (
    ReviewSuggestion,
    ReviewSuggestions,
    TranscriptSummary,
    _parse_review_json,
    _parse_summary_parts,
    _strip_json_fences,
    generate_review_suggestions,
    generate_summary,
    talk,
)
from src.transcript_ai import TranscriptAICallableError


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.responses.pop(0)
        resp = SimpleNamespace(choices=[_FakeChoice(content)], usage=None)
        return resp


class _FakeChat:
    def __init__(self, responses: list[str]) -> None:
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = _FakeChat(responses)


SAMPLE_TRANSCRIPT = (
    "说话人 1 00:00:01\n大家好，这个视频讲的是暖通机房建模。\n\n"
    "说话人 2 00:01:00\n嗯，然后广进是三百，就是管径的意思。\n\n"
    "说话人 3 00:02:00\n这里要注意阀门的型号，这个这个不要搞错。\n"
)


def test_parse_summary_parts_basic():
    raw = "1. 讲解暖通机房建模流程\n2. 说明管径是三百\n⚠ 内容与资料名称可能不符：内容偏管道\n"
    summary = _parse_summary_parts(raw)
    assert summary.points == ["讲解暖通机房建模流程", "说明管径是三百"]
    assert summary.mismatch_note is not None


def test_parse_summary_parts_requires_points():
    with pytest.raises(TranscriptAICallableError):
        _parse_summary_parts("")


def test_strip_json_fences():
    raw = "```json\n{\"suggestions\": []}\n```"
    assert _strip_json_fences(raw) == '{"suggestions": []}'


def test_parse_review_json_valid():
    raw = json_str({
        "suggestions": [
            {"timestamp": "00:01:00", "original": "广进", "corrected": "管径", "reason": "同音字", "confidence": "high"},
        ]
    })
    review = _parse_review_json(raw)
    assert len(review.suggestions) == 1
    assert review.suggestions[0].corrected == "管径"
    assert review.suggestions[0].confidence == "high"


def test_parse_review_json_empty_list_is_valid():
    review = _parse_review_json('{"suggestions": []}')
    assert review.suggestions == []


def test_parse_review_json_invalid():
    with pytest.raises(TranscriptAICallableError):
        _parse_review_json("not json at all")
    with pytest.raises(TranscriptAICallableError):
        _parse_review_json('{"suggestions": "nope"}')
    with pytest.raises(TranscriptAICallableError):
        _parse_review_json('{"suggestions": [{"original": "x"}]}')


def test_parse_review_json_unknown_confidence_defaults_low():
    raw = json_str({
        "suggestions": [
            {"timestamp": "00:01:00", "original": "a", "corrected": "b", "reason": "r", "confidence": "nope"},
        ]
    })
    review = _parse_review_json(raw)
    assert review.suggestions[0].confidence == "low"


def test_generate_summary_happy_path(monkeypatch):
    client = _FakeClient(["1. 要点一\n2. 要点二\n"])
    monkeypatch.setattr(transcript_ai, "_client", lambda: client)
    summary = generate_summary(media_title="暖通机房建模", transcript=SAMPLE_TRANSCRIPT)
    assert summary.points == ["要点一", "要点二"]


def test_generate_summary_degraded_on_transport_error(monkeypatch):
    def boom():
        raise RuntimeError("connect failed")
    monkeypatch.setattr(transcript_ai, "_client", boom)
    with pytest.raises(TranscriptAICallableError) as exc:
        generate_summary(media_title="x", transcript=SAMPLE_TRANSCRIPT)
    assert exc.value.code == "ai_service_unavailable"


def test_generate_review_suggestions_happy(monkeypatch):
    raw = json_str({"suggestions": [
        {"timestamp": "00:01:00", "original": "广进", "corrected": "管径", "reason": "同音字", "confidence": "high"},
    ]})
    client = _FakeClient([raw])
    monkeypatch.setattr(transcript_ai, "_client", lambda: client)
    review = generate_review_suggestions(media_title="x", summary=None, transcript=SAMPLE_TRANSCRIPT)
    assert review.suggestions[0].original == "广进"


def test_talk_summary_uses_history(monkeypatch):
    client = _FakeClient(["1. 修正后要点\n"])
    monkeypatch.setattr(transcript_ai, "_client", lambda: client)
    result = talk(
        kind="summary",
        media_title="x",
        transcript=SAMPLE_TRANSCRIPT,
        history=[{"role": "user", "content": "第一点不准确"}, {"role": "assistant", "content": "1. 旧要点"}],
    )
    assert result.kind == "summary"
    assert result.summary.points == ["修正后要点"]
    # The system prompt must be present and history passed through.
    messages = client.chat.completions.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "转录稿" in messages[0]["content"] or "资料名称" in messages[0]["content"]
    assert any(m["role"] == "user" and "第一点不准确" in m["content"] for m in messages)
    assert any(m["role"] == "assistant" and "旧要点" in m["content"] for m in messages)


def test_talk_review_uses_history(monkeypatch):
    raw = json_str({"suggestions": []})
    client = _FakeClient([raw])
    monkeypatch.setattr(transcript_ai, "_client", lambda: client)
    result = talk(
        kind="review",
        media_title="x",
        transcript=SAMPLE_TRANSCRIPT,
        history=[{"role": "user", "content": "别改那么多"}, {"role": "assistant", "content": '{"suggestions": []}'}],
    )
    assert result.kind == "review"
    assert result.review.suggestions == []


# ── routes ──────────────────────────────────────────────────────────────────


def _make_summary_dto() -> TranscriptSummary:
    return TranscriptSummary(points=["要点一", "要点二"], mismatch_note=None)


def test_routes_enforce_admin(monkeypatch):
    from api.main import app as real_app

    import api.routes_transcript_ai as routes

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.testclient import TestClient as TC

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)

    resp = client.post(
        "/api/admin/transcription/versions/11111111-1111-4111-8111-111111111111/summary",
        json={"base_markdown_sha256": "a" * 64},
    )
    assert resp.status_code == 401
    resp2 = client.post(
        "/api/admin/transcription/media/bulk-review-optimize",
        json={"items": []},
    )
    assert resp2.status_code == 401


def test_apply_suggestions_preserves_timestamps():
    from api.routes_transcript_ai import apply_suggestions

    review = ReviewSuggestions(suggestions=[
        ReviewSuggestion(timestamp="00:01:00", original="广进", corrected="管径", reason="同音", confidence="high"),
        ReviewSuggestion(timestamp="00:02:00", original="这个这个", corrected="", reason="语气词", confidence="high"),
    ])
    applied = apply_suggestions(SAMPLE_TRANSCRIPT, review)
    assert "管径" in applied and "广进" not in applied
    assert "这个这个" not in applied
    # Timestamps + 说话人 markers survive.
    assert "说话人 2 00:01:00" in applied
    assert "说话人 3 00:02:00" in applied


def test_apply_suggestions_skips_unmatched():
    from api.routes_transcript_ai import apply_suggestions

    review = ReviewSuggestions(suggestions=[
        ReviewSuggestion(timestamp="00:09:00", original="不存在", corrected="替换", reason="r", confidence="high"),
    ])
    assert apply_suggestions(SAMPLE_TRANSCRIPT, review) == SAMPLE_TRANSCRIPT


def json_str(obj: dict) -> str:
    import json as _json
    return _json.dumps(obj, ensure_ascii=False)