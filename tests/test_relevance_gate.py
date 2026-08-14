from dataclasses import replace

from src.relevance_gate import evaluate_relevance
from src.retrieve import RetrievedParent
from src.session import ChatSession


def source(score: float, rrf: float = 0.1) -> RetrievedParent:
    return RetrievedParent(
        parent_id=f"p-{score}", doc_title="测试", category="行业规范",
        section_path="第一章", source_path="test.pdf", text="正文",
        score=score, matched_children=[], rrf_score=rrf,
    )


def test_default_off_records_snapshot_and_allows(monkeypatch):
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_ENABLED", False)
    decision = evaluate_relevance(
        [source(0.8, 0.2), source(0.5, 0.1)],
        has_history=False,
        decomposition_applied=False,
    )
    assert decision.action == "allow"
    assert decision.reason == "disabled"
    assert decision.top1_score == 0.8
    assert decision.top2_score == 0.5
    assert decision.score_margin == 0.30000000000000004
    assert decision.top1_rrf == 0.2


def test_enabled_gate_rejects_low_score(monkeypatch):
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_MIN_SCORE", 0.7)
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_MIN_RRF", 0.0)
    decision = evaluate_relevance(
        [source(0.6)], has_history=False, decomposition_applied=False,
    )
    assert decision.action == "low_confidence"
    assert decision.reason == "top1_score"


def test_history_and_decomposition_are_ineligible(monkeypatch):
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_MIN_SCORE", 0.9)
    base = source(0.1)
    history = evaluate_relevance([base], has_history=True, decomposition_applied=False)
    decomposed = evaluate_relevance(
        [replace(base, subquery_idx=0)], has_history=False, decomposition_applied=True,
    )
    assert history.action == decomposed.action == "allow"
    assert history.reason == decomposed.reason == "ineligible_path"


def test_sync_session_skips_generation_when_gate_rejects(monkeypatch):
    candidate = source(0.1)
    monkeypatch.setattr("src.session.retrieve", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_MIN_SCORE", 0.9)
    generate = monkeypatch.setattr(
        "src.session.generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generate called")),
    )
    result = ChatSession().ask("高强度螺栓有什么要求？")
    assert result.relevance["action"] == "low_confidence"
    assert result.sources == []
    assert result.final_sources == []
    assert "未找到足够相关" in result.answer_text


def test_stream_session_skips_generation_and_keeps_history_clean(monkeypatch):
    candidate = source(0.1)
    monkeypatch.setattr("src.session.retrieve", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_ENABLED", True)
    monkeypatch.setattr("src.relevance_gate.RELEVANCE_GATE_MIN_SCORE", 0.9)
    monkeypatch.setattr(
        "src.session.stream_generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stream_generate called")),
    )
    session = ChatSession()
    prep, stream = session.ask_stream("高强度螺栓有什么要求？")
    answer = "".join(stream)
    assert prep.relevance["action"] == "low_confidence"
    assert prep.used_sources == []
    assert "未找到足够相关" in answer
    assert session.last_turn_result is not None
    assert session.last_turn_result.final_sources == []
    assert session.state.messages[-1].content == answer
