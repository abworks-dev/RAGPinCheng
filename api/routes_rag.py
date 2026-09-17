"""Admin RAG observability endpoints (abstention / refusal log)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from .auth import CurrentUser, require_admin
from .db import get_db
from .rag_abstention import aggregate

router = APIRouter(prefix="/admin/rag", tags=["admin-rag"])


class AbstentionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_query: str
    reason: str
    cnt: int
    last_seen: int
    best_score: float | None = None


@router.get("/abstentions", response_model=list[AbstentionRow])
def list_abstentions(
    _admin: CurrentUser = Depends(require_admin),
    since: int | None = Query(default=None, description="epoch seconds, inclusive"),
    limit: int = Query(default=100, ge=1, le=500),
    conn=Depends(get_db),
):
    return [AbstentionRow(**row) for row in aggregate(conn, since=since, limit=limit)]