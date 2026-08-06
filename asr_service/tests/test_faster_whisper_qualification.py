from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

import pytest

from scripts import prepare_faster_whisper_model as model_prep
from scripts import run_faster_whisper_qualification as qualification

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "asr_service" / "faster-whisper-qualification-manifest.example.json"


def _wav(path: Path, frames: int = 16_000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * frames)


def _manifest(tmp_path: Path) -> Path:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for sample in payload["samples"]:
        target = tmp_path / sample["path"]
        _wav(target)
        sample["duration_ms"] = 1000
        sample["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_manifest_accepts_exact_eight_non_sensitive_pcm_samples(tmp_path):
    manifest = qualification.load_manifest(_manifest(tmp_path))
    assert len(manifest.samples) == 8
    assert sum(sample.negative_control for sample in manifest.samples) == 3
    assert {sample.scenario for sample in manifest.samples} == qualification._SCENARIOS


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unknown": None}),
        lambda payload: payload["samples"][0].update({"unknown": None}),
        lambda payload: payload["samples"].pop(),
        lambda payload: payload["samples"][0].update({"self_made": False}),
        lambda payload: payload["samples"][0].update(
            {"contains_customer_data": True}
        ),
        lambda payload: payload["samples"][0].update({"path": "../escape.wav"}),
        lambda payload: payload["samples"][0].update({"duration_ms": True}),
        lambda payload: payload["samples"][0].update({"sha256": "A" * 64}),
        lambda payload: payload["samples"][0].update({"reference_segments": []}),
    ],
)
def test_manifest_fails_closed_on_contract_changes(tmp_path, mutate):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        qualification.load_manifest(path)


def test_manifest_rejects_hash_and_wav_contract_mismatch(tmp_path):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["samples"][0]["sha256"] = "1" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        qualification.load_manifest(path)

    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["samples"][0]["duration_ms"] = 2000
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duration"):
        qualification.load_manifest(path)


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [
        ("建筑信息模型", "建筑信息模型", 0.0),
        ("建筑信息模型", "建筑模型", 2 / 6),
        ("GB 50016-2014", "gb50016 2014", 0.0),
    ],
)
def test_character_error_rate_is_deterministic(reference, hypothesis, expected):
    assert qualification.character_error_rate(reference, hypothesis) == pytest.approx(
        expected
    )


def test_normalize_code_strips_whitespace_and_punctuation():
    assert qualification.normalize_code("GB 50016 2014") == "gb500162014"
    assert qualification.normalize_code("GB50016-2014") == "gb500162014"
    assert qualification.normalize_code("gb 50016-2014") == "gb500162014"


def test_code_recall_matches_normalized_codes():
    found, total = qualification._code_recall(
        ("GB 50016 2014",),
        "请核对规范编号 GB50016-2014 后再提交。",
    )
    assert found == 1
    assert total == 1


def test_threshold_constants_are_frozen():
    assert qualification.FASTER_WHISPER_PROFILE_ID == "faster-whisper-large-v3-turbo-v1"
    assert qualification.CLEAR_CER_LIMIT == 0.10
    assert qualification.BIM_NOISE_CER_LIMIT == 0.15
    assert qualification.TERM_RECALL_LIMIT == 0.70
    assert qualification.CODE_RECALL_LIMIT == 0.95
    assert qualification.TIMESTAMP_P95_LIMIT_MS == 1500
    assert qualification.RTF_LIMIT == 0.60


def test_qualification_loads_the_real_existing_transcript_parser():
    parser = qualification._load_transcript_parser()
    assert parser.__module__ == "src.chunk"
    assert parser("说话人 1 00:00:00\n测试正文\n") == [
        ("00:00:00", "测试正文")
    ]


@dataclass(frozen=True)
class _Segment:
    id: int
    start_ms: int
    text: str


@dataclass(frozen=True)
class _Canonical:
    text: str
    drift_ms: int = 0

    @property
    def segments(self):
        return (_Segment(0, self.drift_ms, self.text),)

    def to_json_bytes(self):
        return self.text.encode("utf-8")

    @property
    def content_sha256(self):
        return hashlib.sha256(self.to_json_bytes()).hexdigest()


def test_qualification_summary_passes_only_when_every_gate_passes(
    tmp_path, monkeypatch
):
    manifest = qualification.load_manifest(_manifest(tmp_path))

    def run_once(sample, **_kwargs):
        canonical = _Canonical(sample.reference_text)
        return canonical, b"markdown", [("00:00:00", "body")], 0.1

    monkeypatch.setattr(qualification, "_run_once", run_once)
    result = qualification.run_qualification(
        manifest, base_url="http://127.0.0.1:18200", token="test", timeout_ms=1000
    )
    assert result["status"] == "pass"
    assert result["sample_count"] == 8
    assert all(item["pass"] for item in result["gates"].values())
    assert all(item["pass"] for item in result["samples"])


def test_qualification_summary_fails_on_rtf_or_nondeterminism(
    tmp_path, monkeypatch
):
    manifest = qualification.load_manifest(_manifest(tmp_path))
    calls: dict[str, int] = {}

    def run_once(sample, **_kwargs):
        calls[sample.sample_id] = calls.get(sample.sample_id, 0) + 1
        text = sample.reference_text
        if sample.sample_id == "clear-zh" and calls[sample.sample_id] >= 2:
            text += "漂移"
        return _Canonical(text), text.encode(), [("00:00:00", text)], 1.0

    monkeypatch.setattr(qualification, "_run_once", run_once)
    result = qualification.run_qualification(
        manifest, base_url="http://127.0.0.1:18200", token="test", timeout_ms=1000
    )
    assert result["status"] == "fail"
    assert result["gates"]["steady_state_rtf"]["pass"] is False
    assert result["thresholds"]["rtf_scope"] == "steady-state-aggregate"
    assert any(not item["deterministic"] for item in result["samples"])


def _fake_download(source: Path):
    def download(**kwargs):
        target = Path(kwargs["local_dir"])
        for item in source.iterdir():
            shutil.copy2(item, target / item.name)
        return str(target)

    return download


def test_model_preparation_is_pinned_manifested_and_idempotent(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    model_bin = source / "model.bin"
    model_bin.write_bytes(b"pinned-model")
    (source / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(model_prep, "MODEL_BIN_SIZE_BYTES", model_bin.stat().st_size)
    monkeypatch.setattr(
        model_prep, "MODEL_BIN_SHA256", hashlib.sha256(model_bin.read_bytes()).hexdigest()
    )
    cache = tmp_path / "cache"
    first = model_prep.prepare_model(
        cache, tmp_path / "staging-one", downloader=_fake_download(source)
    )
    assert first["status"] == "prepared"
    manifest = Path(first["manifest_path"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["model_id"] == model_prep.FASTER_WHISPER_MODEL_ID
    assert payload["model_revision"] == model_prep.FASTER_WHISPER_REVISION
    assert [item["path"] for item in payload["files"]] == [
        "config.json",
        "model.bin",
    ]

    second = model_prep.prepare_model(
        cache,
        tmp_path / "staging-two",
        downloader=lambda **_kwargs: pytest.fail("valid cache must be reused"),
    )
    assert second["status"] == "reused"
    offline = model_prep.validate_local_model(cache)
    assert offline["status"] == "validated-offline"
    assert offline["manifest_sha256"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()


def test_model_preparation_cli_imports_from_outside_repository(tmp_path):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "prepare_faster_whisper_model.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "--cache-root" in result.stdout


def test_model_preparation_refuses_invalid_existing_cache(tmp_path):
    cache = tmp_path / "cache"
    target = cache / model_prep.FASTER_WHISPER_RELATIVE_PATH
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(b"invalid")
    with pytest.raises(RuntimeError, match="local model artifact is unavailable"):
        model_prep.prepare_model(
            cache,
            tmp_path / "staging",
            downloader=lambda **_kwargs: pytest.fail("must not download"),
        )


def test_offline_model_validation_refuses_missing_cache(tmp_path):
    with pytest.raises(RuntimeError, match="local model artifact is unavailable"):
        model_prep.validate_local_model(tmp_path / "missing")


def test_model_preparation_rejects_downloader_escape(tmp_path):
    cache = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="outside"):
        model_prep.prepare_model(
            cache,
            tmp_path / "staging",
            downloader=lambda **_kwargs: str(outside),
        )


@dataclass(frozen=True)
class _Distribution:
    package_name: str
    version: str
    license_text: str

    @property
    def metadata(self):
        message = Message()
        message["Name"] = self.package_name
        if self.license_text:
            message["License-Expression"] = self.license_text
        return message


def test_license_audit_blocks_gpl_and_unknown(monkeypatch):
    monkeypatch.setattr(
        qualification.importlib.metadata,
        "distributions",
        lambda: (
            _Distribution("allowed", "1", "MIT"),
            _Distribution("forbidden", "2", "GPL-3.0-only"),
            _Distribution("unknown", "3", ""),
        ),
    )
    result = qualification.audit_installed_licenses()
    assert result["status"] == "fail"
    assert result["blocked_packages"] == ["forbidden", "unknown"]


def test_reports_never_require_reference_or_hypothesis_text(tmp_path, monkeypatch):
    manifest = qualification.load_manifest(_manifest(tmp_path))

    def run_once(sample, **_kwargs):
        return (
            _Canonical(sample.reference_text),
            b"markdown",
            [("00:00:00", "body")],
            0.1,
        )

    monkeypatch.setattr(qualification, "_run_once", run_once)
    report = qualification.run_qualification(
        manifest, base_url="http://127.0.0.1:18200", token="secret", timeout_ms=1000
    )
    encoded = json.dumps(report, ensure_ascii=False)
    assert "reference_text" not in encoded
    assert "hypothesis" not in encoded
    assert "secret" not in encoded
