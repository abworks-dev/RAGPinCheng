from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT / ".github/workflows/retire-production-legacy-docs-directory.yml"
).read_text(encoding="utf-8")


def test_retirement_is_manual_commit_pinned_and_serialized():
    for required in (
        "RETIRE_EMPTY_LEGACY_DOCS_DIR",
        'group: production-app-manual-v1',
        '[ "${GITHUB_REF}" = "refs/heads/master" ]',
        '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]',
        '[ "${CONTENT_HEAD_ENFORCEMENT}" = "strict" ]',
        '[ "${SOURCE_DECOUPLING_COMPLETE}" = "true" ]',
        "label=com.docker.compose.project=ragpincheng-prod",
        "label=com.docker.compose.service=backend",
        'expected exactly one running production backend',
    ):
        assert required in WORKFLOW


def test_retirement_requires_empty_unmounted_path_and_has_rollback_metadata():
    for required in (
        'LEGACY_DOCS_DIR="/data/business/ragpincheng/content/legacy-docs"',
        'docs.get("Type") == "tmpfs"',
        'docs.get("RW") is False',
        'find "${LEGACY_DOCS_DIR}" -mindepth 1 -print -quit',
        'findmnt -rn -S "${LEGACY_DOCS_DIR}"',
        'directory-metadata.txt',
        'restore_empty_directory',
        'rmdir -- "${LEGACY_DOCS_DIR}"',
        'status=retired',
    ):
        assert required in WORKFLOW
    for forbidden in (
        "rm -rf",
        "docker compose down",
        "/data/business/ragpincheng/source/docs",
        "/data/business/ragpincheng/source/media",
    ):
        assert forbidden not in WORKFLOW
