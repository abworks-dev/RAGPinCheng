from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/repair-app-registry-resolution-production.yml"


def _workflow_text() -> str:
    return (ROOT / WORKFLOW).read_text(encoding="utf-8")


def test_repair_workflow_requires_full_sha_master_and_explicit_confirmation():
    text = _workflow_text()
    workflow = yaml.safe_load(text)

    dispatch = workflow[True]["workflow_dispatch"]
    assert dispatch["inputs"]["confirm_repair"]["default"] is False
    assert set(dispatch["inputs"]["operation"]["options"]) == {"preflight", "apply", "revert"}
    assert dispatch["inputs"]["operation"]["default"] == "preflight"

    assert "[0-9a-fA-F]{40}" in text
    assert "commit_sha must equal the dispatch revision" in text
    assert 'refs/heads/master' in text
    assert "inputs.confirm_repair == true" in text


def test_repair_workflow_only_writes_a_controlled_hosts_record_with_a_backup():
    text = _workflow_text()

    assert "RAGPinCheng controlled app-node registry resolution" in text
    assert "APP_REGISTRY_HOSTS_BACKUP:" in text
    assert "cp -a /etc/hosts" in text
    assert "tee -a /etc/hosts" in text
    assert "sed -i" not in text
    assert "> /etc/hosts" not in text
    # apply must verify the mirror answers with a valid certificate before writing
    assert "--resolve" in text
    assert "no IPv4 address available" in text


def test_repair_workflow_revert_restores_the_approved_backup():
    text = _workflow_text()

    assert "backup_path is required for revert" in text
    assert "backup_path is accepted only for revert" in text
    assert "cp -a \"${BACKUP_PATH}\" /etc/hosts" in text
    assert "APP_REGISTRY_HOSTS_REVERTED:" in text


def test_repair_workflow_preflight_is_read_only():
    text = _workflow_text()
    workflow = yaml.safe_load(text)

    steps = workflow["jobs"]["repair"]["steps"]
    write_steps = {step["name"] for step in steps if "if" in step}
    assert write_steps == {
        "Apply the controlled resolution record",
        "Revert the controlled resolution record",
        "Publish sanitized summary",
    }
    inspect = next(step for step in steps if step["name"] == "Inspect the current resolution state read-only")
    assert "getent hosts" in inspect["run"]
    assert "tee" not in inspect["run"]
