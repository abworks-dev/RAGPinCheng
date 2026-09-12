"""第 6 轮：whisperx 去热词生产配置与 qualification 选择门禁的回归测试。

背景（2026-09-13 生产实测，job 459010b5）：热词偏置把个别 30 秒窗口的解码带进编号复读，
整段真实讲解被吞掉（720.0-750.0s 输出 9 次「建筑抗震设计规范 GB 40011-2014」、
779.3-802.1s 输出 5 段「楼层平台」、480.1-539.7s 输出 11-12 次「建筑设计规范 GB 50011-2010」，
两次独立任务逐字节一致）。同一音频在活动 release 内重放：去热词后恢复 12 段/108 字与
7 段/84 字真实讲解。用户批准改为全局去热词，并要求绝对门禁继续成立。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.transcription.service_profiles import (  # noqa: E402
    FASTER_WHISPER_SERVICE_CONFIG,
    WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG,
    WHISPERX_V2_HOTWORDS_SERVICE_CONFIG,
    WHISPERX_V2_SERVICE_CONFIG,
)
from src.transcription.terminology import correct_terminology  # noqa: E402
from src.transcription.types import TerminologyCorrectionConfig  # noqa: E402


def test_production_whisperx_profile_decodes_without_hotwords():
    production = WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG

    assert production.service_profile_id == "whisperx-large-v3-zh-align-v2"
    assert production.hotwords == ()
    # 去热词是唯一变量：beam/temperature 与上一版生产一致。
    assert production.beam_size == 10
    assert production.temperature == 0.0
    # faster-whisper 候选的实验配置不受影响，仍带自己的热词。
    assert FASTER_WHISPER_SERVICE_CONFIG.hotwords != ()


def test_legacy_hotword_candidate_is_kept_as_the_regression_reference():
    legacy = WHISPERX_V2_HOTWORDS_SERVICE_CONFIG
    baseline = WHISPERX_V2_SERVICE_CONFIG

    assert legacy.hotwords != ()
    assert legacy.hotwords == tuple(
        word
        for word in FASTER_WHISPER_SERVICE_CONFIG.hotwords
        if word != "规范编号"
    )
    assert baseline.hotwords == ()
    # 三个候选必须共用同一 profile id，否则 selection 比较无意义。
    assert {
        legacy.service_profile_id,
        baseline.service_profile_id,
        WHISPERX_V2_FULL_DECODE_SERVICE_CONFIG.service_profile_id,
    } == {"whisperx-large-v3-zh-align-v2"}


def _gate_report(*, status: str, code_recall: float, false_positives: int = 0, coverage: bool = True, noisy_cer: float) -> dict:
    return {
        "status": status,
        "gates": {
            "standard_code_recall": {"observed": code_recall},
            "negative_false_positives": {"observed": false_positives},
            "content_coverage": {"pass": coverage},
        },
        "samples": [{"scenario": "noisy-bim-zh", "cer": noisy_cer}],
    }


def _evaluate_selection(production: dict, legacy: dict, baseline: dict) -> dict:
    source = (ROOT / "scripts" / "run_whisperx_qualification.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    namespace: dict[str, object] = {}
    wanted = {"evaluate_selection", "_scenario_metric"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            module = ast.Module(body=[node], type_ignores=[])
            exec(compile(module, "<selection>", "exec"), namespace)
    return namespace["evaluate_selection"](production, legacy, baseline)  # type: ignore[operator]


def test_selection_accepts_a_hotword_free_candidate_that_matches_the_hotword_reference():
    production = _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10)
    legacy = _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10)
    baseline = _gate_report(status="pass", code_recall=0.0, noisy_cer=0.30)

    decision = _evaluate_selection(production, legacy, baseline)

    assert decision["selected_candidate"] == "full-decode"
    assert all(decision["selection"].values())
    assert decision["legacy_reference_candidate"] == "hotwords"


@pytest.mark.parametrize(
    ("production", "legacy", "failed_key"),
    [
        (
            _gate_report(status="fail", code_recall=1.0, noisy_cer=0.10),
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10),
            "production_candidate_passed",
        ),
        (
            _gate_report(status="pass", code_recall=0.90, noisy_cer=0.10),
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10),
            "standard_code_recall_not_worse_than_legacy_hotwords",
        ),
        (
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.20),
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10),
            "noisy_bim_cer_not_worse_than_legacy_hotwords",
        ),
        (
            _gate_report(status="pass", code_recall=1.0, false_positives=1, noisy_cer=0.10),
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10),
            "negative_false_positives_zero",
        ),
        (
            _gate_report(status="pass", code_recall=1.0, coverage=False, noisy_cer=0.10),
            _gate_report(status="pass", code_recall=1.0, noisy_cer=0.10),
            "content_coverage_passed",
        ),
    ],
)
def test_selection_rejects_any_regression_against_the_hotword_reference(production, legacy, failed_key):
    baseline = _gate_report(status="pass", code_recall=0.0, noisy_cer=0.30)

    decision = _evaluate_selection(production, legacy, baseline)

    assert decision["selected_candidate"] is None
    assert decision["selection"][failed_key] is False
    assert any(value is False for value in decision["selection"].values())


def test_selection_evidence_records_both_sides_of_the_comparison():
    production = _gate_report(status="pass", code_recall=1.0, noisy_cer=0.08)
    legacy = _gate_report(status="pass", code_recall=1.0, noisy_cer=0.12)
    baseline = _gate_report(status="pass", code_recall=0.0, noisy_cer=0.30)

    decision = _evaluate_selection(production, legacy, baseline)

    assert decision["production_code_recall"] == 1.0
    assert decision["legacy_code_recall"] == 1.0
    assert decision["production_noisy_bim_cer"] == 0.08
    assert decision["legacy_noisy_bim_cer"] == 0.12
    assert decision["baseline_code_recall"] == 0.0


@pytest.mark.parametrize(
    ("hypothesis", "expected"),
    [
        ("然后我们平台的一个静高的话", "然后我们平台的一个净高的话"),
        ("梯段的这个梯段径高", "梯段的这个梯段净高"),
        ("梯断净够不应小于2米2", "梯段净高不应小于2米2"),
        ("如果图纸上写了构建碰撞", "如果图纸上写了构件碰撞"),
        ("我们就在模型里做一次碰壮检查", "我们就在模型里做一次碰撞检查"),
        ("梯梁和梯柱都属于钢结构构建", "梯梁和梯柱都属于钢结构构件"),
        ("焊缝和螺栓的标柱也要一起核对", "焊缝和螺栓的标注也要一起核对"),
        ("找到楼梯的铺面视图", "找到楼梯的剖面视图"),
    ],
)
def test_hotword_free_misrecognitions_are_corrected(hypothesis: str, expected: str):
    corrected, _changed = correct_terminology(
        hypothesis, TerminologyCorrectionConfig("bim-engineering-v1")
    )

    assert corrected == expected


def test_legitimate_paving_and_tall_wording_is_not_rewritten():
    config = TerminologyCorrectionConfig("bim-engineering-v1")

    # 「铺面」只在视图/图纸语境下才是剖面误识；「铺面做法」是合法工程用语。
    assert correct_terminology("楼梯铺面做法见详图", config)[0] == "楼梯铺面做法见详图"
    # 「挺高」是合法口语，不做替换。
    assert correct_terminology("这个梯梁挺高的", config)[0] == "这个梯梁挺高的"
