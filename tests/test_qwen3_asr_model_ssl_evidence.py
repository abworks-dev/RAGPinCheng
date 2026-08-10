from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_qwen3_asr_model_ssl_evidence as evidence


def _source(tmp_path: Path, log: str) -> Path:
    for name in ("logs", "evidence", "reports"):
        (tmp_path / name).mkdir()
    verdict = {
        "schema_version": "qwen3-asr-r3-verdict/1",
        "status": "fail",
        "failure_code": "model_preparation_failed",
        "commit_sha": evidence.SOURCE_COMMIT_SHA,
        "run_id": evidence.SOURCE_RUN_ID,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    diagnostic = {
        "schema_version": "qwen3-asr-model-preparation-failure/1",
        "status": "fail",
        "stage": "model_preparation",
        "kind": "snapshot_download_failed",
        "model": "asr",
        "exception_type": "SSLError",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "log_present": True,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    (tmp_path / "reports" / "qualification-verdict.json").write_text(
        json.dumps(verdict), encoding="utf-8"
    )
    (tmp_path / "evidence" / "model-preparation-diagnostic.json").write_text(
        json.dumps(diagnostic), encoding="utf-8"
    )
    (tmp_path / "logs" / "model-preparation.log").write_text(log, encoding="utf-8")
    return tmp_path


def test_extracts_certificate_and_endpoint_categories_without_raw_text(tmp_path):
    secret = "https://user:token@huggingface.co/private/model"
    root = _source(
        tmp_path,
        f"SSLError: certificate verify failed: unable to get local issuer {secret}",
    )
    result = evidence.extract_evidence(source_root=root)
    serialized = json.dumps(result, sort_keys=True)
    assert result["status"] == "evidence_complete"
    assert result["ssl_signals"] == ["certificate_verify_failed", "ssl_error"]
    assert result["endpoint_families"] == ["huggingface"]
    assert secret not in serialized
    assert "token" not in serialized
    assert result["profile_admission"] == "disabled"
    assert result["production_services_modified"] is False


def test_extracts_xet_proxy_tunnel_category(tmp_path):
    root = _source(
        tmp_path,
        "ProxyError: tunnel connection failed for https://cas-bridge.xethub.hf.co/blob",
    )
    result = evidence.extract_evidence(source_root=root)
    assert result["ssl_signals"] == ["proxy_tunnel_failure"]
    assert result["endpoint_families"] == ["huggingface", "xet"]


def test_rejects_wrong_source_identity(tmp_path):
    root = _source(tmp_path, "SSLError")
    path = root / "reports" / "qualification-verdict.json"
    verdict = json.loads(path.read_text(encoding="utf-8"))
    verdict["run_id"] = "1"
    path.write_text(json.dumps(verdict), encoding="utf-8")
    with pytest.raises(evidence.EvidenceError, match="run_id"):
        evidence.extract_evidence(source_root=root)


def test_generic_ssl_is_evidence_incomplete(tmp_path):
    result = evidence.extract_evidence(source_root=_source(tmp_path, "SSLError"))
    assert result["status"] == "evidence_incomplete"
    assert result["ssl_signals"] == ["ssl_error"]
    assert result["endpoint_families"] == []
