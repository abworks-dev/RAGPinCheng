from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/preflight-source-decoupling-t12.yml").read_text(
    encoding="utf-8"
)


def test_t12_preflight_is_explicitly_read_only_and_commit_pinned():
    assert "PREFLIGHT_T12" in WORKFLOW
    assert '[ "${GITHUB_SHA}" = "${EXPECTED_COMMIT}" ]' in WORKFLOW
    assert 'ref: ${{ inputs.expected_commit }}' in WORKFLOW
    assert 'EXPECTED_HEAD_ENFORCEMENT}" = "compat"' in WORKFLOW
    assert "--expected-managed-heads 116" in WORKFLOW
    assert "flock -s -n" in WORKFLOW
    for forbidden in (
        "DELETE FROM",
        "UPDATE media_assets",
        "qdrant.snapshot",
        "docker compose down",
        "rm -rf",
        "CONTENT_HEAD_ENFORCEMENT=strict",
    ):
        assert forbidden not in WORKFLOW


def test_t12_preflight_only_uploads_redacted_aggregate_summary():
    assert "preflight_source_decoupling_t12.py" in WORKFLOW
    assert "summary.json" in WORKFLOW
    assert "No filenames, paths, business hashes, record IDs" in WORKFLOW
    assert "source-decoupling-t12-preflight" in WORKFLOW
