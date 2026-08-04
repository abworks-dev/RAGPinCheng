import sys
from dataclasses import dataclass, field
from types import ModuleType

import pytest

try:
    import qdrant_client  # noqa: F401
except ModuleNotFoundError:
    retrieve_stub = ModuleType("src.retrieve")

    @dataclass
    class RetrievedParent:
        parent_id: str
        doc_title: str
        category: str
        section_path: str
        source_path: str
        text: str
        score: float
        matched_children: list[str]
        doc_type: str = "pdf"
        start_time: str | None = None
        company: str | None = None
        media_id: str | None = None
        rrf_score: float = 0.0

    retrieve_stub.RetrievedParent = RetrievedParent
    sys.modules["src.retrieve"] = retrieve_stub

    session_stub = ModuleType("src.session")

    @dataclass
    class Message:
        role: str
        content: str
        sources_for_ui: list[dict] | None = None

    @dataclass
    class SessionState:
        messages: list[Message] = field(default_factory=list)
        last_sources: list[RetrievedParent] = field(default_factory=list)
        last_search_query: str = ""
        turn_index: int = 0

        def append_turn(self, user_text, assistant_text, sources_for_ui=None):
            self.messages.extend([
                Message("user", user_text),
                Message("assistant", assistant_text, sources_for_ui),
            ])
            self.turn_index += 1

    class ChatSession:
        def __init__(self):
            self.state = SessionState()

    session_stub.ChatSession = ChatSession
    session_stub.Message = Message
    session_stub.StreamingTurnPrep = object
    sys.modules["src.session"] = session_stub

from api import conversation_runtime
from api.db import connect, init_db
from api.conversation_runtime import (
    TurnPersistencePlan,
    create_conversation,
    list_answer_versions,
    list_messages,
    persist_turn,
    prepare_regeneration,
)
from src.session import ChatSession


def _persist_answer(path, conversation_id: str, query: str, answer: str) -> int:
    session = ChatSession()
    conn = connect(path)
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    session = conversation_runtime.hydrate_session(conn, row)
    conn.close()
    session.state.append_turn(query, answer, sources_for_ui=[])
    session.state.last_search_query = query
    plan = TurnPersistencePlan(
        conversation_id=conversation_id,
        user_text=query,
        is_first_turn=session.state.turn_index == 1,
        categories=["公司标准"],
    )
    persist_turn(plan, session)
    assert plan.persisted_assistant_message_id is not None
    return plan.persisted_assistant_message_id


def _create_user(conn) -> None:
    conn.execute(
        "INSERT INTO users(id, employee_id, real_name, password_hash, role, is_active, created_at) "
        "VALUES (1, 'u1', '测试用户', 'hash', 'user', 1, 1)"
    )
    conn.commit()


def test_regeneration_keeps_old_answer_and_switches_active_version(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(conversation_runtime, "connect", lambda: connect(path))

    conn = connect(path)
    _create_user(conn)
    conversation = create_conversation(conn, 1)
    conn.close()
    _persist_answer(path, conversation["id"], "原问题", "原回答")

    conn = connect(path)
    rows = list_messages(conn, conversation["id"])
    assistant_id = rows[-1]["id"]
    session, query, categories = prepare_regeneration(conn, conversation["id"], assistant_id)
    conn.close()

    assert query == "原问题"
    assert categories == ["公司标准"]
    assert session.state.messages == []
    assert session.state.turn_index == 0

    session.state.append_turn(query, "新回答", sources_for_ui=[])
    session.state.last_search_query = query
    regeneration_plan = TurnPersistencePlan(
        conversation_id=conversation["id"],
        user_text=query,
        is_first_turn=False,
        categories=categories,
        regenerate_assistant_message_id=assistant_id,
    )
    persist_turn(regeneration_plan, session)
    assert regeneration_plan.persisted_assistant_message_id == assistant_id

    conn = connect(path)
    effective = list_messages(conn, conversation["id"])
    versions = list_answer_versions(conn, assistant_id)
    stored_original = conn.execute(
        "SELECT content FROM messages WHERE id = ?", (assistant_id,)
    ).fetchone()[0]
    turn_index = conn.execute(
        "SELECT turn_index FROM conversations WHERE id = ?", (conversation["id"],)
    ).fetchone()[0]
    conn.close()

    assert stored_original == "原回答"
    assert effective[-1]["content"] == "新回答"
    assert [(row["content"], row["is_active"]) for row in versions] == [
        ("原回答", 0),
        ("新回答", 1),
    ]
    assert turn_index == 1


def test_only_latest_assistant_answer_can_be_regenerated(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(conversation_runtime, "connect", lambda: connect(path))
    conn = connect(path)
    _create_user(conn)
    conversation = create_conversation(conn, 1)
    conn.close()
    _persist_answer(path, conversation["id"], "问题一", "回答一")
    _persist_answer(path, conversation["id"], "问题二", "回答二")

    conn = connect(path)
    first_assistant_id = [
        row["id"] for row in list_messages(conn, conversation["id"]) if row["role"] == "assistant"
    ][0]
    with pytest.raises(ValueError, match="only_latest"):
        prepare_regeneration(conn, conversation["id"], first_assistant_id)
    conn.close()


def test_version_rows_cascade_when_conversation_is_deleted(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(conversation_runtime, "connect", lambda: connect(path))
    conn = connect(path)
    _create_user(conn)
    conversation = create_conversation(conn, 1)
    conn.close()
    _persist_answer(path, conversation["id"], "问题", "回答")

    conn = connect(path)
    assert conversation_runtime.delete_conversation(conn, conversation["id"])
    assert conn.execute("SELECT COUNT(*) FROM message_answer_versions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM message_answer_heads").fetchone()[0] == 0
    conn.close()
