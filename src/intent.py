"""Query-intent detection for retrieval shaping.

RAG answers most questions with focused parent fragments, but "enumerate /
count" questions (e.g. "机电管综培训总共有几个培训视频", "有哪些章节", "分别列出")
need a wider candidate window so every relevant source survives the final
top-k cutoff.  This module identifies that intent with lightweight rules, so
the retrieval top-k can be raised and a compact title inventory can be
injected into the answer context.
"""
from __future__ import annotations

import re

# Markers that strongly suggest the user wants a list / count / enumeration
# of items rather than an answer from one specific passage.
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
)


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