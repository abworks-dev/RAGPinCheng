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
    assert str(tmp_path) not in report.read_text(encoding="utf-8")


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
