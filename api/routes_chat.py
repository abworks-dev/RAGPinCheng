"""Conversation + chat endpoints.

Replaces the old `/sessions/*` surface from routes.py. Every endpoint here
requires an authenticated user; cross-user access is blocked at the row
ownership check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import sqlite3
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from .auth import CurrentUser, require_csrf, require_user
from .conversation_runtime import (
    TurnPersistencePlan,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_lock,
    hydrate_session,
    list_answer_versions,
    list_conversations,
    list_messages,
    list_user_versions,
    prepare_regeneration,
    prepare_user_edit,
    wrap_stream_with_persistence,
)
from .db import get_db
from .rate_limit import chat_limiter
from .schemas import (
    ChatRequest,
    ConversationListResponse,
    ConversationStateDTO,
    ConversationSummaryDTO,
    CreateConversationResponse,
    MessageDTO,
    SourceDTO,
    source_to_dto,
)
from .sse import event

turn_logger = logging.getLogger("api.chat.turn")

logger = logging.getLogger("api.routes_chat")

router = APIRouter(tags=["chat"])


# ── conversation CRUD ──────────────────────────────────────────────────────


@router.get("/conversations", response_model=ConversationListResponse)
def list_my_conversations(
    user: CurrentUser = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationListResponse:
    rows = list_conversations(conn, user.id)
    return ConversationListResponse(
        conversations=[
            ConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


@router.post("/conversations", response_model=CreateConversationResponse)
def create_my_conversation(
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> CreateConversationResponse:
    row = create_conversation(conn, user.id)
    return CreateConversationResponse(**row)


@router.get("/conversations/{conversation_id}", response_model=ConversationStateDTO)
def get_my_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationStateDTO:
    row = get_conversation(conn, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    msgs_rows = list_messages(conn, conversation_id)
    messages = [
        MessageDTO(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            sources_for_ui=(
                [source_to_dto(s) for s in json.loads(m["sources_json"])]
                if m["sources_json"]
                else None
            ),
            created_at=m["created_at"],
            answer_versions=(
                [
                    {
                        "id": v["id"],
                        "version_index": v["version_index"],
                        "content": v["content"],
                        "sources_for_ui": (
                            [source_to_dto(s) for s in json.loads(v["sources_json"])]
                            if v["sources_json"]
                            else None
                        ),
                        "created_at": v["created_at"],
                        "is_active": bool(v["is_active"]),
                        "user_version_id": v["user_version_id"],
                    }
                    for v in list_answer_versions(conn, m["id"])
                ]
                if m["role"] == "assistant"
                else None
            ),
            user_versions=(
                [
                    {
                        "id": v["id"],
                        "version_index": v["version_index"],
                        "content": v["content"],
                        "created_at": v["created_at"],
                        "is_active": bool(v["is_active"]),
                    }
                    for v in list_user_versions(conn, m["id"])
                ]
                if m["role"] == "user"
                else None
            ),
        )
        for m in msgs_rows
    ]
    return ConversationStateDTO(
        id=row["id"],
        title=row["title"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        turn_index=row["turn_index"],
        messages=messages,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_my_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    row = get_conversation(conn, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    delete_conversation(conn, conversation_id)


# ── streaming chat ─────────────────────────────────────────────────────────


@router.post("/conversations/{conversation_id}/chat")
async def chat(
    conversation_id: str,
    body: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(require_csrf),
) -> EventSourceResponse:
    if (
        body.regenerate_assistant_message_id is not None
        and body.edit_user_message_id is not None
    ):
        raise HTTPException(status_code=422, detail="edit and regenerate are mutually exclusive")
    if body.regenerate_assistant_message_id is None and not (body.query or "").strip():
        raise HTTPException(status_code=422, detail="query is required")
    # Per-user rate limit. Reject early — before doing DB work or hydration —
    # so a hot-spinning client can't queue up expensive turns we'll just
    # discard. 429 with Retry-After is the standard contract.
    allowed, retry_after = await chat_limiter.try_acquire(user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded; slow down",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )

    # Authorization + existence check up front, with its own short-lived
    # connection so we don't hold one through the SSE stream.
    from .db import connect as db_connect
    pre_conn = db_connect()
    try:
        row = pre_conn.execute(
            "SELECT user_id, turn_index FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        latest_assistant_id = (
            pre_conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND role = 'assistant' "
                "ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if body.regenerate_assistant_message_id is not None
            else None
        )
        latest_user_id = (
            pre_conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' "
                "ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if body.edit_user_message_id is not None
            else None
        )
    finally:
        pre_conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    if body.regenerate_assistant_message_id is not None and (
        latest_assistant_id is None
        or latest_assistant_id["id"] != body.regenerate_assistant_message_id
    ):
        raise HTTPException(status_code=409, detail="only latest answer can be regenerated")
    if body.edit_user_message_id is not None and (
        latest_user_id is None or latest_user_id["id"] != body.edit_user_message_id
    ):
        raise HTTPException(status_code=409, detail="only latest question can be edited")
    is_first_turn = int(row["turn_index"]) == 0

    lock = get_lock(conversation_id)

    async def event_generator() -> AsyncIterator[ServerSentEvent]:
        async with lock:
            try:
                # Hydrate ChatSession from DB on its own short-lived conn.
                # Doing it inside the lock means no two concurrent turns
                # see the same stale snapshot.
                hydrate_conn = db_connect()
                try:
                    edit_assistant_message_id = None
                    if body.edit_user_message_id is not None:
                        try:
                            session, categories, edit_assistant_message_id = prepare_user_edit(
                                hydrate_conn,
                                conversation_id,
                                body.edit_user_message_id,
                            )
                            query = (body.query or "").strip()
                        except ValueError as exc:
                            raise HTTPException(status_code=409, detail=str(exc)) from exc
                    elif body.regenerate_assistant_message_id is not None:
                        try:
                            session, query, categories = prepare_regeneration(
                                hydrate_conn,
                                conversation_id,
                                body.regenerate_assistant_message_id,
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=409, detail=str(exc)) from exc
                    else:
                        conv_row = hydrate_conn.execute(
                            "SELECT * FROM conversations WHERE id = ?",
                            (conversation_id,),
                        ).fetchone()
                        session = hydrate_session(hydrate_conn, conv_row)
                        query = body.query or ""
                        categories = body.categories
                finally:
                    hydrate_conn.close()

                prep, raw_stream = await asyncio.to_thread(
                    session.ask_stream,
                    query,
                    categories,
                )

                plan = TurnPersistencePlan(
                    conversation_id=conversation_id,
                    user_text=query,
                    is_first_turn=is_first_turn,
                    categories=categories,
                    regenerate_assistant_message_id=body.regenerate_assistant_message_id,
                    edit_user_message_id=body.edit_user_message_id,
                    edit_assistant_message_id=edit_assistant_message_id,
                )
                persistent_stream = wrap_stream_with_persistence(
                    raw_stream, session, plan
                )

                yield event("prep", {
                    "search_query": prep.search_query,
                    "rewrite_applied": prep.rewrite_applied,
                    "history_chars": prep.history_chars,
                    "budget": prep.budget,
                    "fresh_count": len(prep.fresh_sources),
                    "final_count": len(prep.final_sources),
                    "used_sources": [
                        source_to_dto(s).model_dump()
                        for s in session._sources_for_ui(prep.used_sources)
                    ],
                    "no_source_fallback": prep.no_source_fallback,
                    "relevance": prep.relevance,
                    "policy_version": prep.policy_snapshot.get("policy_version"),
                    "answer_max_output_tokens": prep.policy_snapshot.get("answer_max_output_tokens"),
                    "answer_context_chars": prep.policy_snapshot.get("answer_context_chars"),
                    "relevance_gate_enabled": prep.policy_snapshot.get("relevance_gate_enabled"),
                })

                _STOP = object()
                _generator_lock = threading.Lock()

                def _next_chunk():
                    try:
                        with _generator_lock:
                            return next(persistent_stream)
                    except StopIteration:
                        return _STOP

                # Send a heartbeat ping every 15s during long silences.
                # SSE comments (lines starting with :) are ignored by parsers
                # but keep the TCP connection alive through reverse proxies.
                ping_event = ServerSentEvent(data="", comment="ping")
                last_activity = asyncio.get_event_loop().time()

                while True:
                    if await request.is_disconnected():
                        try:
                            with _generator_lock:
                                persistent_stream.close()
                        except Exception:
                            pass
                        break

                    now = asyncio.get_event_loop().time()
                    time_since_last = now - last_activity
                    if time_since_last > 15:
                        yield ping_event
                        last_activity = now
                        # Wait briefly before checking again — the token may
                        # arrive right after we send the ping.
                        await asyncio.sleep(0.05)
                        continue

                    # Wait for the next token with a short timeout so we can
                    # periodically check for disconnect and send heartbeats.
                    try:
                        chunk = await asyncio.wait_for(
                            asyncio.to_thread(_next_chunk),
                            timeout=15 - time_since_last,
                        )
                    except asyncio.TimeoutError:
                        # No token arrived before our ping window — send a
                        # heartbeat at the top of the next loop.
                        continue

                    last_activity = asyncio.get_event_loop().time()
                    if chunk is _STOP:
                        break
                    if chunk:
                        yield event("token", {"text": chunk})

                result = session.last_turn_result
                if result is not None:
                    yield event("done", {
                        "answer_text": result.answer_text,
                        "assistant_message_id": plan.persisted_assistant_message_id,
                        "timings": result.timings,
                        "history_chars": result.history_chars,
                        "budget": result.budget,
                        "finish_reason": "retrieval_low_confidence" if result.relevance.get("action") == "low_confidence" else "stop",
                        "policy_version": result.policy_snapshot.get("policy_version"),
                        "answer_max_output_tokens": result.policy_snapshot.get("answer_max_output_tokens"),
                        "answer_context_chars": result.policy_snapshot.get("answer_context_chars"),
                        "relevance_gate_enabled": result.policy_snapshot.get("relevance_gate_enabled"),
                        "sources": [
                            source_to_dto(s).model_dump()
                            for s in session._sources_for_ui(result.sources)
                        ],
                    })
                    _emit_turn_telemetry(
                        conversation_id=conversation_id,
                        user_id=user.id,
                        result=result,
                        outcome=("no_source" if not result.sources else "success"),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat stream failed")
                _emit_turn_telemetry(
                    conversation_id=conversation_id,
                    user_id=user.id,
                    result=session.last_turn_result if 'session' in locals() else None,
                    outcome="error",
                    error=str(exc),
                )
                yield event("error", {"message": str(exc)})

    return EventSourceResponse(event_generator())


def _emit_turn_telemetry(
    *,
    conversation_id: str,
    user_id: int,
    result,
    outcome: str,
    error: str | None = None,
) -> None:
    """Single sink for per-turn observability: structured log + Prometheus.

    Keeps the SSE handler clean and ensures both signals stay in sync.
    Never raises — telemetry must not break the user-facing path.
    """
    try:
        timings = dict(result.timings) if result and result.timings else {}
        usage_by_call = dict(result.usage_by_call) if result and result.usage_by_call else {}
        final_count = len(result.final_sources) if result and result.final_sources else 0
        budget = int(result.budget) if result else 0

        payload = {
            "event": "chat_turn",
            "outcome": outcome,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "search_query": result.search_query if result else None,
            "rewrite_applied": bool(result.rewrite_applied) if result else None,
            "fresh_sources": len(result.fresh_sources) if result and result.fresh_sources else 0,
            "final_sources": final_count,
            "history_chars": int(result.history_chars) if result else 0,
            "budget": budget,
            "timings": {k: round(float(v), 4) for k, v in timings.items()},
            "usage": dict(result.usage) if result and result.usage else {},
            "usage_by_call": usage_by_call,
            "relevance": dict(result.relevance) if result and result.relevance else {},
        }
        if error:
            payload["error"] = error
        turn_logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("telemetry emit failed (non-fatal)")
