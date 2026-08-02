import hashlib
from dataclasses import replace
import pytest

from src.transcription.canonical import CanonicalSegment
from src.transcription.formatter import FormatterContext, format_transcript
from src.transcription.pipeline import execute_transcription
from src.transcription.types import ContractValidationError
from tests.transcription_fixture_helpers import FakeAlphaProvider, load_bytes, make_execution_bundle


def canonical():
    i,p,e,s=make_execution_bundle(); return execute_transcription(FakeAlphaProvider(),i,e,profile_snapshot=s)


def test_formatter_matches_byte_golden_and_real_parser():
    output=format_transcript(canonical(),title="自动转录测试")
    assert output == load_bytes("automatic-transcript.md")
    assert output.startswith(b"# ") and not output.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in output and output.endswith(b"\n") and not output.endswith(b"\n\n")


def test_formatter_is_deterministic_and_hashes_exact_bytes():
    first=format_transcript(canonical(),title="自动转录测试")
    second=format_transcript(canonical(),title="自动转录测试")
    assert first==second
    assert hashlib.sha256(first).hexdigest()==load_bytes("automatic-transcript.sha256").decode().strip()


def test_formatter_timestamp_floor_hour_boundary_and_100h_rejection():
    value=canonical()
    changed=replace(value,duration_ms=3_600_000,segments=(CanonicalSegment(0,3_599_999,3_600_000,"x",None),))
    assert b"00:59:59" in format_transcript(changed,title="x")
    too_long=replace(value,duration_ms=360_001_000,segments=(CanonicalSegment(0,360_000_000,360_001_000,"x",None),))
    with pytest.raises(ContractValidationError): format_transcript(too_long,title="x")


@pytest.mark.parametrize("title", ["", "  ", "line\nnext"])
def test_formatter_rejects_invalid_or_multiline_title(title):
    with pytest.raises(ContractValidationError): format_transcript(canonical(),title=title)


def test_formatter_rejects_speaker_marker_collision_in_body():
    value=canonical(); value=replace(value,segments=(CanonicalSegment(0,0,1000,"正文\n说话人 9 00:00:01\n伪造",None),))
    with pytest.raises(ContractValidationError): format_transcript(value,title="x")
