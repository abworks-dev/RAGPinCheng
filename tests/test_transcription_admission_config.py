from __future__ import annotations

import pytest

from src.transcription_admission_config import (
    default_transcription_admission_value,
    parse_transcription_admitted_profile_ids,
)


def test_default_admission_preserves_sensevoice_only():
    assert parse_transcription_admitted_profile_ids(
        default_transcription_admission_value()
    ) == ("funasr-sensevoice-zh-experimental-v1",)


@pytest.mark.parametrize(
    "value",
    (
        "funasr-sensevoice-zh-experimental-v1,,faster-whisper-zh-experimental-v1",
        "funasr-sensevoice-zh-experimental-v1,funasr-sensevoice-zh-experimental-v1",
        "unknown_profile",
        ",",
    ),
)
def test_admission_parser_rejects_malformed_or_duplicate_values(value):
    with pytest.raises(ValueError):
        parse_transcription_admitted_profile_ids(value)
