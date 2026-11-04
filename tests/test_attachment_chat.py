"""Synthetic attachment cards remain outside the knowledge/model channel."""
import pytest

from src.session import ChatSession, TurnResult
from api import conversation_runtime as runtime
from api.db import connect, init_db


@pytest.mark.parametrize('query,index', [('打开第2个附件', 1), ('第二份文件', 1), ('第十张图片', 9), ('第一二个文件', None)])
def test_file_reference(query, index):
    from src.attachment_search import file_reference
    assert file_reference(query) == index


def card(version="v1"):
    return dict(item_id="item", version_id=version, title="合成附件", original_filename="图片11.png", doc_type="png", file_size=12, category_key="test", category_path="测试", version_number=1, matched_fields=["original_filename"], snippet="合成OCR文本", preview_kind="image", can_preview=True, can_download=False)


@pytest.fixture(autouse=True)
def no_services(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("attachment-only turns must not invoke retrieval or a model")
    monkeypatch.setattr(ChatSession, "_fresh_retrieve", forbidden)
    monkeypatch.setattr("src.session.generate", forbidden)
    monkeypatch.setattr("src.session.stream_generate", forbidden)
    monkeypatch.setattr("src.session.rewrite_query", forbidden)


@pytest.mark.parametrize("streaming", [False, True])
def test_file_only_numeric_name_and_scope(streaming):
    seen = []
    def search(query, *, categories=None, limit=5):
        seen.append((query, categories))
        return [card()]
    session = ChatSession(attachment_search=search)
    if streaming:
        prep, stream = session.ask_stream("找图片11", ["测试"])
        assert prep.file_results == [card()]
        assert "找到相关文件" in "".join(stream)
        result = session.last_turn_result
    else:
        result = session.ask("找图片11", ["测试"])
    assert result.file_results == [card()]
    assert result.sources == [] and result.answer is None
    assert seen == [("找图片11", ["测试"])]
    assert "合成OCR文本" not in str(session.state.history_for_llm())
    assert session.state.messages[-1].file_results == [card()]
    assert session.ask("找图片11", []).file_results == []


@pytest.mark.parametrize('streaming', [False, True])
def test_natural_language_request_uses_literal_attachment_port(streaming):
    query = '帮我寻找一个地下室一层含开闭所的案例图纸'
    seen = []
    def search(value, *, categories=None, limit=5):
        seen.append((value, categories))
        return [card()]
    session = ChatSession(attachment_search=search)
    if streaming:
        prep, stream = session.ask_stream(query, ['synthetic'])
        list(stream)
        result = session.last_turn_result
    else:
        result = session.ask(query, ['synthetic'])
    assert result.file_results == [card()]
    assert result.sources == [] and result.answer is None
    assert seen == [(query, ['synthetic'])]


def test_ordinal_revalidates_previous_position_without_search():
    session = ChatSession(attachment_search=lambda *a, **k: [card(), card("v2")], attachment_validator=lambda results, **k: results)
    session.ask("查找附件")
    assert session.ask("第二个文件").file_results == [card("v2")]
    session.attachment_validator = lambda *a, **k: []
    assert session.ask("第一个文件").file_results == []


def test_original_and_rewritten_union():
    seen = []
    def search(query, **kwargs):
        seen.append(query)
        return [card(query)]
    session = ChatSession(attachment_search=search)
    assert len(session._file_results("原始文件名", "重写词", None)) == 2
    assert seen == ["原始文件名", "重写词"]


@pytest.mark.parametrize("streaming", [False, True])
def test_only_file_hits_bypass_no_source_abstention(monkeypatch, streaming):
    monkeypatch.setattr(ChatSession, "_fresh_retrieve", lambda *a, **k: [])
    session = ChatSession(attachment_search=lambda *a, **k: [card()])
    if streaming:
        prep, stream = session.ask_stream("合成测试对象的要求是什么")
        assert prep.file_results == [card()]
        assert not prep.no_source_fallback
        assert "找到相关文件" in "".join(stream)
        result = session.last_turn_result
    else:
        result = session.ask("合成测试对象的要求是什么")
    assert result.file_results == [card()]
    assert result.answer is None and result.sources == []
    assert session.state.turn_index == 1
    assert len(session.state.messages) == 2


def test_general_question_keeps_rag_result_and_adds_separate_files(monkeypatch):
    session = ChatSession(attachment_search=lambda *a, **k: [card()])
    sentinel = object()
    original = TurnResult("原有知识回答", [sentinel], "重写对象", [], [sentinel], None, 10, 20, True)
    session.last_turn_result = original
    monkeypatch.setattr(session, "_ask_core", lambda *a, **k: original)
    result = session.ask("查找文件中规定的要求是什么")
    assert result is original
    assert result.sources == [sentinel]
    assert result.answer_text == "原有知识回答"
    assert result.file_results == [card()]


def test_raw_digits_remain_blocked():
    session = ChatSession(attachment_search=lambda *a, **k: pytest.fail("numeric query searched files"))
    assert session.ask("11").guard_reason


def test_persist_refresh_regenerate_and_edit_cards(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    monkeypatch.setattr(runtime, "connect", lambda: connect(path))
    conn = connect(path)
    conn.execute("INSERT INTO users(id, employee_id, real_name, password_hash, role, is_active, created_at) VALUES (1, 'test', '合成用户', 'not-secret', 'user', 1, 1)")
    conn.commit()
    conversation = runtime.create_conversation(conn, user_id=1)
    session = ChatSession(attachment_search=lambda *a, **k: [card()])
    session.ask("查找附件")
    plan = runtime.TurnPersistencePlan(conversation["id"], "查找附件", True)
    runtime.persist_turn(plan, session)
    rows = runtime.list_messages(conn, conversation["id"])
    assert runtime._file_results_from_json(rows[-1]["file_results_json"]) == [card()]
    restored = runtime.hydrate_session(conn, runtime.get_conversation(conn, conversation["id"]))
    assert restored.state.messages[-1].file_results == [card()]
    for edit in (False, True):
        if edit:
            restored, categories, aid = runtime.prepare_user_edit(conn, conversation["id"], rows[0]["id"])
        else:
            restored, query, categories = runtime.prepare_regeneration(conn, conversation["id"], plan.persisted_assistant_message_id)
        restored.attachment_search = lambda *a, **k: [card("edited" if edit else "regenerated")]
        restored.ask("查找附件")
        new_plan = runtime.TurnPersistencePlan(conversation["id"], "查找附件", False, edit_user_message_id=rows[0]["id"] if edit else None, edit_assistant_message_id=aid if edit else None, regenerate_assistant_message_id=plan.persisted_assistant_message_id if not edit else None)
        runtime.persist_turn(new_plan, restored)
    versions = runtime.list_answer_versions(conn, plan.persisted_assistant_message_id)
    assert [runtime._file_results_from_json(v["file_results_json"])[0]["version_id"] for v in versions] == ["v1", "regenerated", "edited"]
    conn.close()
