from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from scripts import asr_qualification_manifest as contract
from scripts import run_faster_whisper_qualification as faster
from scripts import run_qwen3_asr_qualification as qwen
from scripts import run_whisperx_qualification as whisperx

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "services" / "asr_service" / "asr-qualification-manifest.example.json"


def _wav(
    path: Path,
    *,
    frames: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
    sample_rate: int = 16_000,
) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00" * frames * channels * sample_width)


def _manifest(root: Path, *, annotation_version: str = "1") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload["annotation_version"] = annotation_version
    for sample in payload["samples"]:
        target = root / sample["path"]
        _wav(target)
        sample["size_bytes"] = target.stat().st_size
        sample["duration_ms"] = 1000
        sample["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    path = root / "manifest.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        item.relative_to(root).as_posix(): (
            item.stat().st_size,
            item.stat().st_mtime_ns,
            hashlib.sha256(item.read_bytes()).hexdigest(),
        )
        for item in sorted(root.iterdir())
        if item.is_file()
    }


def _rewrite(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_shared_manifest_round_trips_and_is_read_only(tmp_path):
    path = _manifest(tmp_path)
    before = _snapshot(tmp_path)
    manifest = contract.load_manifest(
        path,
        root=tmp_path,
        allowed_schema_versions=contract.allowed_schema_versions(
            "neutral", "faster-whisper"
        ),
        manifest_source="neutral",
    )

    assert _snapshot(tmp_path) == before
    assert manifest.schema_version == contract.SHARED_SCHEMA_VERSION
    assert manifest.manifest_source == "neutral"
    assert manifest.sample_set_id == "self-made-faster-whisper-r3"
    assert manifest.annotation_version == "1"
    assert len(manifest.samples) == 8
    assert sum(sample.negative_control for sample in manifest.samples) == 3
    assert json.loads(json.dumps(manifest.identity())) == manifest.identity()
    assert manifest.identity()["manifest_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": None}),
        lambda value: value["source"].update({"unknown": None}),
        lambda value: value["source"].update({"self_made": False}),
        lambda value: value["source"].update({"is_internal_recording": True}),
        lambda value: value["source"].update({"contains_customer_data": True}),
        lambda value: value["samples"][0].update({"unknown": None}),
        lambda value: value["samples"].pop(),
        lambda value: value["samples"][0].update({"path": "../escape.wav"}),
        lambda value: value["samples"][0].update({"path": "C:/escape.wav"}),
        lambda value: value["samples"][0].update({"path": "nested\\escape.wav"}),
        lambda value: value["samples"][0].update({"size_bytes": True}),
        lambda value: value["samples"][0].update({"size_bytes": 1}),
        lambda value: value["samples"][0].update({"sha256": "A" * 64}),
        lambda value: value["samples"][0].update({"duration_ms": 1001}),
        lambda value: value["samples"][0].update({"reference_segments": []}),
    ],
)
def test_shared_manifest_fails_closed_on_contract_changes(tmp_path, mutate):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    _rewrite(path, payload)
    with pytest.raises((ValueError, FileNotFoundError)):
        contract.load_manifest(
            path,
            root=tmp_path,
            allowed_schema_versions=contract.allowed_schema_versions(
                "neutral", "faster-whisper"
            ),
            manifest_source="neutral",
        )


def test_shared_manifest_rejects_duplicate_json_keys(tmp_path):
    path = _manifest(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            '"schema_version": "asr-qualification-corpus/1",',
            '"schema_version": "asr-qualification-corpus/1",\n'
            '  "schema_version": "asr-qualification-corpus/1",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        contract.load_manifest(path, root=tmp_path)


@pytest.mark.parametrize(
    ("channels", "sample_width", "sample_rate"),
    [(2, 2, 16_000), (1, 1, 16_000), (1, 2, 8_000)],
)
def test_shared_manifest_rejects_wrong_wav_format(
    tmp_path, channels, sample_width, sample_rate
):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    sample = payload["samples"][0]
    target = tmp_path / sample["path"]
    _wav(
        target,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        frames=sample_rate,
    )
    sample["size_bytes"] = target.stat().st_size
    sample["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    _rewrite(path, payload)
    with pytest.raises(ValueError, match="16 kHz mono PCM16"):
        contract.load_manifest(path, root=tmp_path)


def test_shared_manifest_rejects_reparse_component(tmp_path, monkeypatch):
    path = _manifest(tmp_path)
    original = contract._is_reparse_point

    def is_reparse(candidate: Path) -> bool:
        return candidate.name == "clear-zh.wav" or original(candidate)

    monkeypatch.setattr(contract, "_is_reparse_point", is_reparse)
    with pytest.raises(ValueError, match="reparse point"):
        contract.load_manifest(path, root=tmp_path)


@pytest.mark.parametrize(
    ("engine", "legacy_name"),
    [
        ("faster-whisper", "PRODUCTION_FASTER_WHISPER_INPUT_ROOT"),
        ("qwen3-asr", "PRODUCTION_QWEN3_ASR_INPUT_ROOT"),
    ],
)
def test_neutral_manifest_ignores_retired_legacy_root_keys(
    tmp_path, engine, legacy_name
):
    path = _manifest(tmp_path / "shared")
    environ = {
        "PRODUCTION_ASR_QUALIFICATION_ROOT": str(path.parent),
        "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH": str(path),
        legacy_name: str(tmp_path / "retired"),
    }
    selection = contract.resolve_manifest_from_environment(engine, environ)
    assert selection.source == "neutral"
    assert selection.manifest.manifest_sha256 == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def test_whisperx_neutral_manifest_ignores_retired_direct_path(tmp_path):
    path = _manifest(tmp_path / "shared")
    selection = contract.resolve_manifest_from_environment(
        "whisperx",
        {
            "PRODUCTION_ASR_QUALIFICATION_ROOT": str(path.parent),
            "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH": str(path),
            "PRODUCTION_QWEN3_ASR_MANIFEST_PATH": str(tmp_path / "retired.json"),
        },
    )
    assert selection.source == "neutral"


def test_legacy_only_environment_is_rejected(tmp_path):
    path = _manifest(tmp_path / "legacy")
    with pytest.raises(ValueError, match="neutral ASR qualification manifest"):
        contract.resolve_manifest_from_environment(
            "qwen3-asr", {"PRODUCTION_QWEN3_ASR_INPUT_ROOT": str(path.parent)}
        )


def test_retired_legacy_identity_cannot_override_neutral_manifest(tmp_path):
    neutral = _manifest(tmp_path / "neutral")
    legacy = _manifest(tmp_path / "legacy", annotation_version="2")
    selection = contract.resolve_manifest_from_environment(
        "faster-whisper",
        {
            "PRODUCTION_ASR_QUALIFICATION_ROOT": str(neutral.parent),
            "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH": str(neutral),
            "PRODUCTION_FASTER_WHISPER_INPUT_ROOT": str(legacy.parent),
        },
    )
    assert selection.source == "neutral"
    assert selection.manifest.annotation_version == "1"


def test_partial_neutral_configuration_fails_even_with_retired_legacy_key(tmp_path):
    legacy = _manifest(tmp_path / "legacy")
    with pytest.raises(ValueError, match="configured together"):
        contract.resolve_manifest_from_environment(
            "faster-whisper",
            {
                "PRODUCTION_ASR_QUALIFICATION_ROOT": str(tmp_path / "neutral"),
                "PRODUCTION_FASTER_WHISPER_INPUT_ROOT": str(legacy.parent),
            },
        )


def test_three_engines_load_the_same_neutral_manifest_identity(tmp_path):
    path = _manifest(tmp_path)
    manifests = [
        module.load_manifest(path, root=tmp_path, manifest_source="neutral")
        for module in (faster, qwen, whisperx)
    ]
    identities = [manifest.identity() for manifest in manifests]
    assert identities[0] == identities[1] == identities[2]
    assert {manifest.manifest_source for manifest in manifests} == {"neutral"}
