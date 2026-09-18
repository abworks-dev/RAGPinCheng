"""Query-intent detection for retrieval shaping and answering stance.

Classifies user input into one of four intents so the pipeline can pick the
right retrieval breadth and the right system-prompt stance:

- ``greeting``   : social salutations / self-intro asks. Never run RAG — the
                   session returns a canned self-introduction instead.
- ``enumerate``  : list/count questions (e.g. "总共有几个培训视频", "有哪些章节").
                   Uses a wider top-k + title recall + compact inventory so
                   every relevant item is surfaced and listed.
- ``comparison`` : which-to-choose / comparative questions (decompose path).
- ``fact``       : ordinary factual / how-to questions (default, regular RAG).

This keeps the enumerate/count helpers from the earlier work and adds the
routing used to pick the answering stance.
"""
from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    GREETING = "greeting"
    ENUMERATE = "enumerate"
    COMPARISON = "comparison"
    FACT = "fact"


# ── greeting ────────────────────────────────────────────────────────────────

# Pure greetings / social openers — never knowledge lookups.  Also covers the
# "你能做什么 / 你会什么" self-intro asks so they get a helpful assistant caps
# instead of a cold "未找到相关内容".
_GREETING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Pure salutations / social openers — short and self-contained.
    re.compile(r"^(?:你)?(?:好|嗨|哈喽|hi|hello|hey|早上好|下午好|晚上好|晚安|在吗|在么|在不在)[!！~～。.]*$", re.IGNORECASE),
    # "你能做什么 / 你会做什么 / 你可以做什么 / 你是谁 / 你是做什么的" style asks.
    re.compile(r"^(?:请问?)?你?(?:能|会|可以)?(?:做什么|干什么|能做什么|可以做什么|会做什么|帮我什么|帮我做点什么|帮我回答什么)[！!~～。.?？]*$"),
    re.compile(r"^(?:你|好|嗨|哈喽|hi|hello|hey|在吗|在么|请问|嗨喽){0,2}\s*你?(?:能|会|可以)?(?:做什么|干什么|能做什么|会做什么|可以做什么)\s*[！!~～。.?？]*$", re.IGNORECASE),
    re.compile(r"^你(?:是谁|是做什么的|是干嘛的|是谁呀)[！!~～。.?？]*$"),
    re.compile(r"^[!！~？?。.\s]*$"),  # pure punctuation
    re.compile(r"^(?:谢谢|感谢|谢谢啦|多谢|辛苦|好的|ok|okay|收到)[!！~～。.…]*$", re.IGNORECASE),
)

# Domain terms that make a query a real knowledge lookup, not social chatter.
# If present, the input is almost certainly factual — never classed as greeting.
_GREETING_EXCLUDE_SUBSTR = (
    "雨水管", "管线", "管材", "风管", "桥架", "Revit", "CAD", "BIM",
    "钢筋", "构件", "楼板", "梁", "柱", "墙", "规范", "标准", "图集", "建模",
    "培训", "视频", "章节", "流程", "操作", "审核", "检查",
)


def is_greeting(query: str) -> bool:
    text = query.strip()
    if not text:
        return True  # empty input is treated as social noise
    if any(sub in text for sub in _GREETING_EXCLUDE_SUBSTR):
        return False
    return any(p.search(text) for p in _GREETING_PATTERNS)

_ENUMERATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"总共\s*(?:有|是)?\s*\d*\s*个"),
    re.compile(r"(?:有|是)\s*(?:几个|多少个|哪些|哪几个)"),
    re.compile(r"分别\s*(?:是|有|列出)"),
    re.compile(r"列出\s*(?:所有|全部)?\s*(?:的)?[^，。？?]{0,12}(?:清单|列表)"),
    re.compile(r"(?:有哪些|哪几类|哪些类)"),
    re.compile(r"共计|合计|总共有|一共有"),
    re.compile(r"全部\s*(?:的)?[^，。？?]{0,10}(?:视频|文件|章节|部分|条|项)"),
    re.compile(r"第[一二三四五六七八九十]+\s*[部章讲课节]"),
    re.compile(r"[几多少]\s*[部章讲课节个视频]"),
    # Ordinal / section-range requests: "第10个视频是什么", "第十个章节",
    # "1-10 讲了什么", "第一到第十个视频分别讲什么", "各章节分别讲什么".
    re.compile(r"第\s*\d+\s*(?:个|段)[^，。？?]{0,8}(?:视频|章节|部分|内容|文件)"),
    re.compile(r"第[一二三四五六七八九十]+\s*(?:个|段)[^，。？?]{0,8}(?:视频|章节|部分|内容|文件)"),
    re.compile(r"\b\d+\s*[-—–~至]\s*\d+\b[^，。？?]{0,10}(?:讲了|讲什么|内容|章节|视频|列出来|分别)"),
    re.compile(r"(?:把|将)?[^，。？?]{0,8}\d+\s*[-—–~至]\s*\d+[^，。？?]{0,10}(?:讲了|内容|列出来|分别|是什么)"),
    re.compile(r"第[一二三四五六七八九十]+到第[一二三四五六七八九十]+[^，。？?]{0,8}(?:视频|章节|内容)"),
    re.compile(r"各(?:个|段)?(?:视频|章节|部分|章节内容)[^，。？?]{0,6}(?:讲|内容|是什么|分别)"),
)

# Choose-between / comparative markers → decompose path (retrieve_multi).
_COMPARISON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:哪个|哪种|哪些好|选哪个|该选|应该选|更好|更合适|更推荐|对比|比较|区别|差异|优劣|好一点|适合)"),
    re.compile(r"(?:和|与|vs|vs\.|VS)[^。？?]{0,6}(?:哪个|相比|对比|比较|区别)"),
    re.compile(r"(?:二选一|多选一|怎么选)"),
)


def is_comparison(query: str) -> bool:
    text = query.strip()
    if not text:
        return False
    return any(p.search(text) for p in _COMPARISON_PATTERNS)


def classify_intent(query: str, *, standalone_query: str | None = None) -> Intent:
    """Route the query to an intent for retrieval breadth + answer stance.

    Priority: greeting (short-circuit) → enumerate (list/count) →
    comparison (decompose) → fact (default).  The rewrite-standalone form is
    also considered so a follow-up that was expanded to an enumeration still
    routes correctly.
    """
    candidates = [query]
    if standalone_query and standalone_query.strip():
        candidates.append(standalone_query)

    if any(is_greeting(c) for c in candidates):
        return Intent.GREETING
    if is_enumeration_intent(query, standalone_query=standalone_query):
        return Intent.ENUMERATE
    if any(is_comparison(c) for c in candidates):
        return Intent.COMPARISON
    return Intent.FACT


def is_enumeration_intent(query: str, *, standalone_query: str | None = None) -> bool:
    """Return True when the question asks to enumerate/count items.

    Matches either the original or the rewrite-standalone query so follow-up
    questions still benefit after `rewrite_query` consolidates context.
    """
    candidates = [query]
    if standalone_query and standalone_query.strip():
        candidates.append(standalone_query)
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        for pattern in _ENUMERATION_PATTERNS:
            if pattern.search(text):
                return True
    return False


# Tokens to strip when extracting a title-recall key from an enumeration query.
_ENUMERATION_NOISE = (
    r"总共有|一共有|共计|合计|总共|共有",
    r"几个|多少个|哪些|哪几个|哪些类|几类|几个培训视频|多少",
    r"有哪些|有什么|有没有|分别有哪些",
    r"培训视频|教学视频|视频|章节|部分|清单|列表|分别|第[一二三四五六七八九十]+\s*[部章讲课节]",
    r"的是|有多少",
    # Ordinal / section-range scaffolding: "第十个视频/第10个章节/1-10/第一到第十/各章节".
    r"第\s*\d+\s*(?:个|段)|第[一二三四五六七八九十]+到第|第[一二三四五六七八九十]+\s*(?:个|段)",
    r"\b\d+\s*[-—–~至]\s*\d+\b",
    r"讲了|讲什么|讲了些|内容|是什么|分别|各",
)


def extract_series_token(query: str) -> str:
    """Best-effort extraction of a title-recall key from an enumeration query.

    Removes enumeration scaffolding ("总共有几个培训视频" etc.) so the remainder
    is a short series keyword ("机电管综培训") usable for title LIKE matching.
    Falls back to the whole query on no match.  Only used to widen recall for
    enumeration questions; never affects other queries.
    """
    text = query.strip()
    if not text:
        return ""
    stripped = text
    # Drop punctuation and trailing enumeration markers first.
    stripped = re.sub(r"[，。？?！!、：:]+", " ", stripped)
    for noise in _ENUMERATION_NOISE:
        stripped = re.sub(noise, " ", stripped)
    stripped = re.sub(r"\s+", "", stripped)
    # The series keyword is usually the leading noun chunk; return longest
    # non-empty segment (whole leftover if nothing usable).
    if len(stripped) >= 2:
        return stripped
    return text.strip()