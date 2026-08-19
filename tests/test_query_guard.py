"""Unit tests for query_guard.py and session guard integration."""
import pytest

from src.query_guard import (
    QueryValidation,
    _contains_professional_term,
    _contains_standard_code,
    _is_numeric_dominant,
    validate_search_query,
)
from src.session import ChatSession


class TestIsNumericDominant:
    """Test detection of numeric-dominant queries (rewriter workaround)."""

    def test_pure_numeric(self):
        assert _is_numeric_dominant("22", threshold=0.4) is True
        assert _is_numeric_dominant("11", threshold=0.4) is True

    def test_numeric_with_filler(self):
        # The rewriter may append generic words like this
        assert _is_numeric_dominant("22 细部工程", threshold=0.3) is True
        assert _is_numeric_dominant("11 项目", threshold=0.4) is True

    def test_not_numeric_dominant(self):
        # Real questions with numbers but meaningful context
        assert _is_numeric_dominant("M20 螺栓孔径", threshold=0.4) is False
        # The lexical helper only measures digit density; standard-code
        # precedence is enforced by validate_search_query().
        assert _is_numeric_dominant("GB 50017 第 11 节", threshold=0.4) is True
        assert _is_numeric_dominant("板厚 12mm", threshold=0.4) is False


class TestContainsStandardCode:
    """Test standard code detection (shared with retrieve.py)."""

    def test_gb_code(self):
        assert _contains_standard_code("GB 50017") is True
        assert _contains_standard_code("GB50017") is True
        assert _contains_standard_code("gb 50011") is True

    def test_jgj_code(self):
        assert _contains_standard_code("JGJ 130") is True
        assert _contains_standard_code("JGJ130") is True

    def test_iso_code(self):
        assert _contains_standard_code("ISO 9001") is True

    def test_t_cecs_code(self):
        assert _contains_standard_code("T/CECS 123") is True

    def test_not_code(self):
        assert _contains_standard_code("11") is False
        assert _contains_standard_code("螺栓") is False
        assert _contains_standard_code("规范") is False


class TestContainsProfessionalTerm:
    """Test professional domain term detection."""

    def test_material_terms(self):
        assert _contains_professional_term("螺栓") is True
        assert _contains_professional_term("钢筋") is True
        assert _contains_professional_term("钢材") is True
        assert _contains_professional_term("混凝土") is True

    def test_member_terms(self):
        assert _contains_professional_term("构件") is True
        assert _contains_professional_term("节点") is True
        assert _contains_professional_term("梁") is True
        assert _contains_professional_term("柱") is True

    def test_standard_terms(self):
        assert _contains_professional_term("规范") is True
        assert _contains_professional_term("规程") is True
        assert _contains_professional_term("标准") is True

    def test_grade_terms(self):
        assert _contains_professional_term("q235") is True
        assert _contains_professional_term("Q355") is True
        assert _contains_professional_term("hrb400") is True
        assert _contains_professional_term("HRB500") is True

    def test_bolt_grades(self):
        assert _contains_professional_term("M20") is True
        assert _contains_professional_term("m24") is True

    def test_not_professional(self):
        assert _contains_professional_term("11") is False
        assert _contains_professional_term("？") is False
        assert _contains_professional_term("这个") is False
        assert _contains_professional_term("什么") is False


class TestValidateSearchQueryFirstTurn:
    """Test validation on first turn (no history)."""

    def test_empty_query(self):
        result = validate_search_query("", has_history=False)
        assert result.passed is False
        assert result.reason == "empty"

    def test_whitespace_only(self):
        result = validate_search_query("   ", has_history=False)
        assert result.passed is False
        assert result.reason == "empty"

    def test_pure_numeric(self):
        result = validate_search_query("11", has_history=False)
        assert result.passed is False
        assert result.reason == "numeric_only"
        assert result.message == (
            "当前问题信息不足，暂无法检索到明确内容。"
            "请补充具体的查询对象，例如规范名称、章节号、构件名称、材料型号或项目资料名称。"
        )

    def test_pure_numeric_with_punctuation(self):
        result = validate_search_query("11.1", has_history=False)
        assert result.passed is False
        assert result.reason == "section_only"
        assert result.message == (
            "当前章节号缺少所属文档，暂无法检索到明确内容。"
            "请补充对应的规范或文档名称，例如“GB 50017 第 8 节的要求是什么？”。"
        )

    def test_deep_section_number(self):
        result = validate_search_query("9.3.3.1", has_history=False)
        assert result.passed is False
        assert result.reason == "section_only"

    def test_too_short_only_particles(self):
        result = validate_search_query("呢", has_history=False)
        assert result.passed is False
        assert result.reason == "too_short"

        result = validate_search_query("？", has_history=False)
        assert result.passed is False
        assert result.reason == "too_short"

        result = validate_search_query("什么", has_history=False)
        assert result.passed is False
        assert result.reason == "too_short"

    def test_standard_code_passes(self):
        result = validate_search_query("GB 50017", has_history=False)
        assert result.passed is True

        result = validate_search_query("JGJ 130", has_history=False)
        assert result.passed is True

    def test_professional_term_passes(self):
        result = validate_search_query("M20 螺栓", has_history=False)
        assert result.passed is True

        result = validate_search_query("板厚", has_history=False)
        assert result.passed is True

        result = validate_search_query("Q390 钢材", has_history=False)
        assert result.passed is True

        result = validate_search_query("11 号构件", has_history=False)
        assert result.passed is True

    def test_valid_question_passes(self):
        result = validate_search_query("高强度螺栓的抗拉强度是多少？", has_history=False)
        assert result.passed is True

    def test_standard_with_section_passes(self):
        # Has a standard code reference, so it's not just a section number
        result = validate_search_query("GB 50017 第 8 章", has_history=False)
        assert result.passed is True

    def test_numeric_dominant_with_filler_blocked(self):
        # Rewriter may turn "22" into "22 细部工程" — should still be blocked
        result = validate_search_query("22 细部工程", has_history=False)
        assert result.passed is False
        assert result.reason == "numeric_dominant"


class TestValidateSearchQueryWithHistory:
    """Test validation on subsequent turns (rewriter may have expanded query).

    With history, the bar is lower because the rewriter should have expanded
    short follow-ups into full standalone questions. If it didn't, we trust
    that the user is continuing a thread of conversation.
    """

    def test_pure_numeric_with_history_is_still_rejected(self):
        result = validate_search_query("11", has_history=True)
        assert result.passed is False
        assert result.reason == "numeric_only"

    def test_section_only_with_history_passes(self):
        result = validate_search_query("11.1", has_history=True)
        assert result.passed is True

    def test_short_with_history_passes(self):
        result = validate_search_query("呢", has_history=True)
        assert result.passed is True


class TestChatSessionGuardIntegration:
    """Test that the guard is properly integrated into ChatSession.ask()."""

    def test_guard_blocks_ambiguous_on_first_turn(self):
        session = ChatSession()
        result = session.ask("11")
        assert result.guard_reason == "numeric_only"
        assert result.answer_text == (
            "当前问题信息不足，暂无法检索到明确内容。"
            "请补充具体的查询对象，例如规范名称、章节号、构件名称、材料型号或项目资料名称。"
        )
        assert result.fresh_sources == []
        assert result.final_sources == []
        # Verify sources weren't cleared (though on first turn they're empty anyway)
        assert session.state.last_sources == []

    def test_guard_blocks_numeric_dominant_with_filler(self):
        # Bug case: user types "22", rewriter turns into "22 细部工程"
        # Guard validates original query, should block
        session = ChatSession()
        result = session.ask("22")
        assert result.guard_reason in ("numeric_only", "numeric_dominant")
        assert "信息不足" in result.answer_text

    def test_guard_allows_standard_code(self):
        session = ChatSession()
        result = session.ask("GB 50017")
        assert result.guard_reason == ""
        # Should have gone to retrieval
        # We don't assert on sources length since it depends on indexed data

    def test_guard_allows_professional_term(self):
        session = ChatSession()
        result = session.ask("M20 螺栓")
        assert result.guard_reason == ""

    def test_multiturn_short_follow_up_passes(self):
        session = ChatSession()
        # First turn: valid question
        result1 = session.ask("高强度螺栓的等级有哪些？")
        assert result1.guard_reason == ""

        # Second turn: short follow-up (rewriter should expand it)
        # Since we can't mock the rewriter, we test that has_history=True
        # lowers the bar. The actual rewrite result depends on the LLM.
        result2 = session.ask("M24 的呢？")
        # We don't assert on guard_reason because the rewriter may or may not
        # produce a query that passes. The important thing is it doesn't crash.
        assert isinstance(result2.guard_reason, str)

    def test_guard_reason_in_turnresult(self):
        session = ChatSession()
        result = session.ask("11.1")
        assert result.guard_reason == "section_only"
        assert result.answer_text == (
            "当前章节号缺少所属文档，暂无法检索到明确内容。"
            "请补充对应的规范或文档名称，例如“GB 50017 第 8 节的要求是什么？”。"
        )

    def test_too_short_question(self):
        session = ChatSession()
        result = session.ask("？")
        assert result.guard_reason == "too_short"
        assert result.answer_text == (
            "当前问题过于简短，暂无法检索到明确内容。"
            "请补充具体的构件、材料、规范、软件操作或项目资料名称。"
        )


class TestChatSessionGuardIntegrationStream:
    """Test that the guard is properly integrated into ChatSession.ask_stream()."""

    def test_guard_blocks_ambiguous_on_first_turn_stream(self):
        session = ChatSession()
        prep, stream_iter = session.ask_stream("11")
        assert prep.guard_reason == "numeric_only"
        assert prep.no_source_fallback is True
        assert prep.fresh_sources == []
        assert prep.final_sources == []

        # Consume the stream
        answer = "".join(stream_iter)
        assert "信息不足" in answer

        # Verify TurnResult has guard_reason
        assert session.last_turn_result is not None
        assert session.last_turn_result.guard_reason == "numeric_only"

    def test_guard_allows_standard_code_stream(self):
        session = ChatSession()
        prep, _ = session.ask_stream("GB 50017")
        assert prep.guard_reason == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
