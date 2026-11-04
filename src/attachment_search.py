"""Pure attachment-search ports and intent helpers (no API or storage imports)."""
from __future__ import annotations

import re
from typing import Protocol


class AttachmentSearch(Protocol):
    def __call__(self, query: str, *, categories: list[str] | None = None, limit: int = 5) -> list[dict]: ...


class AttachmentValidator(Protocol):
    def __call__(self, results: list[dict], *, categories: list[str] | None = None) -> list[dict]: ...


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
