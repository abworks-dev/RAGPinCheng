from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/decouple-production-source-t12.yml").read_text(
    encoding="utf-8"
)
DEPLOY = (ROOT / ".github/workflows/deploy-production-app-emergency.yml").read_text(
    encoding="utf-8"
)


def test_t12b_workflow_is_manual_commit_pinned_and_frozen():
    for required in (
        "DECOUPLE_SOURCE_T12_B",
        '[ "${GITHUB_REF}" = "refs/heads/master" ]',
        '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]',
        "EXPECTED_MANAGED_HEADS=116",
        "EXPECTED_CANDIDATE_PARENTS=44",
        "EXPECTED_CANDIDATE_POINTS=104",
        "EXPECTED_MEDIA_ASSETS=4",
        "EXPECTED_TRANSCRIPT_HEADS=2",
        '"media_statuses":{"failed":1,"ready":1,"transcript_ready":2}',
        '"parents_versioned_transcript":0',
        '"points_versioned_transcript":0',
        '"transcript_contracts":{"automatic|managed_artifact|published":2}',
        "a36bbef41e174a42e4bdf99b76ea1c99c8f296ef0288b2b753b1ff89c52bc53a",
        '[ "${EXPECTED_HEAD_ENFORCEMENT}" = "compat" ]',
        "cancel-in-progress: false",
    ):
        assert required in WORKFLOW


def test_t12b_workflow_has_independent_backups_exact_apply_and_rollback():
    for required in (
        ".managed-content-t9.lock",
        ".production-app-deploy.lock",
        "findmnt -n -o MAJ:MIN",
        "insufficient independent backup capacity",
        '"${COMPOSE[@]}" stop backend',
        "PRAGMA integrity_check",
        'rsync -aHAXx --numeric-ids "${CONTENT_ROOT}/"',
        'rsync -aHAXx --numeric-ids "${SOURCE_DOCS}/"',
        'rsync -aHAXx --numeric-ids "${SOURCE_MEDIA}/"',
        "qdrant.snapshot",
        "plan_source_decoupling_t12.py",
        "apply_source_decoupling_t12.py",
        "--expected-plan-sha256",
        "T12_B_ROLLBACK status=complete",
        "T12_B_DECOUPLE status=success",
        '"${CONTENT_ROOT}/media"',
        '"${CONTENT_ROOT}/transcription-artifacts"',
        '"${CONTENT_ROOT}/legacy-docs"',
    ):
        assert required in WORKFLOW
    for forbidden in (
        "delete_collection",
        "docker compose down",
        'rm -rf "${SOURCE_ROOT}"',
        'rm -rf "${SOURCE_DOCS}"',
        'rm -rf "${SOURCE_MEDIA}"',
    ):
        assert forbidden not in WORKFLOW


def test_t12b_public_artifact_is_redacted_and_deploy_checks_mounts():
    upload = WORKFLOW.split("- name: Upload redacted T12-B summary", 1)[1]
    assert "source-decoupling-plan.json" not in upload
    assert "Exact IDs and business paths remain only in the restricted backup" in WORKFLOW
    for required in (
        "verify_source_decoupling_mounts",
        'mounts.get("/app/docs") == os.environ["EXPECTED_DOCS"]',
        'mounts.get("/app/media") == os.environ["EXPECTED_MEDIA"]',
        'not source.startswith("/data/business/ragpincheng/source")',
        'TRANSCRIPTION_ARTIFACT_DIR == Path("/app/content/transcription-artifacts")',
    ):
        assert required in DEPLOY
