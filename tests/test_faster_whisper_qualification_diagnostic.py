from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "qualify-faster-whisper-production.yml"
SCRIPT = ROOT / "scripts" / "qualify-faster-whisper-production.ps1"


def test_workflow_exports_gate_level_diagnostic_artifact():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "QualificationDiagnosticPath" in workflow
    assert "qualification-diagnostic.json" in workflow
    assert "qualification-summary.json" not in workflow
    assert "sample-diagnostics.json" not in workflow
    assert "qualification-runner.stdout.log" not in workflow
    assert "qualification-runner.stderr.log" not in workflow
    assert "Failed gates:" in workflow
    assert "Failed sample IDs:" in workflow
    assert "$failedSampleDiagnostics" in workflow
    assert "Where-Object { $_.pass -eq $false }" in workflow
    assert "canonical=``$($_.canonical_equal)``" in workflow
    assert "markdown=``$($_.markdown_equal)``" in workflow
    assert "turns=``$($_.turns_equal)``" in workflow
    assert "first_canonical_sha256" not in workflow
    assert "second_canonical_sha256" not in workflow


def test_diagnostic_projection_excludes_transcript_text():
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index("function Convert-ToSanitizedQualificationSample")
    end = script.index("Write-QualificationProgress -Stage \"wrapper_start\"")
    projection = script[start:end]

    assert "faster-whisper-r3-diagnostic/2" in projection
    assert "reference_text" not in projection
    assert "hypothesis_text" not in projection
    assert "expected_terms" not in projection
    assert "expected_codes" not in projection
    assert "canonical_sha256" in projection
    for field in (
        "canonical_equal",
        "markdown_equal",
        "turns_equal",
        "first_canonical_sha256",
        "second_canonical_sha256",
        "first_markdown_sha256",
        "second_markdown_sha256",
        "first_turns_sha256",
        "second_turns_sha256",
        "first_segment_count",
        "second_segment_count",
        "first_parser_turn_count",
        "second_parser_turn_count",
    ):
        assert field in projection
    assert "timestamp_drift_max_ms" in projection
    assert "failed_sample_ids" in projection
    assert "Assert-QualificationDiagnosticProjection" in projection
    assert "unapproved_payload" in projection
    assert "qualification-projection-private-marker" in projection
    assert "$safe.Count -ne 36" in projection


def test_script_writes_diagnostic_for_report_and_wrapper_failures():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "-Stage \"qualification_runner\"" in script
    assert "-Stage \"wrapper\"" in script
    assert "$FailureCode = \"quality_gate_failed\"" in script
    assert "diagnostic_available" in script
