from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_faster_whisper_resolver_evidence as evidence


def _diagnostic(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "faster-whisper-r3-dependency-failure/2",
        "status": "fail",
        "failure_code": "dependency_preparation_failed",
        "commit_sha": evidence.SOURCE_COMMIT_SHA,
        "run_id": evidence.SOURCE_RUN_ID,
        "dependency_stage": "pip_download",
        "dependency_operation": "pip_download_command",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "captured_line_count": 130,
        "diagnosis_kind": "resolver_replay_insufficient",
        "affected_requirement": "",
        "fallback_probe_executed": True,
        "fallback_probe_exit_code": 1,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    value.update(overrides)
    return value


def _source(tmp_path: Path, primary: str, fallback: str, **diagnostic: object) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "logs" / "pip-download.log").write_text(primary, encoding="utf-8")
    (tmp_path / "logs" / "pip-resolver-fallback.log").write_text(
        fallback, encoding="utf-8"
    )
    (tmp_path / "reports" / "dependency-diagnostic.json").write_text(
        json.dumps(_diagnostic(**diagnostic)), encoding="utf-8"
    )
    return tmp_path


def test_extracts_normalized_blockers_without_raw_evidence(tmp_path: Path):
    secret = "https://user:token@example.invalid/private?proxy=secret"
    root = _source(
        tmp_path,
        "\n".join(
            [
                "ERROR: Cannot install demo==2 and owner==1 because these package versions have conflicting dependencies.",
                "The user requested (constraint) demo==2",
                "owner 1.0 depends on demo<2",
                secret,
            ]
        ),
        "ERROR: No matching distribution found for nativepkg==9",
    )

    result = evidence.extract_evidence(source_root=root)
    serialized = json.dumps(result, sort_keys=True)

    assert result["status"] == "evidence_complete"
    assert result["source_run_id"] == "30968517582"
    assert result["profile_admission"] == "disabled"
    assert result["production_services_modified"] is False
    assert {item["diagnosis_kind"] for item in result["blockers"]} == {
        "binary_distribution_unavailable",
        "version_constraint_conflict",
    }
    assert secret not in serialized
    assert "example.invalid" not in serialized
    assert "pip-download.log" not in serialized


def test_output_contract_is_strict_and_deterministic(tmp_path: Path):
    root = _source(
        tmp_path,
        "The user requested demo==2\nowner 1.0 depends on demo<2",
        "ERROR: ResolutionImpossible",
    )
    first = evidence.extract_evidence(source_root=root)
    second = evidence.extract_evidence(source_root=root)
    assert first == second
    assert set(first) == {
        "schema_version", "status", "source_run_id", "source_commit_sha",
        "source_diagnostic_schema", "logs", "error_family_counts", "candidates",
        "blockers", "unparsed_relevant_line_count", "profile_admission",
        "production_services_modified",
    }
    assert all(set(item) == {"sha256", "size_bytes", "line_count"} for item in first["logs"].values())
    assert all(set(item) == {"kind", "package", "specifier", "owner", "owner_version", "occurrence_count"} for item in first["candidates"])
    assert all(set(item) == {"package", "diagnosis_kind", "specifiers", "owners", "evidence_count"} for item in first["blockers"])


def test_unversioned_owner_dependency_does_not_prove_conflict(tmp_path: Path):
    root = _source(
        tmp_path,
        "The user requested (constraint) oss2==2.19.1",
        "funasr 1.4.1 depends on oss2",
    )
    result = evidence.extract_evidence(source_root=root)
    assert result["status"] == "evidence_incomplete"
    assert result["blockers"] == []
    assert {item["kind"] for item in result["candidates"]} == {
        "constraint_requirement",
        "owner_dependency",
    }


@pytest.mark.parametrize(
    ("owner_specifier", "constraint_specifier", "expected"),
    [
        ("<2", "==2", True),
        (">=2,<3", "==2.19.1", False),
        ("==1.5", "==2", True),
        ("", "==2", False),
        ("~=1.5", "==2", False),
        ("<2", ">=2", False),
    ],
)
def test_conflict_proof_is_limited_and_unique(
    owner_specifier: str, constraint_specifier: str, expected: bool
):
    assert evidence._specifiers_prove_conflict(owner_specifier, constraint_specifier) is expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "1"),
        ("commit_sha", "0" * 40),
        ("schema_version", "faster-whisper-r3-dependency-failure/1"),
        ("profile_admission", "enabled"),
        ("production_services_modified", True),
    ],
)
def test_rejects_source_contract_mismatch(tmp_path: Path, field: str, value: object):
    root = _source(tmp_path, "ERROR: ResolutionImpossible", "fallback", **{field: value})
    with pytest.raises(evidence.EvidenceError, match="source diagnostic mismatch"):
        evidence.extract_evidence(source_root=root)


def test_rejects_unknown_diagnostic_field(tmp_path: Path):
    root = _source(tmp_path, "primary", "fallback", unexpected="value")
    with pytest.raises(evidence.EvidenceError, match="field set mismatch"):
        evidence.extract_evidence(source_root=root)


def test_malformed_requirement_is_counted_not_disclosed(tmp_path: Path):
    root = _source(
        tmp_path,
        "ERROR: No matching distribution found for bad/package @ https://secret.invalid/x",
        "fallback",
    )
    result = evidence.extract_evidence(source_root=root)
    assert result["status"] == "evidence_incomplete"
    assert result["unparsed_relevant_line_count"] == 1
    assert "secret.invalid" not in json.dumps(result)


def test_rejects_oversized_or_non_utf8_source(tmp_path: Path):
    root = _source(tmp_path, "primary", "fallback")
    primary = root / "logs" / "pip-download.log"
    primary.write_bytes(b"x" * (evidence.MAX_FILE_BYTES + 1))
    with pytest.raises(evidence.EvidenceError, match="file size"):
        evidence.extract_evidence(source_root=root)
    primary.write_bytes(b"\xff")
    with pytest.raises(evidence.EvidenceError, match="strict UTF-8"):
        evidence.extract_evidence(source_root=root)


def test_rejects_reparse_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _source(tmp_path, "primary", "fallback")
    monkeypatch.setattr(evidence, "_is_reparse_point", lambda path: path.name == "pip-download.log")
    with pytest.raises(evidence.EvidenceError, match="non-reparse"):
        evidence.extract_evidence(source_root=root)


def test_implementation_has_no_execution_or_network_imports():
    source = Path(evidence.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("import subprocess", "import socket", "import requests", "import urllib", "import httpx", "-m pip", "pip install", "pip download"):
        assert forbidden not in source
