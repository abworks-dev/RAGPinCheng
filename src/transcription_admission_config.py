"""Application-owned transcription Profile admission configuration."""
from __future__ import annotations

import re


TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV = "TRANSCRIPTION_ADMITTED_PROFILE_IDS"
DEFAULT_TRANSCRIPTION_ADMITTED_PROFILE_IDS = (
    "funasr-sensevoice-zh-experimental-v1",
)
_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_transcription_admitted_profile_ids(value: str) -> tuple[str, ...]:
    if type(value) is not str:
        raise ValueError("transcription admission allowlist must be a string")
    if value == "":
        return ()

    profile_ids = tuple(item.strip() for item in value.split(","))
    if any(
        not item or _PROFILE_ID_PATTERN.fullmatch(item) is None
        for item in profile_ids
    ):
        raise ValueError("transcription admission allowlist contains an invalid profile id")
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("transcription admission allowlist contains duplicate profile ids")
    return profile_ids


def default_transcription_admission_value() -> str:
    return ",".join(DEFAULT_TRANSCRIPTION_ADMITTED_PROFILE_IDS)
