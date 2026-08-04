from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from asr_service.model_cache import (
    MODEL_MANIFEST_VERSION,
    SENSEVOICE_MODEL_ID,
    SENSEVOICE_RELATIVE_PATH,
    SENSEVOICE_REVISION,
    validate_sensevoice_cache,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "prepare_asr_model.py"
SPEC = importlib.util.spec_from_file_location("prepare_asr_model", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def fake_downloader(download_cache: Path) -> Path:
    model = download_cache / "iic" / "SenseVoiceSmall"
    (model / "configuration.json").parent.mkdir(parents=True, exist_ok=True)
    (model / "configuration.json").write_text('{"model":"sensevoice"}\n', encoding="utf-8")
    (model / "model.pt").write_bytes(b"fixed-test-weights")
    (model / "tokens.txt").write_text("a\nb\n", encoding="utf-8")
    return model


def test_prepare_model_generates_strict_manifest_and_promotes(tmp_path: Path):
    cache = tmp_path / "models"
    staging = tmp_path / "models-staging"
    backups = tmp_path / "backups"

    result = MODULE.prepare_model(cache, staging, backups, fake_downloader)
    model = cache / Path(SENSEVOICE_RELATIVE_PATH)
    manifest = model / "model-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert result["status"] == "prepared"
    assert payload["schema_version"] == MODEL_MANIFEST_VERSION
    assert payload["model_id"] == SENSEVOICE_MODEL_ID
    assert payload["model_revision"] == SENSEVOICE_REVISION
    assert payload["model_path"] == SENSEVOICE_RELATIVE_PATH
    assert [entry["path"] for entry in payload["files"]] == [
        "configuration.json",
        "model.pt",
        "tokens.txt",
    ]
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) for entry in payload["files"])
    assert validate_sensevoice_cache(cache, manifest).available is True
    assert list(backups.glob("successful-model-staging-*"))


def test_existing_valid_cache_is_idempotent_and_does_not_download(tmp_path: Path):
    cache = tmp_path / "models"
    staging = tmp_path / "models-staging"
    backups = tmp_path / "backups"
    MODULE.prepare_model(cache, staging, backups, fake_downloader)

    def forbidden(_download_cache: Path) -> Path:
        raise AssertionError("downloader must not run for an already valid cache")

    result = MODULE.prepare_model(cache, staging, backups, forbidden)
    assert result["status"] == "already-available"


def test_invalid_existing_model_is_backed_up_before_replacement(tmp_path: Path):
    cache = tmp_path / "models"
    final = cache / Path(SENSEVOICE_RELATIVE_PATH)
    final.mkdir(parents=True)
    (final / "partial.bin").write_bytes(b"partial")

    result = MODULE.prepare_model(
        cache,
        tmp_path / "models-staging",
        tmp_path / "backups",
        fake_downloader,
    )

    assert result["status"] == "prepared"
    invalid_backups = list((tmp_path / "backups").glob("invalid-model-*"))
    assert len(invalid_backups) == 1
    assert (invalid_backups[0] / "partial.bin").read_bytes() == b"partial"


def test_downloader_cannot_escape_staging_and_failure_is_archived(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.pt").write_bytes(b"not-allowed")

    with pytest.raises(RuntimeError, match="outside the staging cache"):
        MODULE.prepare_model(
            tmp_path / "models",
            tmp_path / "models-staging",
            tmp_path / "backups",
            lambda _download_cache: outside,
        )

    assert list((tmp_path / "backups").glob("failed-model-staging-*"))
    assert not (tmp_path / "models" / Path(SENSEVOICE_RELATIVE_PATH)).exists()


def test_manifest_bytes_are_deterministic_for_same_tree(tmp_path: Path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    for model in (left, right):
        model.mkdir()
        (model / "b.bin").write_bytes(b"b")
        (model / "a.bin").write_bytes(b"a")

    left_manifest = MODULE._write_manifest(left)
    right_manifest = MODULE._write_manifest(right)
    assert left_manifest.read_bytes() == right_manifest.read_bytes()


def test_model_preparation_workflow_is_manual_fixed_and_fail_closed():
    workflow = read(".github/workflows/prepare-asr-model-production.yml")
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "environment: production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert workflow.count("timeout-minutes: 60") == 1
    assert re.search(r"prepare_model:.*?default: false", workflow, re.DOTALL)
    assert "prepare_model must be explicitly enabled" in workflow
    assert "inputs.commit_sha" in workflow
    assert "40-character" in workflow
    assert "commit_sha must equal the workflow dispatch revision" in workflow
    assert "${{ github.sha }}" in workflow
    assert "ASR_MODEL_DOWNLOAD_PROXY: ${{ secrets.ASR_MODEL_DOWNLOAD_PROXY }}" in workflow
    assert "ASR_SERVICE_TOKEN" not in workflow
    assert "activate_service" not in workflow.lower()


def test_powershell_wrapper_keeps_service_closed_and_scopes_proxy():
    script = read("scripts/prepare-asr-model.ps1")
    lowered = script.lower()
    assert "PrepareModel must be explicitly enabled" in script
    assert "ASR_SERVICE_ENABLED=false" in script
    assert "At least 10 GiB free space" in script
    assert "Get-ScheduledTask" in script
    assert "Get-NetTCPConnection -LocalPort 8200 -State Listen" in script
    assert "ASR_MODEL_DOWNLOAD_PROXY is required" in script
    assert "$env:HTTP_PROXY = $downloadProxy" in script
    assert "$env:HTTPS_PROXY = $downloadProxy" in script
    assert "[System.EnvironmentVariableTarget]::Process" in script
    assert "start-scheduledtask" not in lowered
    assert "register-scheduledtask" not in lowered
    assert "new-netfirewallrule" not in lowered
    assert "set-netfirewallrule" not in lowered
    assert "netsh advfirewall" not in lowered
    assert "remove-item" not in lowered
    assert "asr_service_token" not in lowered
    assert "${PRIVATE_IPV4}" in script


def test_python_downloader_has_no_free_model_or_revision_inputs():
    script = read("scripts/prepare_asr_model.py")
    assert "snapshot_download(" in script
    assert "SENSEVOICE_MODEL_ID" in script
    assert "revision=SENSEVOICE_REVISION" in script
    assert 'parser.add_argument("--model-id"' not in script
    assert 'parser.add_argument("--model-revision"' not in script
    assert 'parser.add_argument("--revision"' not in script
    assert "validate_sensevoice_cache" in script
    assert "model-manifest.json" in script
    assert "shutil.rmtree" not in script


def test_existing_deployment_channel_remains_model_free():
    deploy = read("scripts/deploy-asr.ps1").lower()
    assert "prepare_asr_model" not in deploy
    assert "snapshot_download" not in deploy
    assert "modelscope download" not in deploy
