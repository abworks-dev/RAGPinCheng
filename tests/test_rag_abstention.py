"""Tests for the RAG abstention (refusal) log and admin API."""
from __future__ import annotations

import sqlite3
import time

from types import SimpleNamespace

from api import rag_abstention
from api.rag_abstention import (
    ABSTAIN_GREETING,
    ABSTAIN_LOW_CONFIDENCE,
    ABSTAIN_NOT_FOUND,
    ABSTAIN_NO_SOURCES,
    abstention_reason,
    aggregate,
    record,
)


def _make_db(tmp_path):
    from api import db as app_db
    from api import db_migrations

    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _turn(**kwargs) -> SimpleNamespace:
    defaults = {
        "guard_reason": "",
        "relevance": {"action": "allow"},
        "answer_text": "正常回答[1]。",
        "final_sources": [SimpleNamespace(doc_title="视频A", score=0.9)],
        "fresh_sources": [],
        "sources": [SimpleNamespace(doc_title="视频A", score=0.9)],
        "search_query": "某查询",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_reason_mapping():
    assert abstention_reason(_turn(guard_reason="greeting")) == ABSTAIN_GREETING
    assert abstention_reason(_turn(relevance={"action": "low_confidence"}, final_sources=[])) == ABSTAIN_LOW_CONFIDENCE
    assert abstention_reason(_turn(answer_text="未找到相关内容。建议补充…", final_sources=[SimpleNamespace(doc_title="x", score=0.1)])) == ABSTAIN_NOT_FOUND
    assert abstention_reason(_turn(final_sources=[], sources=[])) == ABSTAIN_NO_SOURCES
    assert abstention_reason(_turn()) is None


def test_record_and_aggregate(tmp_path):
    conn = _make_db(tmp_path)
    now = int(time.time())
    record(conn, conversation_id="c1", user_query="什么是未知A", search_query="未知A", reason=ABSTAIN_NOT_FOUND, top_source_title="doc1", top_score=0.3, assistant_text="未找到相关内容。", now=now)
    record(conn, conversation_id="c1", user_query="什么是未知A", search_query="未知A", reason=ABSTAIN_NOT_FOUND, top_source_title="doc2", top_score=0.2, assistant_text="未找到相关内容。", now=now + 1)
    record(conn, conversation_id="c2", user_query="有哪些教学视频", search_query="有哪些教学视频", reason=ABSTAIN_GREETING, now=now + 2)
    conn.commit()

    agg = aggregate(conn, since=now - 1)
    by_q = {r["user_query"]: r for r in agg}
    assert by_q["什么是未知A"]["cnt"] >= 2
    assert by_q["什么是未知A"]["reason"] == ABSTAIN_NOT_FOUND
    assert by_q["有哪些教学视频"]["reason"] == ABSTAIN_GREETING
    conn.close()


def test_admin_route_enforces_auth(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import routes_rag

    import time as _t
    from api import db as app_db
    from api.db import get_db
    from api.db import connect as db_connect

    path = tmp_path / "app.sqlite"
    app_db.init_db(path, backup_dir=tmp_path / "backups")
    conn = db_connect(path)
    now = int(_t.time())
    conn.executemany(
        """INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at)
           VALUES (?,?,?,?,?,1,?)""",
        [
            (1, "admin", "管理员", "hash", "admin", now),
            (2, "member", "成员", "hash", "user", now),
        ],
    )
    conn.executemany(
        "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
        [
            ("admin-session", 1, "admin-csrf", now, now + 3600),
            ("member-session", 2, "member-csrf", now, now + 3600),
        ],
    )
    conn.commit()
    conn.close()

    app = FastAPI()
    app.include_router(routes_rag.router, prefix="/api")

    def db_override():
        request_conn = db_connect(path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)

    assert client.get("/api/admin/rag/abstentions").status_code == 401
    resp = client.get(
        "/api/admin/rag/abstentions",
        cookies={"pc_sid": "member-session"},
    )
    assert resp.status_code == 403
    client.close()