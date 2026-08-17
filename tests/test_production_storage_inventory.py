from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inventory-production-storage.ps1"


def _powershell() -> str | None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    ps51 = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(ps51) if ps51.is_file() else shutil.which("powershell.exe")


def test_inventory_reports_only_aggregate_metadata(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "RAGPinCheng-ASR"
    candidate = data_root / "dependency-runs" / "candidate-secret-name"
    candidate.mkdir(parents=True)
    secret_file_name = "customer-secret-document.txt"
    (candidate / secret_file_name).write_bytes(b"content-not-for-report")
    report_path = tmp_path / "inventory.json"

    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ReportPath",
            str(report_path),
            "-AsrDataRoot",
            str(data_root),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    raw = report_path.read_text(encoding="utf-8-sig")
    report = json.loads(raw)
    assert secret_file_name not in raw
    assert "content-not-for-report" not in raw
    assert report["roots"]["asr_data"]["bytes"] == len(b"content-not-for-report")
    assert report["dependency_runs"]["candidate"]["directories"] == 1
    assert report["dependency_runs"]["candidate"]["bytes"] == len(b"content-not-for-report")
    assert report["breakdowns"]["asr_data"]["categories"]["dependency_runs"]["bytes"] == len(
        b"content-not-for-report"
    )
    assert "candidate-secret-name" not in raw
    assert report["policy"]["advisory_only"] is True
    assert report["candidates"][0]["advisory_status"] == "protected"


def test_inventory_classifies_candidate_release_and_active_references(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "data"
    program_root = tmp_path / "program"
    backup_root = data_root / "backups"
    dependency_root = data_root / "dependency-runs"
    for candidate_id in ("101", "102", "broken", "103"):
        (dependency_root / f"candidate-{candidate_id}").mkdir(parents=True)
        (dependency_root / f"candidate-{candidate_id}" / "wheelhouse.bin").write_bytes(b"x")
    (dependency_root / "candidate-103" / "run.lock").write_text("active", encoding="ascii")
    (program_root / "releases" / "101").mkdir(parents=True)
    (data_root / "config" / "releases" / "101").mkdir(parents=True)
    (program_root / "releases" / "101" / "release-manifest.json").write_text(
        '{"candidate_id":"101"}', encoding="utf-8"
    )
    (data_root / "release-state").mkdir(parents=True)
    (data_root / "release-state" / "active.json").write_text(
        '{"candidate_id":"101"}', encoding="utf-8"
    )
    activation = backup_root / "900"
    activation.mkdir(parents=True)
    (activation / "candidate-activation-state.json").write_text(
        '{"candidate_id":"102","previous_candidate_id":"101"}', encoding="utf-8"
    )
    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-ReportPath", str(report_path),
            "-AsrDataRoot", str(data_root), "-AsrProgramRoot", str(program_root),
            "-BackupDirectory", str(backup_root),
        ], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    candidates = {item["candidate_id"]: item for item in report["candidates"]}
    assert candidates["101"]["status"] == "identity-conflict"
    assert "active-release-state" in candidates["101"]["reasons"]
    assert candidates["102"]["status"] == "rollback-referenced"
    assert candidates["103"]["status"] == "active-marker"
    assert candidates["broken"]["status"] == "unknown-name"
    activation_audit = report["activation_audit"]
    assert activation_audit["references"][0]["activation_id"] == "900"
    assert activation_audit["references"][0]["candidate_ids"] == ["102", "101"]


def test_inventory_reports_other_entries_and_gpu_advisory_details(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "asr-data"
    runtime_root = tmp_path / "runtime"
    (data_root / "unclassified-output").mkdir(parents=True)
    (data_root / "unclassified-output" / "payload.bin").write_bytes(b"abc")
    invalid_release = runtime_root / "releases" / "invalid-release"
    invalid_release.mkdir(parents=True)
    (invalid_release / "runtime-manifest.json").write_text(
        '{"release_id":"different","qualification_status":"pending"}', encoding="utf-8"
    )
    (runtime_root / "qualification" / "101").mkdir(parents=True)
    (runtime_root / "resolver" / "pip-cache").mkdir(parents=True)
    (runtime_root / "resolver" / "pip-cache" / "cached.whl").write_bytes(b"cache")
    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-ReportPath", str(report_path),
            "-AsrDataRoot", str(data_root), "-RuntimeRoot", str(runtime_root),
        ], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    other = report["breakdowns"]["asr_data"]["other_entries"]
    assert other[0]["name"] == "unclassified-output"
    assert other[0]["bytes"] == 3
    release = report["gpu_runtime_inventory"]["releases"][0]
    assert release["identity"] == "invalid-release-contract"
    assert "failed-release_id_matches" in release["advisory_reasons"]
    assert release["advisory_status"] == "protected"
    resolver_cache = report["gpu_runtime_inventory"]["caches"]["resolver-pip-cache"]
    assert resolver_cache["bytes"] == len(b"cache")
    assert resolver_cache["advisory_status"] == "protected-inventory-only"
    assert report["gpu_runtime_inventory"]["resolver"] == []
    assert report["gpu_runtime_inventory"]["reference_inventory_status"] in {
        "measured", "unavailable-protect-all"
    }
    sources = report["gpu_runtime_inventory"]["reference_sources"]
    assert sources["scheduled_tasks"]["task_names"] == [
        "RAGPinCheng-GPU", "RAGPinCheng-GPU-Runtime-Cleanup"
    ]


def test_inventory_workflow_uses_asr_activation_backup_root():
    workflow = (ROOT / ".github" / "workflows" / "inventory-production-storage.yml").read_text(encoding="utf-8")
    assert "PRODUCTION_ASR_BACKUP_ROOT" in workflow
    assert "-AsrActivationBackupRoot" in workflow
    assert "GPU_MODEL_CACHE_SOURCE" in workflow
    assert "-GpuConfiguredModelCachePath" in workflow


def test_inventory_classifies_model_preparation_and_repair_caches(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "asr-data"
    runtime_root = tmp_path / "runtime"
    revision = data_root / "qualification" / "qwen3-asr" / "models" / "Qwen3-ASR-0.6B" / "abc123"
    revision.mkdir(parents=True)
    (revision / "model-manifest.json").write_text('{"status":"ready"}', encoding="utf-8")
    (revision / "weights.bin").write_bytes(b"model")

    preparation_root = data_root / "model-preparation" / "faster-whisper"
    final_manifest = (
        data_root / "models" / "faster-whisper-large-v3-turbo" /
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" / "model-manifest.json"
    )
    final_manifest.parent.mkdir(parents=True)
    final_manifest.write_text('{"status":"ready"}', encoding="utf-8")
    manifest_sha = hashlib.sha256(final_manifest.read_bytes()).hexdigest()
    old = datetime.now(timezone.utc) - timedelta(days=40)
    for run_id in ("100", "101", "102"):
        run = preparation_root / run_id
        run.mkdir(parents=True)
        (run / "model-preparation.json").write_text(
            json.dumps({"schema_version":"faster-whisper-model-preparation/1","status":"prepared","manifest_path":str(final_manifest),"manifest_sha256":manifest_sha}), encoding="utf-8"
        )
        (run / "offline-validation.json").write_text('{"status":"validated-offline"}', encoding="utf-8")
        os.utime(run, (old.timestamp() + int(run_id), old.timestamp() + int(run_id)))
    candidate_manifest = (
        preparation_root / "100" / "staging" / "candidate-cache" /
        "faster-whisper-large-v3-turbo" / "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" /
        "model-manifest.json"
    )
    candidate_manifest.parent.mkdir(parents=True)
    candidate_manifest.write_bytes(final_manifest.read_bytes())
    os.utime(preparation_root / "100", (old.timestamp() + 100, old.timestamp() + 100))

    repair = runtime_root / "model-cache-repair" / "200"
    for repository in ("models--BAAI--bge-m3", "models--BAAI--bge-reranker-v2-m3"):
        snapshot = repair / "hub" / repository / "snapshots" / "rev"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"weights")
    os.utime(repair, (old.timestamp(), old.timestamp()))

    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [
            executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-ReportPath", str(report_path),
            "-AsrDataRoot", str(data_root), "-RuntimeRoot", str(runtime_root),
            "-GpuConfiguredModelCachePath", str(repair),
        ], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    shared = report["asr_qualification_inventory"]["shared_model_revisions"][0]
    assert shared["advisory_status"] == "protected-active-model"
    runs = {item["run_id"]: item for item in report["asr_model_preparation_inventory"]["runs"]}
    assert runs["100"]["completion_status"] == "complete"
    if report["project_reference_inventory"]["status"] == "measured":
        assert runs["100"]["advisory_status"] == "eligible-advisory"
    repair_run = report["gpu_model_cache_repair_inventory"]["runs"][0]
    assert repair_run["embedding_complete"] is True
    assert repair_run["reranker_complete"] is True
    assert repair_run["advisory_status"] == "protected"
    assert "configured-model-cache-source" in repair_run["advisory_reasons"]
    assert runs["100"]["final_manifest_match"] is True
    assert runs["100"]["candidate_manifest_matches_final"] is True
    assert {item["kind"] for item in runs["100"]["components"]} == {
        "staging/download", "staging/candidate-cache", "report"
    }


def test_inventory_validates_and_protects_referenced_wheel_cache(tmp_path: Path):
    executable = _powershell()
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    data_root = tmp_path / "asr-data"
    wheel = b"wheel-content"
    wheel_sha = hashlib.sha256(wheel).hexdigest()
    key_material = {"schema_version": "faster-whisper-wheel-cache-key/1", "test": "inventory"}
    cache_key = hashlib.sha256(
        json.dumps(key_material, separators=(",", ":")).encode()
    ).hexdigest()
    cache = data_root / "qualification" / "wheel-cache" / cache_key
    cache.mkdir(parents=True)
    (cache / "package.whl").write_bytes(wheel)
    (cache / "cache-manifest.json").write_text(
        json.dumps({
            "schema_version": "faster-whisper-wheel-cache/1",
            "cache_key": cache_key,
            "key_material": key_material,
            "wheel_manifest": {
                "schema_version": "faster-whisper-wheel-manifest/3",
                "files": [{"file_name": "package.whl", "size_bytes": len(wheel), "sha256": wheel_sha}],
            },
        }), encoding="utf-8",
    )
    verdict = data_root / "qualification" / "runs" / "123" / "reports" / "qualification-verdict.json"
    verdict.parent.mkdir(parents=True)
    verdict.write_text(json.dumps({"wheel_cache_key": cache_key}), encoding="utf-8")
    staging = data_root / "qualification" / "wheel-cache" / f".staging-{cache_key}-123"
    staging.mkdir()
    invalid_key = "f" * 64
    invalid = data_root / "qualification" / "wheel-cache" / invalid_key
    invalid.mkdir()
    (invalid / "cache-manifest.json").write_text("{}", encoding="utf-8")
    unknown = data_root / "qualification" / "wheel-cache" / "unexpected-entry"
    unknown.mkdir()
    report_path = tmp_path / "inventory.json"
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), "-ReportPath", str(report_path), "-AsrDataRoot", str(data_root)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    inventory = json.loads(report_path.read_text(encoding="utf-8-sig"))["faster_whisper_wheel_cache_inventory"]
    entries = {item["name"]: item for item in inventory["entries"]}
    assert entries[cache_key]["integrity_status"] == "valid"
    assert "qualification-evidence-reference" in entries[cache_key]["advisory_reasons"]
    assert entries[staging.name]["kind"] == "staging"
    assert entries[staging.name]["advisory_status"] == "protected"
    assert entries[invalid_key]["integrity_status"] == "invalid"
    assert "cache-contract-invalid" in entries[invalid_key]["advisory_reasons"]
    assert entries[unknown.name]["kind"] == "unknown"
    assert entries[unknown.name]["advisory_status"] == "protected"
