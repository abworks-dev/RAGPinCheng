from unittest.mock import patch

from src.session import ChatSession, Message


def _session_with_turn() -> ChatSession:
    session = ChatSession()
    session.state.messages = [
        Message(role="user", content="钢结构防腐处理有哪些要求？"),
        Message(role="assistant", content="应根据环境类别选择防腐体系。"),
    ]
    session.state.turn_index = 1
    session.state.last_search_query = "钢结构防腐处理有哪些要求？"
    return session


def test_first_turn_is_standalone_without_rewrite():
    resolution, _ = ChatSession()._resolve_search_query("钢结构防腐有哪些要求？")

    assert resolution.original_query == "钢结构防腐有哪些要求？"
    assert resolution.standalone_query == resolution.original_query
    assert resolution.kind == "standalone"
    assert resolution.confidence == 1.0


def test_successful_rewrite_is_structured_as_follow_up():
    session = _session_with_turn()
    with patch("src.session.rewrite_query", return_value="钢结构防腐涂层损坏后如何处理？"):
        resolution, _ = session._resolve_search_query("损坏了怎么办？")

    assert resolution.kind == "follow_up"
    assert resolution.standalone_query == "钢结构防腐涂层损坏后如何处理？"
    assert resolution.confidence == 0.8
    assert resolution.fallback_reason == ""


def test_short_follow_up_uses_previous_user_question_when_rewrite_is_unchanged():
    session = _session_with_turn()
    with patch("src.session.rewrite_query", return_value="防腐涂层厚度呢？"):
        resolution, _ = session._resolve_search_query("防腐涂层厚度呢？")

    assert resolution.kind == "follow_up"
    assert resolution.standalone_query == "钢结构防腐处理有哪些要求？；追问：防腐涂层厚度呢？"
    assert resolution.confidence == 0.35
    assert resolution.referenced_turns == [1]
    assert resolution.fallback_reason == "short_follow_up_fallback"


def test_ambiguous_follow_up_requests_clarification():
    session = _session_with_turn()
    with patch("src.session.rewrite_query", return_value="那我来问你看下"):
        resolution, _ = session._resolve_search_query("那我来问你看下")

    assert resolution.kind == "clarification_required"
    assert resolution.confidence == 0.0
    assert resolution.fallback_reason == "ambiguous_follow_up"


def test_ambiguous_follow_up_stream_skips_retrieval():
    session = _session_with_turn()
    with patch("src.session.rewrite_query", return_value="那我来问你看下"):
        prep, stream = session.ask_stream("那我来问你看下")

    assert prep.guard_reason == "clarification_required"
    assert prep.fresh_sources == []
    assert "具体对象或条件" in "".join(stream)


def test_repeated_question_reuses_last_search_query():
    session = _session_with_turn()

    resolution, _ = session._resolve_search_query("钢结构防腐处理有哪些要求？")

    assert resolution.standalone_query == session.state.last_search_query
    assert resolution.fallback_reason == "repeat_query"
