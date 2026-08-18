import pytest
from dataclasses import replace

from src.transcription.candidate import CandidateSegment
from src.transcription.normalizer import normalize_candidate
from src.transcription.provider_protocol import ProviderCandidate
from src.transcription.types import (
    ContractValidationError,
    NormalizerConfig,
    TerminologyCorrectionConfig,
    TimeUnit,
    TranscriptSegmentationConfig,
    TranscriptWarningCode,
)
from tests.transcription_fixture_helpers import make_execution_bundle, make_profile


def normalize(segments, *, config=None, duration=10_000):
    i,p,e,s=make_execution_bundle(duration_ms=duration)
    if config is not None:
        from tests.transcription_fixture_helpers import make_profile
        from src.transcription.profile import TranscriptionExecutionConfig, ProfileSnapshot
        p = make_profile(normalizer_config=config)
        e=TranscriptionExecutionConfig.create(p,i,language="zh-CN",timeout_ms=5000)
        s=ProfileSnapshot.create(p,e)
    c=ProviderCandidate(e.provider_key,"zh-CN",duration,tuple(segments))
    return normalize_candidate(i,c,s,e)


def seg(pos,start,end,text,unit=TimeUnit.seconds):
    return CandidateSegment(pos,str(start),str(end),unit,text)


def test_decimal_time_uses_half_up_and_supports_milliseconds():
    result=normalize([seg(0,"0.0005","1.0005"," x "), seg(1,"1001","1501","y",TimeUnit.milliseconds)])
    assert [(x.start_ms,x.end_ms,x.text) for x in result.segments] == [(1,1001,"x"),(1001,1501,"y")]


def test_sort_exact_dedup_overlap_empty_and_warning_order_are_deterministic():
    result=normalize([
        seg(2,2,3,"overlap"), seg(0,0,.1,"a"), seg(1,.05,.2,"b"),
        seg(3,2,3,"overlap"), seg(4,4,5," \r\n "),
    ])
    assert [x.id for x in result.segments] == [0,1,2]
    assert [x.text for x in result.segments] == ["a","b","overlap"]
    assert [w.code for w in result.warnings] == [
        TranscriptWarningCode.empty_segment_dropped,
        TranscriptWarningCode.duplicate_segment_dropped,
        TranscriptWarningCode.segment_overlap,
    ]
    assert normalize_candidate


def test_adjacent_segments_are_not_overlap():
    result=normalize([seg(0,0,1,"a"),seg(1,1,2,"b")])
    assert TranscriptWarningCode.segment_overlap not in {w.code for w in result.warnings}


def test_all_blank_segments_fail_closed():
    with pytest.raises(ContractValidationError): normalize([seg(0,0,1," \t\n")])


def test_single_pass_merge_and_split_emit_owned_warnings():
    merged=normalize([seg(0,0,.1,"a"),seg(1,.11,1,"long")], config=NormalizerConfig(2,100,50))
    assert len(merged.segments)==1
    assert any(w.code is TranscriptWarningCode.short_segment_merged for w in merged.warnings)
    split=normalize([seg(0,0,9,"第一句。第二句。第三句。")], config=NormalizerConfig(0,4,0))
    assert len(split.segments)>1
    assert any(w.code is TranscriptWarningCode.long_segment_split for w in split.warnings)
    assert all(x.end_ms>x.start_ms for x in split.segments)


def test_candidate_duration_and_identity_cannot_override_input_or_snapshot():
    i,p,e,s=make_execution_bundle(duration_ms=10000)
    with pytest.raises(ContractValidationError):
        normalize_candidate(i,ProviderCandidate("fake-alpha","zh-CN",9999,(seg(0,0,1,"x"),)),s,e)


def normalize_engineering(text, *, preset="balanced", maximum_ms=30_000, maximum_chars=240, duration=60_000):
    profile = make_profile(
        normalizer_config=NormalizerConfig(0, 1000, 1000),
        segmentation_config=TranscriptSegmentationConfig(
            preset, maximum_ms, maximum_chars, 750
        ),
        terminology_config=TerminologyCorrectionConfig("bim-engineering-v1"),
    )
    input_ref, _profile, execution, snapshot = make_execution_bundle(
        duration_ms=duration,
        profile=profile,
    )
    candidate = ProviderCandidate(
        execution.provider_key,
        "zh-CN",
        duration,
        (seg(0, 0, duration / 1000, text),),
    )
    return normalize_candidate(input_ref, candidate, snapshot, execution)


def test_engineering_terminology_correction_is_deterministic_and_bounded():
    result = normalize_engineering(
        "auto CAD、B I M、reVIT、NAVISWORKS、BIM 2026 0805、GB 50016 2014、12 . 5、95 %",
        maximum_ms=None,
        maximum_chars=500,
    )
    assert result.segments[0].text == (
        "AutoCAD、BIM、Revit、Navisworks、BIM-2026-0805、GB 50016-2014、12.5、95%"
    )
    assert TranscriptWarningCode.terminology_corrected in {
        item.code for item in result.warnings
    }


def test_engineering_terminology_negative_samples_are_not_rewritten():
    text = "自动 CAD 图层，BIMMER，RevitAPI，navisworks2，版本 208，完成率95%，普通数字 12.5。"
    result = normalize_engineering(text, maximum_ms=None, maximum_chars=500)
    assert result.segments[0].text == text
    assert TranscriptWarningCode.terminology_corrected not in {
        item.code for item in result.warnings
    }


@pytest.mark.parametrize(
    ("preset", "maximum_ms", "expected_segments"),
    [
        ("natural", None, 1),
        ("balanced", 30_000, 2),
        ("fine", 15_000, 4),
    ],
)
def test_timestamp_presets_enforce_their_duration_bounds(
    preset, maximum_ms, expected_segments
):
    result = normalize_engineering(
        "模" * 60,
        preset=preset,
        maximum_ms=maximum_ms,
        maximum_chars=500,
    )
    assert len(result.segments) == expected_segments
    if maximum_ms is not None:
        assert all(
            item.end_ms - item.start_ms <= maximum_ms for item in result.segments
        )


def test_fixed_engineering_terms_are_not_split_across_timestamp_segments():
    result = normalize_engineering(
        "前缀 AutoCAD 12.5 208 95% 后缀",
        preset="fine",
        maximum_ms=None,
        maximum_chars=6,
    )
    texts = [item.text for item in result.segments]
    for term in ("AutoCAD", "12.5", "208", "95%"):
        assert any(term in text for text in texts)
