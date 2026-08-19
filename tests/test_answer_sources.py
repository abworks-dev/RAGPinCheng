from types import SimpleNamespace

from src.answer_policy import AnswerPolicy
from src.generate import GenerationPrep, finalize_answer_sources, generate
from src.retrieve import RetrievedParent
from src.session import ChatSession


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
    assert session.state.messages[-1].sources_for_ui == []
    assert session.state.last_sources == candidates
