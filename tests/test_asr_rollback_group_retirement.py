from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "retire-asr-rollback-group.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "retire-asr-rollback-group.yml").read_text(encoding="utf-8")


def test_retirement_is_exact_hash_locked_and_recoverable():
    assert "Candidate must have exactly one matching activation reference" in SCRIPT
    assert "Activation state must be rolled-back" in SCRIPT
    assert "Active candidate is permanently protected" in SCRIPT
    assert "Approved manifest SHA-256 mismatch" in SCRIPT
    assert "Scheduled Task identity changed after preview" in SCRIPT
    assert "-Mode Restore" in WORKFLOW
    assert "group: production-gpu-exclusive" in WORKFLOW


def test_workflow_is_limited_to_the_three_approved_groups():
    expected = {
        "31512225203": "31513770886",
        "31516434826": "31517785623",
        "31877287791": "31877991737",
    }
    for candidate, activation in expected.items():
        assert f"'{candidate}' = '{activation}'" in WORKFLOW
    assert "Requested group is outside the approved retirement boundary" in WORKFLOW
    assert "if ([string]$active.candidate_id -ne '31879196389')" in WORKFLOW
