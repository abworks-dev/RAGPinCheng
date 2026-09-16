"""AI assistants for video transcript workbench.

Three admin-facing conveniences built on the shared Zhipu (OpenAI-compatible)
call path used by answer generation / table summarization:

1. ``generate_summary`` — bullet-point video summary from title + transcript.
2. ``generate_review_suggestions`` — homophone / filler-word correction
   suggestions (structured JSON) from title + summary + transcript.
3. ``talk`` — regenerate either a summary or the suggestions given a bounded
   correction dialog, so the admin can point out inaccuracies and get a full
   fresh result (never an incremental diff).

Every entry point degrades to a structured error instead of raising, so the
workbench stays usable when the LLM is unreachable or returns unparseable
output.  Token/character budgets keep long transcripts inside the model window.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Literal

import httpx
from openai import OpenAI

from .config import (
    LLM_MODEL,
    TRANSCRIPT_AI_MAX_CHARS,
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
)
from .external_usage import record_usage
from .prompts import load_prompt

# Structured failure reasons surfaced to the UI (never raw stack traces).
AI_DEGRADED = "ai_service_unavailable"
AI_PARSE_FAILED = "ai_output_unparseable"
AI_INPUT_EMPTY = "ai_input_empty"

_SYSTEM_PROMPTS: dict[str, str] = {}


def _system_prompt(kind: str) -> str:
    key = {"summary": "transcript_summary_system", "review": "transcript_review_system"}[kind]
    if key not in _SYSTEM_PROMPTS:
        _SYSTEM_PROMPTS[key] = load_prompt(key)
    return _SYSTEM_PROMPTS[key]


class TranscriptAICallableError(Exception):
    """Raised when an AI call fails or its output cannot be parsed.

    ``code`` is one of the AI_* constants above and is safe to show in the UI.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TranscriptSummary:
    points: list[str]
    mismatch_note: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewSuggestion:
    timestamp: str
    original: str
    corrected: str
    reason: str
    confidence: str


@dataclass(frozen=True, slots=True)
class ReviewSuggestions:
    suggestions: list[ReviewSuggestion]


@dataclass(frozen=True, slots=True)
class TranscriptAIResult:
    kind: Literal["summary", "review"]
    summary: TranscriptSummary | None = None
    review: ReviewSuggestions | None = None


_SuggestionSchema = dict[str, object]


def _client() -> OpenAI:
    if not ZHIPU_API_KEY:
        raise TranscriptAICallableError(AI_DEGRADED)
    try:
        return OpenAI(
            api_key=ZHIPU_API_KEY,
            base_url=ZHIPU_BASE_URL,
            timeout=httpx.Timeout(120.0, connect=30.0, read=120.0, write=30.0),
        )
    except Exception:
        # Construction does not normally fail, but treat any setup error as
        # an unavailable service rather than letting it escape to callers.
        raise TranscriptAICallableError(AI_DEGRADED)


def _truncate(transcript: str) -> str:
    if len(transcript) <= TRANSCRIPT_AI_MAX_CHARS:
        return transcript
    return transcript[:TRANSCRIPT_AI_MAX_CHARS] + "\n...(转录稿过长，已截断)"


def _call(
    client: OpenAI | None,
    *,
    kind: Literal["summary", "review"],
    user_body: str,
    history: list[dict] | None = None,
) -> str:
    """Run one LLM call for the given assistant kind; returns raw text.

    Raises TranscriptAICallableError(AI_DEGRADED) on any transport/API error.
    """
    operation = "transcript_ai"
    started = perf_counter()
    try:
        client = client or _client()
        messages: list[dict] = [{"role": "system", "content": _system_prompt(kind)}]
        if history:
            for m in history:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_body})
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception:
        record_usage("zhipu", operation, success=False, latency_ms=int((perf_counter() - started) * 1000))
        raise TranscriptAICallableError(AI_DEGRADED)
    usage = getattr(resp, "usage", None)
    usage_dict = {key: int(getattr(usage, key, 0) or 0) for key in
                  ("prompt_tokens", "completion_tokens", "total_tokens")} if usage else {}
    record_usage("zhipu", operation, usage=usage_dict, latency_ms=int((perf_counter() - started) * 1000))
    return (resp.choices[0].message.content or "").strip()


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` fences and stray whitespace the model may add."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_summary_parts(raw: str) -> TranscriptSummary:
    mismatch_note: str | None = None
    points: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("⚠"):
            mismatch_note = line[1:].strip()
            continue
        numbered = re.sub(r"^\s*\d+[\.、)]\s*", "", line)
        if numbered:
            points.append(numbered)
    if not points:
        raise TranscriptAICallableError(AI_PARSE_FAILED)
    return TranscriptSummary(points=points, mismatch_note=mismatch_note)


def generate_summary(
    *,
    media_title: str,
    transcript: str,
    history: list[dict] | None = None,
    client: OpenAI | None = None,
) -> TranscriptSummary:
    """Generate (or re-generate from a correction dialog) a video summary."""
    if not media_title.strip() or not transcript.strip():
        raise TranscriptAICallableError(AI_INPUT_EMPTY)
    user_body = (
        f"资料名称：{media_title.strip()}\n\n转录稿：\n{_truncate(transcript.strip())}\n\n"
        "请按规则输出视频要点总结。"
    )
    raw = _call(client, kind="summary", user_body=user_body, history=history)
    return _parse_summary_parts(raw)


def _parse_review_json(raw: str) -> ReviewSuggestions:
    cleaned = _strip_json_fences(raw)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        raise TranscriptAICallableError(AI_PARSE_FAILED)
    suggestions_raw = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(suggestions_raw, list):
        raise TranscriptAICallableError(AI_PARSE_FAILED)
    suggestions: list[ReviewSuggestion] = []
    for item in suggestions_raw:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("timestamp") or "")
        original = str(item.get("original") or "")
        corrected = str(item.get("corrected") or "")
        reason = str(item.get("reason") or "")
        confidence = str(item.get("confidence") or "low")
        if confidence not in ("high", "medium", "low"):
            confidence = "low"
        if not (timestamp and original and corrected):
            continue
        suggestions.append(
            ReviewSuggestion(
                timestamp=timestamp,
                original=original,
                corrected=corrected,
                reason=reason,
                confidence=confidence,
            )
        )
    if not suggestions:
        # An explicit empty list is a valid "nothing to fix" result.
        if isinstance(suggestions_raw, list) and len(suggestions_raw) == 0:
            return ReviewSuggestions(suggestions=[])
        raise TranscriptAICallableError(AI_PARSE_FAILED)
    return ReviewSuggestions(suggestions=suggestions)


def generate_review_suggestions(
    *,
    media_title: str,
    summary: TranscriptSummary | None,
    transcript: str,
    history: list[dict] | None = None,
    client: OpenAI | None = None,
) -> ReviewSuggestions:
    """Generate (or re-generate from a correction dialog) correction suggestions."""
    if not media_title.strip() or not transcript.strip():
        raise TranscriptAICallableError(AI_INPUT_EMPTY)
    summary_text = "\n".join(summary.points) if summary and summary.points else "（尚未生成总结）"
    user_body = (
        f"资料名称：{media_title.strip()}\n\n视频总结：\n{summary_text}\n\n"
        f"转录稿：\n{_truncate(transcript.strip())}\n\n请按规则找出值得修正的片段，只输出 JSON。"
    )
    raw = _call(client, kind="review", user_body=user_body, history=history)
    return _parse_review_json(raw)


def talk(
    *,
    kind: Literal["summary", "review"],
    media_title: str,
    transcript: str,
    history: list[dict],
    client: OpenAI | None = None,
) -> TranscriptAIResult:
    """Append the admin's latest correction to the dialog and regenerate a full result.

    ``history`` is the bounded dialog so far (user/assistant turns WITH the
    latest user correction already appended as the final message); the
    assistant output is always a fresh complete result, never an incremental
    diff.  Conversation state lives in the caller (frontend, not persisted).
    """
    if not media_title.strip() or not transcript.strip():
        raise TranscriptAICallableError(AI_INPUT_EMPTY)
    if kind == "summary":
        summary = generate_summary(
            media_title=media_title,
            transcript=transcript,
            history=history,
            client=client,
        )
        return TranscriptAIResult(kind="summary", summary=summary)
    review = generate_review_suggestions(
        media_title=media_title,
        summary=None,
        transcript=transcript,
        history=history,
        client=client,
    )
    return TranscriptAIResult(kind="review", review=review)