from copy import deepcopy
from pathlib import Path
import pytest

from src.transcription.canonical import CanonicalSegment, CanonicalTranscript
from src.transcription.pipeline import execute_transcription
from src.transcription.types import ContractValidationError
from tests.transcription_fixture_helpers import FakeAlphaProvider, make_execution_bundle


def canonical():
    i,p,e,s = make_execution_bundle()
    return execute_transcription(FakeAlphaProvider(), i, e, profile_snapshot=s)


def test_canonical_roundtrip_bytes_and_hash_are_deterministic():
    value = canonical()
    rebuilt = CanonicalTranscript.from_json_dict(value.to_json_dict())
    assert rebuilt == value
    assert rebuilt.to_json_bytes() == value.to_json_bytes()
    assert rebuilt.content_sha256 == value.content_sha256


@pytest.mark.parametrize("path", ["root", "profile", "segment", "warning", "artifact"])
def test_unknown_fields_are_rejected_at_every_object_boundary(path):
    data = canonical().to_json_dict()
    if path == "root": data["unknown"] = None
    elif path == "profile": data["profile_snapshot"]["unknown"] = None
    elif path == "segment": data["segments"][0]["unknown"] = None
    elif path == "warning":
        data["warnings"] = [{"code":"segment_overlap","primary_original_position":1,"related_original_positions":[0],"unknown":None}]
    else:
        data["artifact_refs"] = [{"artifact_id":"diag","kind":"provider_diagnostic","content_sha256":"b"*64,"size_bytes":1,"unknown":None}]
    with pytest.raises(ContractValidationError):
        CanonicalTranscript.from_json_dict(data)


@pytest.mark.parametrize("field,value", [("duration_ms", True), ("duration_ms", 1.0), ("duration_ms", -1)])
def test_canonical_timestamp_scalars_are_exact_nonnegative_integers(field, value):
    data = canonical().to_json_dict(); data[field] = value
    with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)


@pytest.mark.parametrize("field,value", [("start_ms", True), ("end_ms", 0), ("start_ms", -1), ("end_ms", 70001)])
def test_segment_range_boundaries(field, value):
    data = canonical().to_json_dict(); data["segments"][0][field] = value
    with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)


def test_canonical_rejects_non_json_native_and_raw_provider_escape_fields():
    data = canonical().to_json_dict(); data["segments"][0]["text"] = Path("private")
    with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)
    for name in ["raw", "chunks", "tokens", "logits", "vad", "debug"]:
        data = canonical().to_json_dict(); data[name] = {}
        with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)


def test_segment_ids_must_be_contiguous_and_time_sorted():
    data = canonical().to_json_dict(); data["segments"][1]["id"] = 3
    with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)
    data = canonical().to_json_dict(); data["segments"].reverse()
    with pytest.raises(ContractValidationError): CanonicalTranscript.from_json_dict(data)
