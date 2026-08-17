import importlib
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import conversation_runtime, routes_admin
from api.auth import require_admin, require_csrf_admin
from api.conversation_runtime import (
    TurnPersistencePlan,
    create_conversation,
    hydrate_session,
    list_messages,
    persist_turn,
    prepare_regeneration,
    prepare_user_edit,
)
from api.db import connect, init_db
from api.db import get_db
from src.answer_policy import (
    AnswerPolicy,
    list_answer_policy_audit,
    load_answer_policy,
    save_answer_policy,
)
from src.retrieve import RetrievedParent
from src.session import ChatSession


def _source() -> RetrievedParent:
    return RetrievedParent(
        parent_id="p1",
        doc_title="测试资料",
        category="行业规范",
        section_path="第一章",
        source_path="test.pdf",
        text="测试正文",
        score=0.9,
        matched_children=[],
        rrf_score=0.2,
    )


def _policy(version: str, *, gate: bool = False) -> AnswerPolicy:
    return AnswerPolicy(
        answer_temperature=0.35,
        answer_max_output_tokens=900,
        answer_context_chars=5000,
        relevance_gate_enabled=gate,
        relevance_min_score=0.6,
        relevance_min_rrf=0.01,
        relevance_min_margin=0.05,
        policy_version=version,
        updated_at=1,
        updated_by=1,
    )


def _create_admin(conn) -> None:
    conn.execute(
        "INSERT INTO users(id,employee_id,real_name,password_hash,role,is_active,created_at) "
        "VALUES (1,'admin','系统管理员','hash','admin',1,1)"
    )
    conn.commit()


def test_policy_save_load_and_audit(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = connect(path)
    _create_admin(conn)

    saved = save_answer_policy(
        conn,
        _policy("input", gate=True),
        updated_by=1,
        change_reason="完成阈值校准",
    )
    conn.commit()

    loaded = load_answer_policy(conn)
    audit = list_answer_policy_audit(conn)
    assert loaded == saved
    assert saved.policy_version.startswith("admin-")
    assert saved.relevance_gate_enabled is True
    assert audit[0]["change_reason"] == "完成阈值校准"
    assert json.loads(audit[0]["new_policy_json"])["policy_version"] == saved.policy_version
    conn.close()


def test_policy_validation_rejects_out_of_range_without_writing(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = connect(path)
    _create_admin(conn)
    invalid = AnswerPolicy(answer_max_output_tokens=128)
    with pytest.raises(ValueError, match="out of range"):
        save_answer_policy(conn, invalid, updated_by=1)
    assert conn.execute("SELECT COUNT(*) FROM answer_policy_audit").fetchone()[0] == 0
    conn.close()


def test_stream_turn_uses_one_policy_snapshot_even_for_guard_fallback(monkeypatch):
    calls = 0
    policy = _policy("admin-stream")

    def load_once():
        nonlocal calls
        calls += 1
        return policy

    monkeypatch.setattr("src.session.load_answer_policy", load_once)
    session = ChatSession()
    prep, stream = session.ask_stream("11")
    assert "补充" in "".join(stream)
    assert calls == 1
    assert prep.policy_snapshot["policy_version"] == "admin-stream"
    assert session.last_turn_result is not None
    assert session.last_turn_result.policy_snapshot == prep.policy_snapshot
    assert session.state.messages[-1].policy_snapshot == prep.policy_snapshot


def test_generation_passes_policy_to_sync_and_stream_provider(monkeypatch):
    generation = importlib.import_module("src.generate")
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("stream"):
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="流式回答"))],
                        usage=None,
                    )
                ]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="同步回答"))],
                usage=None,
            )

    monkeypatch.setattr(
        generation,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )
    policy = _policy("admin-generate")
    answer = generation.generate("测试问题", [_source()], policy=policy)
    prep, stream = generation.stream_generate("测试问题", [_source()], policy=policy)

    assert answer.text == "同步回答"
    assert "".join(stream) == "流式回答"
    assert prep.policy is policy
    assert [(call["temperature"], call["max_tokens"]) for call in calls] == [
        (0.35, 900),
        (0.35, 900),
    ]


def test_policy_snapshot_persists_for_new_regenerated_and_edited_answers(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(conversation_runtime, "connect", lambda: connect(path))
    conn = connect(path)
    _create_admin(conn)
    conversation = create_conversation(conn, 1)
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation["id"],)).fetchone()
    session = hydrate_session(conn, row)
    conn.close()

    session.state.append_turn("原问题", "原回答", [], _policy("policy-1").public_dict())
    session.state.last_search_query = "原问题"
    plan = TurnPersistencePlan(conversation["id"], "原问题", True)
    persist_turn(plan, session)
    assistant_id = plan.persisted_assistant_message_id
    assert assistant_id is not None

    conn = connect(path)
    regen, query, categories = prepare_regeneration(conn, conversation["id"], assistant_id)
    conn.close()
    regen.state.append_turn(query, "重新回答", [], _policy("policy-2").public_dict())
    regen.state.last_search_query = query
    persist_turn(
        TurnPersistencePlan(
            conversation["id"], query, False, categories,
            regenerate_assistant_message_id=assistant_id,
        ),
        regen,
    )

    conn = connect(path)
    user_id = next(row["id"] for row in list_messages(conn, conversation["id"]) if row["role"] == "user")
    edited, categories, paired_id = prepare_user_edit(conn, conversation["id"], user_id)
    conn.close()
    edited.state.append_turn("编辑问题", "编辑回答", [], _policy("policy-3").public_dict())
    edited.state.last_search_query = "编辑问题"
    persist_turn(
        TurnPersistencePlan(
            conversation["id"], "编辑问题", False, categories,
            edit_user_message_id=user_id,
            edit_assistant_message_id=paired_id,
        ),
        edited,
    )

    conn = connect(path)
    rows = conn.execute(
        "SELECT policy_version,policy_json FROM message_answer_versions "
        "WHERE assistant_message_id=? ORDER BY version_index",
        (assistant_id,),
    ).fetchall()
    conn.close()
    assert [row["policy_version"] for row in rows] == ["policy-1", "policy-2", "policy-3"]
    assert [json.loads(row["policy_json"])["answer_max_output_tokens"] for row in rows] == [900, 900, 900]


def test_answer_policy_routes_enforce_admin_and_csrf_dependencies():
    def dependencies(path: str, method: str):
        route = next(
            item for item in routes_admin.router.routes
            if isinstance(item, APIRoute) and item.path == path and method in item.methods
        )
        return {dependency.call for dependency in route.dependant.dependencies}

    assert require_admin in dependencies("/admin/answer-policy", "GET")
    assert require_admin in dependencies("/admin/answer-policy/audit", "GET")
    assert require_csrf_admin in dependencies("/admin/answer-policy", "PATCH")
    assert require_csrf_admin in dependencies("/admin/answer-policy/reset", "POST")


def test_answer_policy_http_auth_and_csrf_boundary(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = connect(path)
    now = int(time.time())
    conn.executemany(
        "INSERT INTO users(employee_id,real_name,password_hash,role,is_active,created_at) VALUES (?,?,?,?,1,?)",
        [("plain", "普通用户", "hash", "user", now), ("admin", "系统管理员", "hash", "admin", now)],
    )
    users = {row["employee_id"]: row["id"] for row in conn.execute("SELECT id,employee_id FROM users")}
    sessions = {}
    for employee_id, user_id in users.items():
        sid, csrf = f"sid-{employee_id}", f"csrf-{employee_id}"
        conn.execute(
            "INSERT INTO auth_sessions(id,user_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?)",
            (sid, user_id, csrf, now, now + 3600),
        )
        sessions[employee_id] = (sid, csrf)
    conn.commit()
    conn.close()

    def override_db():
        request_conn = connect(path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app = FastAPI()
    app.include_router(routes_admin.router, prefix="/api")
    app.dependency_overrides[get_db] = override_db

    def auth(employee_id: str, csrf: bool = False):
        sid, token = sessions[employee_id]
        return {
            "cookies": {"pc_sid": sid},
            "headers": {"X-CSRF-Token": token} if csrf else {},
        }

    with TestClient(app) as client:
        assert client.get("/api/admin/answer-policy").status_code == 401
        assert client.get("/api/admin/answer-policy", **auth("plain")).status_code == 403
        assert client.get("/api/admin/answer-policy", **auth("admin")).status_code == 200
        body = {
            "answer_temperature": 0.2,
            "answer_max_output_tokens": 1200,
            "answer_context_chars": 6000,
            "relevance_gate_enabled": False,
            "relevance_min_score": 0,
            "relevance_min_rrf": 0,
            "relevance_min_margin": 0,
        }
        assert client.patch("/api/admin/answer-policy", json=body, **auth("admin")).status_code == 403
        assert client.patch("/api/admin/answer-policy", json=body, **auth("admin", csrf=True)).status_code == 200
