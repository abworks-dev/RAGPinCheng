"""GLM-4 generation with cited sources, via Zhipu's OpenAI-compatible API."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

import httpx
from openai import OpenAI
from .external_usage import record_usage

from .config import (
    LLM_MODEL,
    LLM_REWRITE_MODEL,
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
)
from .answer_policy import AnswerPolicy, load_answer_policy
from .prompts import load_prompt, render_prompt
from .retrieve import RetrievedParent


_NUMBERED_CITATION_RE = re.compile(
    r"\[(\d+(?:\s*[,，、]\s*\d+)*)\](?!\()"
)


@dataclass
class Answer:
    text: str
    sources: list[RetrievedParent]
    messages: list[dict] | None = None  # exact messages sent to the LLM (for debugging)
    model: str | None = None
    context_chars: int = 0
    budget_used: int = 0   # chars actually packed into <sources> after budget trim
    budget: int = 0        # the budget passed in (for telemetry / regression detection)
    # Token usage from the provider (populated when the API returns it).
    # Keys: prompt_tokens, completion_tokens, total_tokens.
    usage: dict = field(default_factory=dict)
    citation_diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CitationFinalization:
    text: str
    sources: list[RetrievedParent]
    diagnostics: dict


@dataclass
class GenerationPrep:
    """Everything determined synchronously before the LLM call.

    Shared by the sync and streaming code paths so message construction and
    source-packing aren't duplicated.
    """
    used_sources: list[RetrievedParent]
    messages: list[dict]
    model: str
    context_chars: int
    budget: int
    policy: AnswerPolicy
    # Populated by the streaming iterator as it consumes the final
    # usage-bearing chunk. Empty until the stream is exhausted.
    usage: dict = field(default_factory=dict)


def finalize_answer_sources(
    text: str,
    candidate_sources: list[RetrievedParent],
) -> tuple[str, list[RetrievedParent]]:
    """Keep only sources cited by the final answer and renumber citations.

    Sources are ordered by first citation appearance so every published
    citation remains aligned with ``sources[N-1]``. Invalid source numbers are
    dropped instead of exposing unrelated retrieval candidates to the UI.
    """
    result = finalize_answer_sources_with_diagnostics(text, candidate_sources)
    return result.text, result.sources


def finalize_answer_sources_with_diagnostics(
    text: str,
    candidate_sources: list[RetrievedParent],
) -> CitationFinalization:
    """Normalize citations and retain non-sensitive citation quality signals."""
    source_indexes: list[int] = []
    source_number_by_index: dict[int, int] = {}
    invalid_numbers: set[int] = set()
    marker_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal marker_count
        rewritten: list[str] = []
        for raw_number in re.split(r"\s*[,，、]\s*", match.group(1)):
            marker_count += 1
            source_index = int(raw_number) - 1
            if source_index < 0 or source_index >= len(candidate_sources):
                invalid_numbers.add(int(raw_number))
                continue
            if source_index not in source_number_by_index:
                source_indexes.append(source_index)
                source_number_by_index[source_index] = len(source_indexes)
            marker = f"[{source_number_by_index[source_index]}]"
            if marker not in rewritten:
                rewritten.append(marker)
        return "".join(rewritten)

    normalized_text = _NUMBERED_CITATION_RE.sub(replace, text)
    cited_sources = [candidate_sources[index] for index in source_indexes]
    no_answer = "未找到相关内容" in normalized_text
    completed_statements = re.findall(r"[^。！？；\n]+[。！？；]", normalized_text)
    uncited_statement_count = sum(
        1 for statement in completed_statements
        if not _NUMBERED_CITATION_RE.search(statement)
        and len(re.sub(r"\s+", "", statement)) >= 4
    )
    if no_answer:
        uncited_statement_count = 0
    if no_answer:
        status = "no_answer"
    elif invalid_numbers:
        status = "invalid_citations"
    elif not cited_sources or uncited_statement_count:
        status = "uncited"
    else:
        status = "valid"

    location_fields = (
        "start_time", "sheet_name", "cell_range", "slide_number",
        "paragraph_anchor", "page_number", "topic_id", "heading_anchor",
    )
    located_count = sum(
        1 for source in cited_sources
        if any(getattr(source, field_name, None) not in (None, "") for field_name in location_fields)
    )
    versions_by_item: dict[str, set[str]] = {}
    for source in cited_sources:
        item_id = getattr(source, "content_item_id", None)
        version_id = getattr(source, "content_version_id", None)
        if item_id and version_id:
            versions_by_item.setdefault(str(item_id), set()).add(str(version_id))

    return CitationFinalization(
        text=normalized_text,
        sources=cited_sources,
        diagnostics={
            "status": status,
            "candidate_count": len(candidate_sources),
            "citation_marker_count": marker_count,
            "cited_count": len(cited_sources),
            "invalid_citation_numbers": sorted(invalid_numbers),
            "uncited_answer": status == "uncited",
            "uncited_statement_count": uncited_statement_count,
            "located_count": located_count,
            "version_conflict": any(len(versions) > 1 for versions in versions_by_item.values()),
        },
    )


def _render_source(p: RetrievedParent, n: int) -> str:
    """Render one <source> block with a 1-based citation index `n`."""
    company_attr = f' company="{p.company}"' if p.company else ""
    if p.doc_type == "transcript" and p.start_time:
        return (
            f'<source index="{n}" id="{p.parent_id[:8]}" doc="{p.doc_title}" '
            f'category="{p.category}"{company_attr} '
            f'time="{p.start_time}" type="transcript">\n'
            f"{p.text}\n"
            f"</source>"
        )
    # Show the LLM only the leaf of the breadcrumb (e.g. `(5) 钢材耐腐蚀性差`)
    # instead of the full path `第1章 概述 > 1.1 ... > 1.1.1 ... > (5) ...`.
    # Inline citations stay short and readable; the full breadcrumb is
    # exposed in the SourceWorkspace detail view.
    section_leaf = p.section_path.split(" > ")[-1] if p.section_path else ""
    return (
        f'<source index="{n}" id="{p.parent_id[:8]}" doc="{p.doc_title}" '
        f'category="{p.category}"{company_attr} '
        f'section="{section_leaf}" type="pdf">\n'
        f"{p.text}\n"
        f"</source>"
    )


def _interleave_by_subquery(parents: list[RetrievedParent]) -> list[RetrievedParent]:
    """Round-robin parents across their `subquery_idx` groups, preserving each
    group's internal order. So every comparison side contributes its top hit
    before any side contributes its second — ensuring both sides survive a
    budget cutoff downstream. Groups are visited in first-appearance order.
    """
    groups: dict[object, list[RetrievedParent]] = {}
    order: list[object] = []
    for p in parents:
        key = p.subquery_idx
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)
    result: list[RetrievedParent] = []
    i = 0
    while any(i < len(groups[k]) for k in order):
        for k in order:
            if i < len(groups[k]):
                result.append(groups[k][i])
        i += 1
    return result


def _build_context(
    parents: list[RetrievedParent],
    budget: int,
) -> tuple[str, list[RetrievedParent]]:
    """Pack as many parents as fit under `budget` chars. Always keep at least one.

    Decomposed path (any parent carries `subquery_idx`): interleave sources
    round-robin across sub-queries first, so each comparison side gets a turn
    before the budget fills. Single-query path: original sequential order,
    byte-for-byte unchanged.
    """
    if any(p.subquery_idx is not None for p in parents):
        parents = _interleave_by_subquery(parents)
    blocks: list[str] = []
    used: list[RetrievedParent] = []
    total = 0
    for p in parents:
        # 1-based index the LLM cites as `[N]`. It is assigned in packing order,
        # which is the SAME order (and subset) the UI receives as `sources[]`,
        # so `[N]` in the answer resolves to `sources[N-1]` on the frontend.
        n = len(used) + 1
        block = _render_source(p, n)
        if total + len(block) > budget and used:
            break
        blocks.append(block)
        used.append(p)
        total += len(block)
    return "\n\n".join(blocks), used


def _client() -> OpenAI:
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY is not set. Add it to .env.")
    return OpenAI(
        api_key=ZHIPU_API_KEY,
        base_url=ZHIPU_BASE_URL,
        timeout=httpx.Timeout(120.0, connect=30.0, read=120.0, write=30.0),
    )


def _prepare_generation(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None,
    budget: int | None,
    policy: AnswerPolicy | None = None,
) -> GenerationPrep:
    """Build the messages list and decide which parents fit under `budget`.

    Pure / synchronous: makes no API call. Both `generate()` and
    `stream_generate()` build on this so they agree on what gets sent.
    """
    effective_policy = policy or load_answer_policy()
    effective_budget = effective_policy.answer_context_chars if budget is None else max(budget, 0)
    context, used = _build_context(parents, effective_budget)
    user_msg = render_prompt("answer_user", context=context, query=query)

    messages: list[dict] = [
        {"role": "system", "content": load_prompt("answer_system")}
    ]
    if history:
        for m in history:
            role = m.get("role")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    return GenerationPrep(
        used_sources=used,
        messages=messages,
        model=LLM_MODEL,
        context_chars=len(context),
        budget=effective_budget,
        policy=effective_policy,
    )


def _extract_usage(resp) -> dict:
    """Best-effort pull of token usage off an OpenAI-compatible response/chunk."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out: dict = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if v is None and isinstance(u, dict):
            v = u.get(k)
        if v is not None:
            out[k] = int(v)
    return out


def rewrite_query(
    history: list[dict],
    question: str,
    max_turns: int = 6,
    usage_out: dict | None = None,
) -> str:
    """Rewrite a follow-up question into a standalone one using recent chat history.

    `history` is a list of {"role": "user"|"assistant", "content": str} dicts
    NOT including the current `question`. Returns `question` unchanged when
    history is empty or the rewrite call fails.
    """
    if not history:
        return question

    recent = history[-max_turns:]
    convo_lines = []
    for m in recent:
        speaker = "用户" if m.get("role") == "user" else "助手"
        content = (m.get("content") or "").strip()
        if content:
            convo_lines.append(f"{speaker}：{content}")
    if not convo_lines:
        return question

    user_msg = render_prompt(
        "rewrite_user",
        history="\n".join(convo_lines),
        question=question,
    )
    started = perf_counter()
    try:
        client = _client()
        resp = client.chat.completions.create(
            # Rewrite uses LLM_REWRITE_MODEL, not LLM_MODEL — the rewrite is
            # latency-critical (one cheap call before retrieval) while answer
            # generation cares more about quality. Defaults to LLM_MODEL when
            # LLM_REWRITE_MODEL is unset, so single-model setups still work.
            model=LLM_REWRITE_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": load_prompt("rewrite_system")},
                {"role": "user", "content": user_msg},
            ],
            extra_body={"thinking": {"type": "disabled"}},
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        if usage_out is not None:
            usage_out.update(_extract_usage(resp))
            usage_out["model"] = LLM_REWRITE_MODEL
        record_usage("zhipu", "rewrite", usage=_extract_usage(resp), latency_ms=int((perf_counter() - started) * 1000))
    except Exception:
        record_usage("zhipu", "rewrite", success=False, latency_ms=int((perf_counter() - started) * 1000))
        return question

    rewritten = rewritten.strip().strip('"').strip("'").strip("“”‘’").strip()
    return rewritten or question


def generate(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None = None,
    budget: int | None = None,
    policy: AnswerPolicy | None = None,
) -> Answer:
    """Run the answering LLM call (non-streaming).

    Channel separation:
      - `history` (conversation channel) is interleaved as native chat turns.
        Callers must strip <sources> from prior assistant messages before
        passing them here.
      - `parents` (knowledge channel) are packed into the *current* user
        message only, never into history.
      - `query` is the user's original question, not the retrieval rewrite.
    """
    prep = _prepare_generation(query, parents, history, budget, policy)
    client = _client()
    started = perf_counter()
    try:
        resp = client.chat.completions.create(
            model=prep.model, temperature=prep.policy.answer_temperature,
            max_tokens=prep.policy.answer_max_output_tokens, messages=prep.messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception:
        record_usage("zhipu", "answer", success=False, latency_ms=int((perf_counter() - started) * 1000))
        raise
    record_usage("zhipu", "answer", usage=_extract_usage(resp), latency_ms=int((perf_counter() - started) * 1000))
    finalized = finalize_answer_sources_with_diagnostics(
        resp.choices[0].message.content or "",
        prep.used_sources,
    )
    return Answer(
        text=finalized.text,
        sources=finalized.sources,
        messages=prep.messages,
        model=prep.model,
        context_chars=prep.context_chars,
        budget_used=prep.context_chars,
        budget=prep.budget,
        usage=_extract_usage(resp),
        citation_diagnostics=finalized.diagnostics,
    )


def stream_generate(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None = None,
    budget: int | None = None,
    policy: AnswerPolicy | None = None,
) -> tuple[GenerationPrep, Iterator[str]]:
    """Streaming variant of `generate()`.

    Returns `(prep, generator)`:
      - `prep` is resolved synchronously: which parents got packed, what
        messages will be sent, model/context telemetry. Render this up-front
        in the UI.
      - `generator` yields text deltas as they arrive from the LLM. The full
        answer text is the concatenation of all yielded chunks.

    The same channel-separation rules as `generate()` apply.
    """
    prep = _prepare_generation(query, parents, history, budget, policy)
    client = _client()
    started = perf_counter()
    try:
        resp = client.chat.completions.create(
        model=prep.model,
        temperature=prep.policy.answer_temperature,
        max_tokens=prep.policy.answer_max_output_tokens,
        messages=prep.messages,
        stream=True,
        # Ask the provider to send a final chunk carrying token usage.
        stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception:
        record_usage("zhipu", "answer", success=False, latency_ms=int((perf_counter() - started) * 1000))
        raise

    def _iter() -> Iterator[str]:
        try:
            for chunk in resp:
                usage = _extract_usage(chunk)
                if usage:
                    prep.usage.update(usage)
                    prep.usage["model"] = prep.model
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception:
            record_usage("zhipu", "answer", success=False, latency_ms=int((perf_counter() - started) * 1000))
            raise
        record_usage("zhipu", "answer", usage=prep.usage, latency_ms=int((perf_counter() - started) * 1000))

    return prep, _iter()
