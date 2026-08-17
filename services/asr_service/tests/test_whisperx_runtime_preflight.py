from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import run_whisperx_runtime_preflight as preflight


def test_missing_runtime_configuration_is_reported_without_paths(tmp_path, monkeypatch):
    report = tmp_path / "preflight.json"
    monkeypatch.delenv("PRODUCTION_ASR_QUALIFICATION_ROOT", raising=False)
    monkeypatch.delenv("PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["preflight", "--report", str(report)])

    assert preflight.main() == 2
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result == {
        "failure_code": "shared-corpus-unconfigured",
        "failure_stage": "shared-corpus",
        "production_services_modified": False,
        "schema_version": "whisperx-runtime-preflight/1",
        "status": "fail",
    }
    assert str(tmp_path) not in report.read_text(encoding="utf-8")


def test_missing_shared_corpus_path_is_reported_without_paths(tmp_path, monkeypatch):
    report = tmp_path / "preflight.json"
    missing_root = tmp_path / "missing-corpus"
    monkeypatch.setenv("PRODUCTION_ASR_QUALIFICATION_ROOT", str(missing_root))
    monkeypatch.setenv(
        "PRODUCTION_ASR_QUALIFICATION_MANIFEST_PATH", str(missing_root / "manifest.json")
    )
    monkeypatch.setattr(sys, "argv", ["preflight", "--report", str(report)])

    assert preflight.main() == 2
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["failure_code"] == "shared-corpus-unavailable"
    assert result["failure_stage"] == "shared-corpus"
    assert str(tmp_path) not in report.read_text(encoding="utf-8")


def test_unexpected_import_failure_keeps_exception_details_out_of_report():
    result = preflight._failure_result(
        preflight.PreflightStageError("imports", ModuleNotFoundError("private.module"))
    )

    assert result == {
        "failure_code": "runtime-preflight-failed",
        "failure_stage": "imports",
        "production_services_modified": False,
        "schema_version": "whisperx-runtime-preflight/1",
        "status": "fail",
    }
    assert "private.module" not in str(result)


def test_profile_admission_is_verified_without_importing_transcription_package(
    tmp_path, monkeypatch
):
    catalog = tmp_path / "profile_catalog.py"
    catalog.write_text(
        "WHISPERX_BALANCED_PROFILE_ID = 'whisperx-large-v3-zh-balanced-v2'\n"
        "TranscriptionProfileDefinition.create(\n"
        "    profile_id=WHISPERX_BALANCED_PROFILE_ID,\n"
        "    admission=ProfileAdmission.disabled,\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "PROFILE_CATALOG_PATH", catalog)

    assert preflight._profile_admission() == "disabled"


def test_whisperx_root_derives_all_isolated_directories(tmp_path, monkeypatch):
    root = tmp_path / "whisperx"
    root.mkdir()
    expected = tuple(root / child for child in preflight.WHISPERX_ROOT_CHILDREN)
    for path in expected:
        path.mkdir()
    monkeypatch.setenv(preflight.WHISPERX_ROOT_ENV, str(root))

    assert preflight._whisperx_directories() == expected


def test_successful_result_is_written_as_ascii_json(tmp_path, monkeypatch):
    report = tmp_path / "preflight.json"
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda: {
            "schema_version": "whisperx-runtime-preflight/1",
            "status": "pass",
            "gpu": {"name": "RTX", "memory_total_mib": 1},
            "production_services_modified": False,
        },
    )
    monkeypatch.setattr(sys, "argv", ["preflight", "--report", str(report)])

    assert preflight.main() == 0
    text = report.read_text(encoding="utf-8")
    assert text.isascii()
    assert json.loads(text)["status"] == "pass"
