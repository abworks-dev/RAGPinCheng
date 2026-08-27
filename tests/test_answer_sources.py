from types import SimpleNamespace

from src.answer_policy import AnswerPolicy
from src.generate import (
    GenerationPrep,
    finalize_answer_sources,
    finalize_answer_sources_with_diagnostics,
    generate,
)
from src.retrieve import RetrievedParent
from src.session import ChatSession
from api.schemas import DoneEvent
from src.prompts import load_prompt


def _sources(count: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(parent_id=f"source-{index}") for index in range(1, count + 1)]


def _parents(count: int) -> list[RetrievedParent]:
    return [
        RetrievedParent(
            parent_id=f"source-{index}",
            doc_title=f"资料 {index}",
            category="行业规范",
            section_path=f"第 {index} 节",
            source_path=f"source-{index}.pdf",
            text=f"正文 {index}",
            score=1.0,
            matched_children=[],
            rrf_score=1.0,
        )
        for index in range(1, count + 1)
    ]


def test_final_sources_follow_first_citation_order_and_renumber_sparse_indexes():
    sources = _sources(5)

    text, cited = finalize_answer_sources("第二项要求[5]，第一项要求[2]，再次说明[5]。", sources)

    assert text == "第二项要求[1]，第一项要求[2]，再次说明[1]。"
    assert [source.parent_id for source in cited] == ["source-5", "source-2"]


def test_final_sources_support_grouped_citations_without_duplicates():
    sources = _sources(5)

    text, cited = finalize_answer_sources("综合要求[3, 1，3、5]。", sources)

    assert text == "综合要求[1][2][3]。"
    assert [source.parent_id for source in cited] == ["source-3", "source-1", "source-5"]


def test_final_sources_drop_invalid_citations_and_unreferenced_candidates():
    sources = _sources(5)

    text, cited = finalize_answer_sources("有效要求[2]，无效编号[0][9]。", sources)

    assert text == "有效要求[1]，无效编号。"
    assert [source.parent_id for source in cited] == ["source-2"]


def test_no_answer_does_not_publish_context_candidates():
    text, cited = finalize_answer_sources("未找到相关内容。", _sources(5))

    assert text == "未找到相关内容。"
    assert cited == []


def test_citation_diagnostics_distinguish_valid_invalid_uncited_and_no_answer():
    candidates = _parents(3)

    valid = finalize_answer_sources_with_diagnostics("要求如下[2]。", candidates)
    invalid = finalize_answer_sources_with_diagnostics("要求如下[2][9]。", candidates)
    uncited = finalize_answer_sources_with_diagnostics("要求如下。", candidates)
    no_answer = finalize_answer_sources_with_diagnostics("未找到相关内容。", candidates)

    assert valid.diagnostics["status"] == "valid"
    assert valid.diagnostics["candidate_count"] == 3
    assert valid.diagnostics["cited_count"] == 1
    assert invalid.diagnostics["status"] == "invalid_citations"
    assert invalid.diagnostics["invalid_citation_numbers"] == [9]
    assert uncited.diagnostics["status"] == "uncited"
    assert uncited.diagnostics["uncited_answer"] is True
    assert no_answer.diagnostics["status"] == "no_answer"
    assert no_answer.diagnostics["uncited_answer"] is False
    assert no_answer.diagnostics["uncited_statement_count"] == 0


def test_citation_diagnostics_report_location_coverage_and_version_conflict():
    first, second = _parents(2)
    first.content_item_id = second.content_item_id = "item-1"
    first.content_version_id = "version-1"
    second.content_version_id = "version-2"
    first.page_number = 8

    result = finalize_answer_sources_with_diagnostics("要求[1]，补充要求[2]。", [first, second])

    assert result.diagnostics["located_count"] == 1
    assert result.diagnostics["version_conflict"] is True


def test_citation_diagnostics_detect_partially_uncited_factual_sentences():
    result = finalize_answer_sources_with_diagnostics(
        "第一项要求[1]。第二项要求没有引用。",
        _parents(2),
    )

    assert result.diagnostics["status"] == "uncited"
    assert result.diagnostics["uncited_statement_count"] == 1


def test_generate_returns_only_sources_cited_by_the_model(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="要求如下[2]。"))],
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response),
        )
    )
    monkeypatch.setattr("src.generate._client", lambda: client)

    answer = generate("有什么要求？", _parents(3), budget=10_000)

    assert answer.text == "要求如下[1]。"
    assert [source.parent_id for source in answer.sources] == ["source-2"]
    assert answer.citation_diagnostics["status"] == "valid"


def test_stream_finalization_persists_no_answer_without_public_sources():
    session = ChatSession()
    candidates = _parents(5)
    prep = GenerationPrep(
        used_sources=candidates,
        messages=[],
        model="test-model",
        context_chars=100,
        budget=1_000,
        policy=AnswerPolicy(),
    )

    session._finalize_streaming_turn(
        query="不存在的要求是什么？",
        search_query="不存在的要求是什么？",
        rewrite_applied=False,
        fresh_sources=candidates,
        final_sources=candidates,
        gen_prep=prep,
        full_text="未找到相关内容。",
        history_chars=0,
        budget=1_000,
        timings={},
        rewrite_usage={},
    )

    assert session.last_turn_result is not None
    assert session.last_turn_result.sources == []
    assert session.last_turn_result.citation_diagnostics["status"] == "no_answer"
    assert session.state.messages[-1].sources_for_ui == []
    assert session.state.last_sources == candidates


def test_done_event_documents_citation_diagnostics():
    event = DoneEvent(
        timings={}, sources=[], answer_text="回答", history_chars=0, budget=0,
        citation_diagnostics={"status": "uncited"},
    )

    assert event.model_dump()["citation_diagnostics"] == {"status": "uncited"}


def test_answer_prompt_requires_each_factual_sentence_to_carry_a_citation():
    prompt = load_prompt("answer_system")

    assert "每个事实性句子" in prompt
    assert "不得把多个事实句的引用统一放到最后一句" in prompt
