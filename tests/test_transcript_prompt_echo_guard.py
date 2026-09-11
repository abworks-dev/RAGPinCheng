from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.transcript_prompt_echo_guard import (
    PromptEchoPublicationBlocked,
    assert_publishable,
    prompt_echo_evidence,
)

ECHO_TEXT = (
    "请准确识别 Revit、Navisworks、AutoCAD BIM-2026-0805 12.5 208 95% 以下是中文"
)
ECHO_TEXT_GB = (
    "请准确识别 Revit、Navisworks AutoCAD BIM-2026-0805、GB 50016-0805、GB 50016-0805"
)
REAL_TEXT = "结构量的尺寸是按照这个单体图的，定位是按照楼梯图的。"
REAL_WITH_REVIT = "我们这里演示一下 Revit 的材质设置流程。"


def canonical(*texts: str) -> object:
    return SimpleNamespace(
        segments=tuple(SimpleNamespace(text=text) for text in texts)
    )


def test_prompt_echo_evidence_counts_only_echo_segments():
    count, samples = prompt_echo_evidence(
        canonical(REAL_TEXT, ECHO_TEXT, REAL_WITH_REVIT, ECHO_TEXT_GB)
    )
    assert count == 2
    assert len(samples) == 2


def test_publish_is_blocked_for_prompt_echo_versions():
    with pytest.raises(PromptEchoPublicationBlocked) as caught:
        assert_publishable(canonical(REAL_TEXT, ECHO_TEXT))
    assert caught.value.count == 1
    assert "不能发布" in caught.value.message
    assert "重新转录" in caught.value.message


def test_publish_is_allowed_for_clean_and_real_speech_versions():
    assert_publishable(canonical(REAL_TEXT, REAL_WITH_REVIT))
    assert prompt_echo_evidence(canonical(REAL_TEXT))[0] == 0
    # A canonical without segments (manual/legacy) must not block publication.
    assert_publishable(SimpleNamespace(segments=None))
