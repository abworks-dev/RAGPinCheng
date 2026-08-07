from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_qwen3_asr_resolver_evidence as evidence


def _diagnostic(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "qwen3-asr-r3-dependency-diagnostic/2",
        "status": "diagnostic_failed",
        "failure_code": "focused_probe_failed",
        "commit_sha": evidence.SOURCE_COMMIT_SHA,
        "diagnostic_run_id": evidence.SOURCE_RUN_ID,
        "source_run_id": "30970277613",
        "source_commit_sha": "86b69db6831e3cd201436a26bbe836229bf419bd",
        "production_freeze_sha256": "1" * 64,
        "combined_requirements_sha256": "2" * 64,
        "windows_requirements_sha256": "3" * 64,
        "qwen3_asr_requirements_sha256": "4" * 64,
        "internal_wheel_manifest_sha256": "5" * 64,
        "resolver_replayed": True,
        "resolver_exit_code": 1,
        "dependency_operation": "focused_probe_command",
        "failure_origin": "native_exit",
        "native_exit_code": 1,
        "captured_line_count": 16,
        "diagnosis_kind": "unknown",
        "affected_requirement": "oss2",
        "focused_probe_executed": True,
        "focused_probe_exit_code": 1,
        "cleanup_complete": True,
        "profile_admission": "disabled",
        "production_services_modified": False,
    }
    value.update(overrides)
    return value


def _source(tmp_path: Path, replay: str, probe: str, **diagnostic: object) -> Path:
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (tmp_path / "logs" / "resolver-replay.log").write_text(replay, encoding="utf-8")
    (tmp_path / "logs" / "focused-binary-probe.log").write_text(
        probe, encoding="utf-8"
    )
    (tmp_path / "state" / "dependency-diagnostic.json").write_text(
        json.dumps(_diagnostic(**diagnostic)), encoding="utf-8"
    )
    return tmp_path


def test_extracts_only_normalized_evidence(tmp_path: Path):
    secret = "https://user:token@example.invalid/private?proxy=secret"
    root = _source(
        tmp_path,
        "\n".join(
            (
                "The user requested (constraint) demo==2",
                "owner 1.0 depends on demo<2",
                secret,
            )
        ),
        "ERROR: No matching distribution found for nativepkg==9",
    )
    result = evidence.extract_evidence(source_root=root)
    serialized = json.dumps(result, sort_keys=True)
    assert result["status"] == "evidence_complete"
    assert result["classification"] == "proven_version_conflict"
    assert result["source_run_id"] == "30972780438"
    assert {item["diagnosis_kind"] for item in result["blockers"]} == {
        "binary_distribution_unavailable",
        "version_constraint_conflict",
    }
    assert secret not in serialized
    assert "example.invalid" not in serialized
    assert "resolver-replay.log" not in serialized
    assert result["profile_admission"] == "disabled"
    assert result["production_services_modified"] is False


def test_unversioned_oss2_owner_is_not_misclassified(tmp_path: Path):
    root = _source(
        tmp_path,
        "The user requested (constraint) oss2==2.19.1",
        "funasr 1.4.1 depends on oss2",
    )
    result = evidence.extract_evidence(source_root=root)
    assert result["status"] == "evidence_incomplete"
    assert result["classification"] == "still_unknown"
    assert result["blockers"] == []
    assert {item["kind"] for item in result["candidates"]} == {
        "constraint_requirement",
        "owner_dependency",
    }


def test_contract_is_strict_and_deterministic(tmp_path: Path):
    root = _source(tmp_path, "ERROR: ResolutionImpossible", "probe")
    first = evidence.extract_evidence(source_root=root)
    assert first == evidence.extract_evidence(source_root=root)
    assert set(first) == {
        "schema_version",
        "status",
        "classification",
        "source_run_id",
        "source_commit_sha",
        "source_diagnostic_schema",
        "logs",
        "error_family_counts",
        "candidates",
        "blockers",
        "unparsed_relevant_line_count",
        "unparsed_records",
        "profile_admission",
        "production_services_modified",
    }


@pytest.mark.parametrize(
    ("line", "category", "classification"),
    (
        (
            "ERROR: resolver failed while considering conflicting candidates",
            "resolver_context_only",
            "resolver_context_only",
        ),
        (
            "ERROR: requirement needs a private URL https://user:token@example.invalid/x",
            "still_unknown",
            "still_unknown",
        ),
        (
            "ERROR: runtime requires Python @/private/path",
            "python_incompatible",
            "python_incompatible",
        ),
        (
            "ERROR: No matching distribution for package @ https://secret.invalid/x",
            "binary_unavailable",
            "binary_unavailable",
        ),
    ),
)
def test_unparsed_lines_emit_only_fixed_safe_metadata(
    tmp_path: Path, line: str, category: str, classification: str
):
    root = _source(tmp_path, line, "probe")
    result = evidence.extract_evidence(source_root=root)
    serialized = json.dumps(result, sort_keys=True)
    assert result["classification"] == classification
    assert result["unparsed_relevant_line_count"] == 1
    assert len(result["unparsed_records"]) == 1
    record = result["unparsed_records"][0]
    assert set(record) == {
        "source",
        "line_number",
        "character_count",
        "sha256",
        "character_types",
        "category",
        "requirements",
    }
    assert record["category"] == category
    assert set(record["character_types"]) == {
        "ascii_letters",
        "digits",
        "whitespace",
        "punctuation",
        "non_ascii",
    }
    assert line not in serialized
    assert "example.invalid" not in serialized
    assert "secret.invalid" not in serialized
    assert "/private/path" not in serialized


def test_context_prefix_can_prove_only_an_explicit_conflict(tmp_path: Path):
    root = _source(
        tmp_path,
        "ERROR: pip context: The user requested (constraint) oss2==2.19.1",
        "ERROR: pip context: funasr 1.4.1 depends on oss2<2",
    )
    result = evidence.extract_evidence(source_root=root)
    assert result["classification"] == "proven_version_conflict"
    assert result["status"] == "evidence_complete"
    assert all(item["requirements"] for item in result["unparsed_records"])


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("diagnostic_run_id", "1"),
        ("commit_sha", "0" * 40),
        ("schema_version", "qwen3-asr-r3-dependency-diagnostic/1"),
        ("captured_line_count", 15),
        ("cleanup_complete", False),
        ("profile_admission", "enabled"),
        ("production_services_modified", True),
    ),
)
def test_rejects_source_contract_mismatch(
    tmp_path: Path, field: str, value: object
):
    root = _source(tmp_path, "replay", "probe", **{field: value})
    with pytest.raises(evidence.EvidenceError, match="source diagnostic mismatch"):
        evidence.extract_evidence(source_root=root)


def test_rejects_unknown_field_and_unsafe_source(tmp_path: Path, monkeypatch):
    root = _source(tmp_path, "replay", "probe", unexpected="value")
    with pytest.raises(evidence.EvidenceError, match="field set mismatch"):
        evidence.extract_evidence(source_root=root)

    root = _source(tmp_path / "second", "replay", "probe")
    monkeypatch.setattr(
        evidence, "_is_reparse_point", lambda path: path.name == "resolver-replay.log"
    )
    with pytest.raises(evidence.EvidenceError, match="non-reparse"):
        evidence.extract_evidence(source_root=root)


def test_implementation_has_no_execution_or_network_capability():
    source = Path(evidence.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "import subprocess",
        "import socket",
        "import requests",
        "import urllib",
        "import httpx",
        "-m pip",
        "pip install",
        "pip download",
    ):
        assert forbidden not in source
