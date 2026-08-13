from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/apply-managed-content-t10.yml").read_text(
    encoding="utf-8"
)


def test_t10_apply_requires_separate_confirmation_fixed_master_and_compat():
    assert "workflow_dispatch:" in WORKFLOW
    assert "APPLY_T10" in WORKFLOW
    assert '[ "${GITHUB_REF}" = "refs/heads/master" ]' in WORKFLOW
    assert '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]' in WORKFLOW
    assert '[ "${EXPECTED_HEAD_ENFORCEMENT}" = "compat" ]' in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW


def test_t10_apply_is_pinned_to_preflight_parameters_and_actor_variable():
    assert 'T9_RUN_ID="t9-31656884161-1"' in WORKFLOW
    assert 'EXPECTED_COUNT=117' in WORKFLOW
    assert "d28157cdfa710b30ed16ce1dbac71d3b001b15e950fa241db95dbb7cbae457e6" in WORKFLOW
    assert "T10_IMPORT_ACTOR_USER_ID" in WORKFLOW
    assert '[[ "${ACTOR_USER_ID}" =~ ^[1-9][0-9]*$ ]]' in WORKFLOW


def test_t10_apply_creates_recovery_points_before_staging_and_apply():
    sqlite_backup = WORKFLOW.index('for name in ("app.sqlite", "parents.sqlite")')
    qdrant_backup = WORKFLOW.index("collections/pincheng_docs/snapshots")
    content_backup = WORKFLOW.index('"${CONTENT_ROOT}/" "${RUN_ROOT}/content/"')
    staging = WORKFLOW.index("stage_legacy_content_t10.py")
    apply = WORKFLOW.index("apply_legacy_content_t10.py")
    assert sqlite_backup < staging
    assert qdrant_backup < staging
    assert content_backup < staging < apply
    assert "Qdrant collection is not green" in WORKFLOW
    assert "qdrant_points_before=" in WORKFLOW
    assert '"${BUSINESS_DEVICE}" != "${BACKUP_DEVICE}"' in WORKFLOW
    assert '"${DATA_DEVICE}" != "${BACKUP_DEVICE}"' in WORKFLOW


def test_t10_apply_stops_at_review_and_preserves_legacy_sources():
    assert "lifecycle_status='awaiting_review'" in WORKFLOW
    assert "SELECT count(*) FROM content_item_heads" in WORKFLOW
    assert "rebuild_content_view.py" not in WORKFLOW
    assert "publish" not in WORKFLOW.lower().replace("upload redacted t10 apply summary", "")
    for forbidden in ("rm -rf", "docker compose down", "source/media", "CONTENT_HEAD_ENFORCEMENT=strict"):
        assert forbidden not in WORKFLOW
