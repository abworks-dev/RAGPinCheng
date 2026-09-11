"""Publication guard against transcripts that still echo the ASR prompt.

Legacy automatic versions produced before the prompt-echo fix can contain spans
such as "请准确识别 Revit、Navisworks、AutoCAD BIM-2026-0805 12.5 208 95% 以下是中文",
which is the decoder echoing the ASR prompt asset rather than real speech. They
must never reach the published transcript head or the index.

The echo vocabulary has exactly one owner, ``src.transcription.normalizer``; this
guard only consults that rule and refuses publication, so the definition of
"prompt echo" cannot drift into a second copy.
"""
from __future__ import annotations

from typing import Iterable

from src.transcription.normalizer import _prompt_echo_offset

MAX_SAMPLES = 3
SAMPLE_CHARS = 60


class PromptEchoPublicationBlocked(Exception):
    """Raised when a version contains prompt-echo spans and must not publish."""

    def __init__(self, count: int, samples: Iterable[str]) -> None:
        self.count = count
        self.samples = tuple(samples)
        super().__init__(self.message)

    @property
    def message(self) -> str:
        sample_text = "；".join(f"“{item}”" for item in self.samples)
        detail = f"（示例：{sample_text}）" if sample_text else ""
        return (
            f"该转录版本含 {self.count} 段疑似 ASR 提示词回吐内容，不能发布{detail}。"
            "请重新转录生成新版本，并改用新版本审核发布。"
        )


def _segment_texts(canonical: object) -> tuple[str, ...]:
    segments = getattr(canonical, "segments", None)
    if not segments:
        return ()
    texts: list[str] = []
    for segment in segments:
        text = getattr(segment, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return tuple(texts)


def prompt_echo_evidence(canonical: object) -> tuple[int, tuple[str, ...]]:
    """Return the echo-segment count and bounded samples for a canonical transcript."""
    samples: list[str] = []
    count = 0
    for text in _segment_texts(canonical):
        if _prompt_echo_offset(text) is None:
            continue
        count += 1
        if len(samples) < MAX_SAMPLES:
            samples.append(text[:SAMPLE_CHARS])
    return count, tuple(samples)


def assert_publishable(canonical: object) -> None:
    """Raise :class:`PromptEchoPublicationBlocked` when the version must not publish."""
    count, samples = prompt_echo_evidence(canonical)
    if count:
        raise PromptEchoPublicationBlocked(count, samples)
