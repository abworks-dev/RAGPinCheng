"""Pure attachment-search ports and intent helpers (no API or storage imports)."""
from __future__ import annotations

import re
from typing import Protocol


class AttachmentSearch(Protocol):
    def __call__(self, query: str, *, categories: list[str] | None = None, limit: int = 5) -> list[dict]: ...


class AttachmentValidator(Protocol):
    def __call__(self, results: list[dict], *, categories: list[str] | None = None) -> list[dict]: ...


def attachment_query_terms(query: str) -> list[str]:
    """Small lexical grammar, not semantic rewriting; unknown constraints stay literal.

    Refuse unsupported negative requests rather than silently dropping their polarity.
    Never truncate terms: that could discard a project, floor or drawing identity.
    """
    if re.fullmatch(r'[^\s]+\.(?:png|jpg|jpeg|pdf|dwg|rvt|xlsx|pptx)', query, re.I):
        return [query]
    if re.search(r'不含|不包含|没有|不带|不要|排除|除了|非|不是|无开闭所', query):
        return []
    text = re.sub(r'^(?:(?:请|帮我|麻烦|给我|想要|我要|寻找|查找|搜索|找一下|找|一下|查看|打开|预览|下载)\s*)+', '', query)
    if text == query:
        words = list(dict.fromkeys(query.split()))
        return words if 0 < len(words) <= 12 else []
    text = re.sub(r'^(?:一个|一份|一张|一些|个|份|张)\s*', '', text)
    text = re.sub(r'(?:的)?(?:案例)?(?:图纸|文件|附件)(?:案例)?(?:在哪|在哪里|哪里)?[？?。！!]*$', '', text).strip()
    # Split only bounded grammar positions, not connector characters inside an
    # unknown project/name (e.g. 内含项目 must remain an identity constraint).
    text = re.sub(r'(地下(?:室)?[一二三四五六七八九十0-9]+层|负[一二三四五六七八九十0-9]+层|\bb[0-9]+\b)', r' \1 ', text)
    text = re.sub(r'(^|\s)(?:包含|带有|含有|设有|含|带)', r'\1', text)
    text = re.sub(r'的(?=\s|$|[a-z0-9])', ' ', text)
    words = list(dict.fromkeys(text.split()))
    return words if 0 < len(words) <= 12 else []


def file_intent(query: str) -> bool:
    # Content questions still use normal RAG even when they mention a file.
    if re.search(r"(?:是什么|为什么|如何|怎么|要求|规定|解释|总结|分析|说明)", query) and not re.search(r"(?:打开|预览|下载|发我|给我)", query):
        return False
    return bool(re.search(r"(?:找|搜索|查找|查看|打开|预览|下载|发我|给我).*(?:文件|附件|图片|照片|图纸|pdf|xlsx|pptx)|(?:文件|附件|图片|照片|图纸).*(?:在哪|哪里)", query, re.I))


def file_reference(query: str) -> int | None:
    match = re.search(r"第([一二三四五六七八九十0-9]+)(?:个|份|张)?(?:文件|附件|图片)", query)
    if not match:
        return None
    token = match.group(1)
    return int(token) - 1 if token.isdigit() else {value: index for index, value in enumerate("一二三四五六七八九十")}.get(token)
