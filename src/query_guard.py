"""Query validation guard for blocking ambiguous/vague inputs before retrieval.

Designed to catch inputs like "11", "22", "?", "这个", "标准" that would
force the retrieval pipeline to return low-quality matches and then force
the LLM to hallucinate an answer.

Rules follow a priority:
  1. Always pass when a recognized standard code (GB 50017, JGJ etc.) is present.
  2. Block pure-numeric inputs on every turn.
  3. Block isolated section numbers (11.1, 3.2.4) on the first turn.
  4. Pass recognized professional objects before applying numeric heuristics.
  5. Block numeric-dominant and overly-short first-turn inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Reuse the same standard-code regex from retrieve.py so we have consistent
# "what counts as a standard code" semantics across the codebase.
# Matches BIM-relevant standard codes (case-insensitive).
_CODE_RE = re.compile(
    r"(?<![A-Za-z])("
    r"T[/／](?:CECS|CCES|CSCS|CCS)"
    r"|"
    r"(?:GB|JGJ|JG|CJJ|YB|TB|JC|CECS|DBJ|DB)(?:[/／]T)?"
    r"|"
    r"ISO"
    r")\s*[-—/／\s]?\s*"
    r"(\d{2,5}(?:[-．\.]\d+)*)",
    re.IGNORECASE,
)

# Common professional object markers in BIM domain.
# If the query contains any of these, it's almost certainly a valid question.
_PROFESSIONAL_TERMS = {
    "螺栓", "钢筋", "钢材", "构件", "焊缝", "节点", "楼板", "梁", "柱", "墙",
    "混凝土", "型钢", "钢管", "幕墙", "玻璃", "防火", "防腐", "抗震", "挠度",
    "孔径", "间距", "厚度", "宽度", "高度", "长度", "直径", "半径",
    "规范", "规程", "标准", "图集", "图纸", "建模", "revit", "cad",
    "m10", "m12", "m16", "m20", "m24", "m30",
    "q235", "q345", "q355", "q390", "q420",
    "hrb400", "hrb500",
}

# Isolated section number: digits.digits.digits... with nothing else meaningful.
# Matches "11.1", "3.2.4", "9.3.3.1" but NOT "11", "GB 50017 11.1" or
# "第11章" (those have surrounding context).
_SECTION_ONLY_RE = re.compile(r"^\s*\d+(?:[．.]\d+)+\s*$")

# Purely numeric/punctuation: optional whitespace around digits/punctuation only.
_PURE_NOISE_RE = re.compile(r"^\s*[\d\W_]+\s*$")

# Pure digits only — no Chinese characters, no letters.
# "222" matches; "11.1" and "那 22 呢" do not.
_PURE_DIGITS_ONLY_RE = re.compile(r"^\s*\d[\d\s]*\s*$")

_INSUFFICIENT_QUERY_MESSAGE = (
    "当前问题信息不足，暂无法检索到明确内容。"
    "请补充具体的查询对象，例如规范名称、章节号、构件名称、材料型号或项目资料名称。"
)
_SECTION_WITHOUT_DOCUMENT_MESSAGE = (
    "当前章节号缺少所属文档，暂无法检索到明确内容。"
    "请补充对应的规范或文档名称，例如“GB 50017 第 8 节的要求是什么？”。"
)
_TOO_SHORT_QUERY_MESSAGE = (
    "当前问题过于简短，暂无法检索到明确内容。"
    "请补充具体的构件、材料、规范、软件操作或项目资料名称。"
)


@dataclass
class QueryValidation:
    """Result of query validation.

    When `pass` is False, retrieval and generation should be skipped; the
    caller should return `message` directly to the user.

    `reason` is a machine-readable tag for telemetry and debugging panels.
    """

    passed: bool
    reason: str = ""
    message: str = ""

    @classmethod
    def ok(cls) -> QueryValidation:
        return cls(passed=True)

    @classmethod
    def reject(cls, reason: str, message: str) -> QueryValidation:
        return cls(passed=False, reason=reason, message=message)


def _contains_standard_code(query: str) -> bool:
    """Check if the query mentions a recognizable standard code.

    Uses the exact same regex as the code-boost prefetch in retrieve.py,
    so "GB 50011", "JGJ 130", "T/CECS xxx" etc. all pass through
    automatically, even if they are short.
    """
    return bool(_CODE_RE.search(query))


def _is_numeric_dominant(query: str, threshold: float = 0.5) -> bool:
    """Detect if the query is dominated by numeric characters.

    For cases like "22 细部工程" where the rewriter appended some generic
    words but the core intent is still just a number. Returns True when
    numeric characters exceed `threshold` proportion of non-whitespace.
    """
    stripped = query.strip()
    if not stripped:
        return False
    non_whitespace = len(stripped.replace(" ", ""))
    numeric_count = sum(1 for ch in stripped if ch.isdigit())
    return numeric_count / max(non_whitespace, 1) >= threshold


def _contains_professional_term(query: str) -> bool:
    """Check if the query mentions at least one professional domain term.

    This is a lightweight lexical check, not NLU. If the user mentions a
    bolt, steel bar, or material grade, they know what they're asking for.
    """
    q = query.lower()
    return any(term in q for term in _PROFESSIONAL_TERMS)


def validate_search_query(search_query: str, *, has_history: bool) -> QueryValidation:
    """Validate the original user query before running retrieval.

    ChatSession deliberately validates the original text rather than the
    rewritten query, because a rewrite model can invent context around a bare
    number and accidentally turn an ambiguous input into a plausible query.

    Args:
        search_query: The original user text.
        has_history: Whether this is not the first turn in the conversation.
            When True, the validation bar is lower because the rewriter had a
            chance to expand a short follow-up; if it couldn't expand it,
            we're more confident the input truly is ambiguous.
    """
    stripped = search_query.strip()

    # Empty or whitespace-only → always reject.
    if not stripped:
        return QueryValidation.reject(
            reason="empty",
            message="请输入您的问题。",
        )

    # Standard code found → definitely a domain question; pass immediately.
    # This covers "GB 50011", "05G336", etc. even though they are short.
    if _contains_standard_code(stripped):
        return QueryValidation.ok()

    # Pure digits only — no Chinese characters, no letters.
    # ALWAYS reject, even with history. "222" is meaningless without context;
    # the rewriter will turn it into "What is 222?" but the history may not
    # contain any document reference that gives 222 meaning.
    # Legitimate follow-ups like "那 22 呢?" will have Chinese characters
    # and pass this check.
    if _PURE_DIGITS_ONLY_RE.match(stripped):
        if not has_history:
            return QueryValidation.reject(
                reason="numeric_only",
                message=_INSUFFICIENT_QUERY_MESSAGE,
            )
        return QueryValidation.reject(
            reason="numeric_only",
            message=_INSUFFICIENT_QUERY_MESSAGE,
        )

    # Isolated section number with no surrounding document context.
    # "11.1" → reject; "GB 50017 11.1" → passed by the code check above.
    # With history, this could be a legitimate follow-up like "那 11.1 呢?".
    if _SECTION_ONLY_RE.match(stripped):
        if not has_history:
            return QueryValidation.reject(
                reason="section_only",
                message=_SECTION_WITHOUT_DOCUMENT_MESSAGE,
            )
        return QueryValidation.ok()

    # Professional context wins over the generic numeric heuristic. This is
    # what keeps valid queries such as "M20 螺栓", "Q345 钢材" and
    # "11 号构件" from being rejected merely because they contain digits.
    if _contains_professional_term(stripped):
        return QueryValidation.ok()

    # Pure punctuation/noise carries no retrievable intent.
    if _PURE_NOISE_RE.match(stripped):
        return QueryValidation.reject(
            reason="too_short",
            message=_TOO_SHORT_QUERY_MESSAGE,
        )

    # A first-turn query dominated by digits but lacking a recognized object
    # is ambiguous. With history, non-bare numeric follow-ups are allowed so
    # the already-computed rewrite can carry the prior object into retrieval.
    if _is_numeric_dominant(stripped, threshold=0.3):
        if not has_history:
            return QueryValidation.reject(
                reason="numeric_dominant",
                message=_INSUFFICIENT_QUERY_MESSAGE,
            )
        return QueryValidation.ok()

    # Very short input with no identifiable subject.
    # With history, we trust the rewriter to have carried context forward;
    # without history, we ask for clarification.
    if len(stripped) <= 4 and not has_history:
        # 2-4 characters is a common "this", "how", "standard" case.
        # Check one more time: if it has at least one Chinese character that
        # is not a pure question particle, maybe pass?
        particles = {"呢", "啊", "吗", "吧", "哦", "嗯", "？", "?", "什", "么", "怎", "这", "那", "此"}
        has_real_content = any(ch for ch in stripped if "一" <= ch <= "鿿" and ch not in particles)
        if not has_real_content:
            return QueryValidation.reject(
                reason="too_short",
                message=_TOO_SHORT_QUERY_MESSAGE,
            )

    return QueryValidation.ok()
