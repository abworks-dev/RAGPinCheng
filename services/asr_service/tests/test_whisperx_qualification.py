from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

import pytest

from scripts import run_whisperx_qualification as qualification

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "services" / "asr_service" / "qwen3-asr-qualification-manifest.example.json"


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 16_000)


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


@dataclass(frozen=True)
class _Segment:
    id: int
    start_ms: int
    text: str


@dataclass(frozen=True)
class _Canonical:
    text: str

    @property
    def segments(self):
        return (_Segment(0, 0, self.text),)

    def to_json_bytes(self):
        return self.text.encode("utf-8")

    @property
    def content_sha256(self):
        return hashlib.sha256(self.to_json_bytes()).hexdigest()


def test_reuses_strict_self_made_eight_sample_contract(tmp_path):
    manifest = qualification.load_manifest(_manifest(tmp_path))
    assert len(manifest.samples) == 8
    assert sum(sample.negative_control for sample in manifest.samples) == 3
    assert {sample.scenario for sample in manifest.samples} == qualification._SCENARIOS


def test_thresholds_match_existing_asr_qualification_baseline():
    assert qualification.CLEAR_CER_LIMIT == 0.10
    assert qualification.BIM_NOISE_CER_LIMIT == 0.15
    assert qualification.TERM_RECALL_LIMIT == 0.70
    assert qualification.CODE_RECALL_LIMIT == 0.95
    assert qualification.TIMESTAMP_P95_LIMIT_MS == 1500
    assert qualification.RTF_LIMIT == 0.60


def test_shared_metrics_keep_whisperx_report_identity(tmp_path, monkeypatch):
    manifest = qualification.load_manifest(_manifest(tmp_path))

    def run_once(sample, **_kwargs):
        value = _Canonical(sample.reference_text)
        return value, b"markdown", [("00:00:00", "body")], 0.1

    monkeypatch.setattr(qualification, "_run_once", run_once)
    result = qualification.run_qualification(manifest, timeout_ms=1000)
    assert result["status"] == "pass"
    assert result["schema_version"] == qualification.REPORT_SCHEMA_VERSION
    assert result["profile_id"] == qualification.WHISPERX_PROFILE_ID
    assert result["repetitions"] == 2
    assert result["sample_count"] == 8
    assert result["manifest_source"] == "legacy"
    assert result["manifest_sha256"] == manifest.manifest_sha256
    assert result["sample_set_id"] == manifest.sample_set_id
    assert result["annotation_version"] == manifest.annotation_version
    assert result["qualification_corpus"] == manifest.identity()


def test_local_single_repetition_passes_through_shared_evaluator(tmp_path, monkeypatch):
    manifest = qualification.load_manifest(_manifest(tmp_path))

    def run_once(sample, **_kwargs):
        value = _Canonical(sample.reference_text)
        return value, b"markdown", [("00:00:00", "body")], 0.1

    monkeypatch.setattr(qualification, "_run_once", run_once)
    result = qualification.run_qualification(
        manifest, timeout_ms=1000, repetitions=1
    )

    assert result["repetitions"] == 1
    assert all(item["deterministic"] is None for item in result["samples"])


def test_report_contains_no_reference_or_hypothesis_text(tmp_path, monkeypatch):
    manifest = qualification.load_manifest(_manifest(tmp_path))

    def run_once(sample, **_kwargs):
        value = _Canonical(sample.reference_text)
        return value, b"markdown", [("00:00:00", "body")], 0.1

    monkeypatch.setattr(qualification, "_run_once", run_once)
    report = qualification.run_qualification(manifest, timeout_ms=1000)
    encoded = json.dumps(report, ensure_ascii=False)
    assert "reference_text" not in encoded
    assert "hypothesis" not in encoded


def test_candidate_matrix_selects_only_improving_full_decode(monkeypatch):
    reports = {
        "baseline": {
            "status": "fail",
            "sample_count": 8,
            "gates": {
                "standard_code_recall": {"observed": 0.0},
                "negative_false_positives": {"observed": 0},
            },
            "samples": [{"scenario": "noisy-bim-zh", "cer": 0.25}],
        },
        "hotwords": {
            "status": "fail",
            "sample_count": 8,
            "gates": {
                "standard_code_recall": {"observed": 0.5},
                "negative_false_positives": {"observed": 0},
            },
            "samples": [{"scenario": "noisy-bim-zh", "cer": 0.2}],
        },
        "full-decode": {
            "status": "pass",
            "sample_count": 8,
            "gates": {
                "standard_code_recall": {"observed": 1.0},
                "negative_false_positives": {"observed": 0},
            },
            "samples": [{"scenario": "noisy-bim-zh", "cer": 0.1}],
        },
    }

    def run_qualification(_manifest, *, timeout_ms, service_config):
        del timeout_ms
        if not service_config.hotwords:
            candidate = "baseline"
        elif service_config.beam_size != 1 or service_config.temperature != 0.0:
            candidate = "full-decode"
        else:
            candidate = "hotwords"
        return json.loads(json.dumps(reports[candidate]))

    monkeypatch.setattr(qualification, "run_qualification", run_qualification)
    result = qualification.run_candidate_matrix(object(), timeout_ms=1000)

    assert result["status"] == "pass"
    assert result["selected_candidate"] == "full-decode"
    assert result["candidate_order"] == ["baseline", "hotwords", "full-decode"]
    assert all(result["selection"].values())


def test_candidate_matrix_rejects_non_improving_or_false_positive_full_decode(
    monkeypatch,
):
    def run_qualification(_manifest, *, timeout_ms, service_config):
        del timeout_ms
        full = service_config.beam_size != 1 or service_config.temperature != 0.0
        return {
            "status": "pass",
            "sample_count": 8,
            "gates": {
                "standard_code_recall": {"observed": 1.0},
                "negative_false_positives": {"observed": 1 if full else 0},
            },
            "samples": [{"scenario": "noisy-bim-zh", "cer": 0.1}],
        }

    monkeypatch.setattr(qualification, "run_qualification", run_qualification)
    result = qualification.run_candidate_matrix(object(), timeout_ms=1000)

    assert result["status"] == "fail"
    assert result["selected_candidate"] is None
    assert result["selection"] == {
        "full_candidate_passed": True,
        "standard_code_recall_improved": False,
        "noisy_bim_cer_improved": False,
        "negative_false_positives_zero": False,
    }


def test_diagnostic_evidence_is_text_free_and_classifies_model_miss(tmp_path):
    manifest = qualification.load_manifest(_manifest(tmp_path))
    sample = next(
        item for item in manifest.samples if item.sample_id == "standard-codes"
    )
    evidence = qualification._diagnostic_evidence(
        sample,
        "请核对规范编号鸡比五万零一十六二零一四",
        "请核对规范编号鸡比五万零一十六二零一四",
    )
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert evidence["classification"] == "acoustic_model_miss"
    assert evidence["raw_to_canonical_equal"] is True
    assert evidence["expected_items"][0]["present_in_raw"] is False
    assert sample.reference_text not in encoded
    assert "GB 50016 2014" not in encoded
    assert "鸡比" not in encoded


def test_diagnostic_evidence_classifies_normalizer_loss(tmp_path):
    manifest = qualification.load_manifest(_manifest(tmp_path))
    sample = next(
        item for item in manifest.samples if item.sample_id == "standard-codes"
    )
    evidence = qualification._diagnostic_evidence(
        sample,
        sample.reference_text,
        "请核对规范编号",
    )
    assert evidence["classification"] == "normalizer_loss"
    assert evidence["expected_items"][0]["present_in_raw"] is True
    assert evidence["expected_items"][0]["present_in_canonical"] is False


def test_diagnostic_report_requires_both_target_samples():
    qualification._DIAGNOSTIC_OBSERVATIONS.clear()
    qualification._DIAGNOSTIC_OBSERVATIONS["standard-codes"] = [
        {"classification": "acoustic_model_miss"}
    ]
    report = qualification.build_diagnostic_report()
    assert report["schema_version"] == "whisperx-failure-diagnostic/1"
    assert report["status"] == "incomplete"
    assert report["contains_transcript_text"] is False


def test_edit_counts_are_structured_without_text():
    assert qualification._edit_counts("甲乙丙", "甲丁丙") == {
        "distance": 1,
        "substitutions": 1,
        "insertions": 0,
        "deletions": 0,
    }
    assert qualification._edit_counts("甲乙", "甲乙丙")["insertions"] == 1
    assert qualification._edit_counts("甲乙丙", "甲乙")["deletions"] == 1


def test_manifest_contract_remains_fail_closed(tmp_path):
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["samples"][0]["contains_customer_data"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        qualification.load_manifest(path)


def test_license_audit_keeps_whisperx_identity_and_fails_closed(monkeypatch):
    class Distribution:
        version = "1"

        @property
        def metadata(self):
            value = Message()
            value["Name"] = "unknown-package"
            return value

    monkeypatch.setattr(
        qualification.shared.importlib.metadata,
        "distributions",
        lambda: (Distribution(),),
    )
    result = qualification.audit_installed_licenses()
    assert result["schema_version"] == "whisperx-license-audit/1"
    assert result["status"] == "fail"
    assert result["blocked_packages"] == ["unknown-package"]


def test_license_audit_accepts_exact_bundled_mit_or_bsd_text(tmp_path, monkeypatch):
    class Distribution:
        version = "1"

        def __init__(self, name, first_line):
            self.name = name
            self.path = tmp_path / name
            self.path.write_text(first_line + "\nCopyright holder\n", encoding="utf-8")
            self.files = (f"{name}-1.dist-info/licenses/LICENSE",)

        @property
        def metadata(self):
            value = Message()
            value["Name"] = self.name
            return value

        def locate_file(self, _item):
            return self.path

    values = (
        Distribution("mit-package", "MIT License"),
        Distribution("bsd-package", "BSD 3-Clause License"),
    )
    monkeypatch.setattr(
        qualification.shared.importlib.metadata,
        "distributions",
        lambda: values,
    )
    result = qualification.audit_installed_licenses()
    assert result["status"] == "pass"
    assert result["blocked_packages"] == []
    assert all(item["status"] == "allowed" for item in result["packages"])


def test_workflow_and_runner_are_manual_isolated_and_disabled():
    workflow = (
        ROOT / ".github/workflows/qualify-whisperx-production.yml"
    ).read_text(encoding="utf-8")
    diagnostic_workflow = (
        ROOT / ".github/workflows/diagnose-whisperx-production.yml"
    ).read_text(encoding="utf-8")
    script = (ROOT / "scripts/qualify-whisperx-production.ps1").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "enable exactly one qualification execution mode" in workflow
    assert "runtime_preflight:" in workflow
    assert "if: ${{ inputs.runtime_preflight }}" in workflow
    assert "run_whisperx_runtime_preflight.py" in workflow
    assert "python311_path_missing" in workflow
    assert "environment: production-asr" in workflow
    assert "prepare-qwen3-asr-qualification-samples.ps1" not in workflow
    assert "PRODUCTION_ASR_QUALIFICATION_ROOT" in workflow
    assert "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH" in workflow
    assert "PRODUCTION_QWEN3_ASR_MANIFEST_PATH" not in workflow
    assert "torch==2.8.0+cu128" in script
    assert "whisperx==3.8.6" in script
    assert "python-dotenv>=1.0.0" in script
    assert "--audit-licenses" in script
    assert "installed dependency license audit failed" in script
    assert "Profile is not disabled" in script
    assert "Get-ScheduledTask" in script
    assert "Get-NetFirewallRule" in script
    lowered = script.lower()
    for forbidden in (
        "new-service",
        "start-service",
        "register-scheduledtask",
        "new-netfirewallrule",
    ):
        assert forbidden not in lowered
    assert "${{ runner.temp }}\\whisperx-qualification-" not in workflow
    assert "PRODUCTION_WHISPERX_ROOT" in workflow
    assert "PRODUCTION_WHISPERX_MODEL_ROOT" not in workflow
    assert "PRODUCTION_WHISPERX_NLTK_ROOT" not in workflow
    assert "PRODUCTION_WHISPERX_QUALIFICATION_ROOT" not in workflow
    assert "PRODUCTION_WHISPERX_WHEEL_CACHE_ROOT" not in workflow
    assert "PRODUCTION_WHISPERX_REPORT_ROOT" not in workflow
    assert (
        'Join-Path $env:PRODUCTION_WHISPERX_ROOT "reports\\runs\\${{ github.run_id }}\\reports\\verdict.json"'
    ) in workflow
    assert "execute_diagnostic must be explicitly enabled" in diagnostic_workflow
    assert "environment: production-asr" in diagnostic_workflow
    assert "prepare-qwen3-asr-qualification-samples.ps1" not in diagnostic_workflow
    assert "PRODUCTION_ASR_QUALIFICATION_ROOT" in diagnostic_workflow
    assert "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH" in diagnostic_workflow
    assert "PRODUCTION_QWEN3_ASR_MANIFEST_PATH" not in diagnostic_workflow
    assert "PRODUCTION_WHISPERX_ROOT" in diagnostic_workflow
    assert "PRODUCTION_WHISPERX_MODEL_ROOT" not in diagnostic_workflow
    assert "-DiagnosticMode" in diagnostic_workflow
    assert "contains_transcript_text" in diagnostic_workflow
    assert "failure-diagnostic.json" in diagnostic_workflow
    assert "DiagnosticMode" in script
    assert "$diagnosticComplete" in script
    assert 'status -eq "complete"' in script
    assert "selected_candidate = $SelectedCandidate" in script
    assert "standard_code_recall_improved = [bool]" in script
    assert "noisy_bim_cer_improved = [bool]" in script
    assert "- Selected candidate:" in workflow
