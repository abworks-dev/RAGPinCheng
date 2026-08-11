"""Application-owned transcription Profile admission configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile


TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV = "TRANSCRIPTION_ADMITTED_PROFILE_IDS"
DEFAULT_TRANSCRIPTION_ADMITTED_PROFILE_IDS = (
    "funasr-sensevoice-zh-experimental-v1",
)
_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATE_SCHEMA = "transcription-admission-env-state/1"
_SETTING_PATTERN = re.compile(
    rf"^\s*(?:export\s+)?{re.escape(TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV)}\s*="
)


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


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.readlines()


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _current_setting(lines: list[str]) -> tuple[int | None, str | None]:
    matches = [index for index, line in enumerate(lines) if _SETTING_PATTERN.match(line)]
    if len(matches) > 1:
        raise ValueError("transcription admission setting is duplicated in the env file")
    if not matches:
        return None, None
    index = matches[0]
    value = lines[index].split("=", 1)[1].strip()
    parse_transcription_admitted_profile_ids(value)
    return index, value


def set_transcription_admission(env_file: Path, state_file: Path, value: str) -> None:
    admitted = parse_transcription_admitted_profile_ids(value)
    canonical_value = ",".join(admitted)
    env_file = env_file.resolve(strict=True)
    state_file = state_file.resolve(strict=False)
    lines = _read_lines(env_file)
    index, previous_value = _current_setting(lines)
    state = {
        "schema_version": STATE_SCHEMA,
        "setting": TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV,
        "previous_present": index is not None,
        "previous_value": previous_value,
    }
    _atomic_write(
        state_file,
        json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n",
        mode=0o600,
    )

    replacement = f"{TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV}={canonical_value}\n"
    if index is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(replacement)
    else:
        newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
        lines[index] = replacement.rstrip("\n") + newline
    mode = stat.S_IMODE(env_file.stat().st_mode)
    _atomic_write(env_file, "".join(lines), mode=mode)


def restore_transcription_admission(env_file: Path, state_file: Path) -> None:
    env_file = env_file.resolve(strict=True)
    state_file = state_file.resolve(strict=True)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if (
        state.get("schema_version") != STATE_SCHEMA
        or state.get("setting") != TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV
        or type(state.get("previous_present")) is not bool
    ):
        raise ValueError("invalid transcription admission state file")
    previous_value = state.get("previous_value")
    if state["previous_present"]:
        if type(previous_value) is not str:
            raise ValueError("invalid previous transcription admission value")
        parse_transcription_admitted_profile_ids(previous_value)
    elif previous_value is not None:
        raise ValueError("unexpected previous transcription admission value")

    lines = _read_lines(env_file)
    index, _current_value = _current_setting(lines)
    if index is not None:
        lines.pop(index)
    if state["previous_present"]:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(
            f"{TRANSCRIPTION_ADMITTED_PROFILE_IDS_ENV}={previous_value}\n"
        )
    mode = stat.S_IMODE(env_file.stat().st_mode)
    _atomic_write(env_file, "".join(lines), mode=mode)
