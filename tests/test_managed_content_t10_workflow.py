from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/preflight-managed-content-t10.yml").read_text(
    encoding="utf-8"
)


def test_t10_preflight_is_manual_fixed_master_and_compat_only():
    assert "workflow_dispatch:" in WORKFLOW
    assert "PREFLIGHT_T10" in WORKFLOW
    assert '[ "${GITHUB_REF}" = "refs/heads/master" ]' in WORKFLOW
    assert '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]' in WORKFLOW
    assert '[ "${EXPECTED_HEAD_ENFORCEMENT}" = "compat" ]' in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW


def test_t10_preflight_is_pinned_to_successful_t9_recovery_point():
    assert 'T9_RUN_ID="t9-31656884161-1"' in WORKFLOW
    assert "approved_commit=e96417f3be8b04575285845f2d9216a535b43004" in WORKFLOW
    assert 'EXPECTED_COUNT=117' in WORKFLOW
    assert 'PLAN_PATH="${T9_ROOT}/migration-plan.json"' in WORKFLOW
    assert "tooling-e96417f3be8b04575285845f2d9216a535b43004" in WORKFLOW
    assert 'cmp --silent "${PLAN_PATH}" "${REPRODUCED_PLAN}"' in WORKFLOW


def test_t10_preflight_has_no_production_write_or_sensitive_artifact():
    assert "preflight_legacy_content_t10.py" in WORKFLOW
    for forbidden in ("--apply", "apply_legacy_content_t10.py", "stage_legacy_content_t10.py", "rm -rf", "sudo "):
        assert forbidden not in WORKFLOW
    assert "relative paths, hashes of business files, or file contents" in WORKFLOW
    assert 'path: ${{ runner.temp }}/t10-' in WORKFLOW
