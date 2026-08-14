from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import asr_local_lab as lab
from scripts import asr_model_download as model_download


@dataclass(frozen=True)
class Sample:
    sample_id: str
    scenario: str


@dataclass(frozen=True)
class Manifest:
    samples: tuple[Sample, ...]


def test_initialize_creates_exact_marker_and_allowlisted_directories(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "lab"
    monkeypatch.setattr(lab, "_is_elevated", lambda: False)

    result = lab.initialize_lab(root, source)

    assert result["status"] == "initialized"
    marker = json.loads((root / lab.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker == {
        "schema_version": lab.LAB_SCHEMA_VERSION,
        "source_root": str(source.resolve()),
    }
    assert (root / "envs/qwen3-asr").is_dir()
    assert (root / "envs/whisperx").is_dir()
    assert (root / "envs/lab-tools").is_dir()
    assert (root / "caches/cuda").is_dir()


def test_initialize_refuses_elevation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(lab, "_is_elevated", lambda: True)

    with pytest.raises(lab.LabConfigurationError, match="without elevation"):
        lab.initialize_lab(tmp_path / "lab", source)


def test_initialize_refuses_to_adopt_nonempty_unmarked_root(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "existing"
    root.mkdir()
    (root / "unrelated.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(lab, "_is_elevated", lambda: False)

    with pytest.raises(lab.LabConfigurationError, match="refusing to adopt"):
        lab.initialize_lab(root, source)


def test_paths_used_by_evaluation_must_remain_inside_lab(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()

    assert lab.require_inside_lab(root / "runs/report.json", root, "report") == (
        root / "runs/report.json"
    ).resolve()
    with pytest.raises(lab.LabConfigurationError, match="inside the lab root"):
        lab.require_inside_lab(tmp_path / "outside.json", root, "report")


def test_paths_reject_nested_reparse_components(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    nested = root / "runs" / "redirected"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        lab, "_is_reparse_point", lambda path: path == nested
    )

    with pytest.raises(lab.LabConfigurationError, match="reparse point"):
        lab.require_inside_lab(nested / "report.json", root, "report")


def test_direct_unprivileged_guard_refuses_elevation(monkeypatch):
    monkeypatch.setattr(lab, "_is_elevated", lambda: True)

    with pytest.raises(lab.LabConfigurationError, match="without elevation"):
        lab.ensure_unprivileged()


def test_lab_and_source_roots_must_be_disjoint(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(lab.LabConfigurationError, match="disjoint"):
        lab.validate_lab_root(source / "lab", source)
    with pytest.raises(lab.LabConfigurationError, match="disjoint"):
        lab.validate_lab_root(tmp_path, source)


def test_marker_binds_lab_to_one_source_root(tmp_path, monkeypatch):
    source = tmp_path / "source"
    other = tmp_path / "other"
    source.mkdir()
    other.mkdir()
    root = tmp_path / "lab"
    monkeypatch.setattr(lab, "_is_elevated", lambda: False)
    lab.initialize_lab(root, source)

    with pytest.raises(lab.LabConfigurationError, match="source root"):
        lab.load_marker(root, other)


def test_process_environment_redirects_known_caches_inside_lab(tmp_path):
    values = lab.process_environment(tmp_path)
    for name in (
        "PYTHONPYCACHEPREFIX",
        "PIP_CACHE_DIR",
        "HF_HOME",
        "HF_HUB_CACHE",
        "TORCH_HOME",
        "TORCH_EXTENSIONS_DIR",
        "CUDA_CACHE_PATH",
        "NLTK_DATA",
        "TEMP",
        "TMP",
    ):
        assert Path(values[name]).is_relative_to(tmp_path.resolve())
    assert values["PYTHONNOUSERSITE"] == "1"


def test_hugging_face_origin_override_is_process_scoped(monkeypatch):
    original = socket.getaddrinfo
    calls = []

    def fake_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setenv(lab.HF_ORIGIN_IP_ENV, "18.65.14.100")
    with lab.hugging_face_origin_override():
        socket.getaddrinfo("huggingface.co", 443)
        socket.getaddrinfo("example.com", 443)

    assert calls == ["18.65.14.100", "example.com"]
    assert socket.getaddrinfo is fake_getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", original)


def test_hugging_face_origin_override_rejects_nonpublic_ip(monkeypatch):
    monkeypatch.setenv(lab.HF_ORIGIN_IP_ENV, "127.0.0.1")

    with pytest.raises(lab.LabConfigurationError, match="must be public"):
        with lab.hugging_face_origin_override():
            pass


def test_curl_snapshot_download_is_pinned_filtered_and_path_safe(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(lab.HF_ORIGIN_IP_ENV, "18.65.14.100")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if "/api/models/" in command[-1]:
            payload = json.dumps(
                {
                    "siblings": [
                        {"rfilename": "config.json"},
                        {"rfilename": "model.bin"},
                        {"rfilename": "README.md"},
                    ]
                }
            )
            return subprocess.CompletedProcess(
                command,
                0,
                payload + "\n__URL__https://huggingface.co/api/models/test",
                "",
            )
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"model")
        return subprocess.CompletedProcess(
            command, 0, "https://cdn-lfs.huggingface.co/file", ""
        )

    monkeypatch.setattr(lab.subprocess, "run", run)
    target = tmp_path / "snapshot"
    result = lab.curl_snapshot_download(
        repo_id="owner/model",
        revision="a" * 40,
        local_dir=str(target),
        local_dir_use_symlinks=False,
        allow_patterns=["*.json", "*.bin"],
        max_workers=1,
    )

    assert result == str(target.resolve())
    assert (target / "config.json").read_bytes() == b"model"
    assert (target / "model.bin").read_bytes() == b"model"
    assert not (target / "README.md").exists()
    assert all("--proto-redir" in command for command in calls)


def test_curl_snapshot_download_resumes_partial_and_reuses_completed_file(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(model_download.HF_ORIGIN_IP_ENV, "18.65.14.100")
    calls = []
    target = tmp_path / "snapshot"
    partial = target / "model.bin.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial-")

    def run(command, **_kwargs):
        calls.append(command)
        if "/api/models/" in command[-1]:
            payload = json.dumps({"siblings": [{"rfilename": "model.bin"}]})
            return subprocess.CompletedProcess(
                command,
                0,
                payload + "\n__URL__https://huggingface.co/api/models/test",
                "",
            )
        output = Path(command[command.index("--output") + 1])
        assert output.read_bytes() == b"partial-"
        output.write_bytes(output.read_bytes() + b"complete")
        return subprocess.CompletedProcess(
            command, 0, "https://cdn-lfs.huggingface.co/file", ""
        )

    monkeypatch.setattr(model_download.subprocess, "run", run)
    arguments = {
        "repo_id": "owner/model",
        "revision": "a" * 40,
        "local_dir": str(target),
        "local_dir_use_symlinks": False,
    }

    model_download.curl_snapshot_download(**arguments)
    first_call_count = len(calls)
    model_download.curl_snapshot_download(**arguments)

    assert (target / "model.bin").read_bytes() == b"partial-complete"
    assert len(calls) == first_call_count + 1
    assert "--continue-at" in calls[1]


def test_model_staging_lock_fails_fast_for_concurrent_writer(tmp_path):
    with model_download.exclusive_staging_lock(tmp_path):
        with pytest.raises(model_download.AsrModelDownloadError, match="already in use"):
            with model_download.exclusive_staging_lock(tmp_path):
                pass


@pytest.mark.parametrize("path", ("../outside.bin", "/absolute.bin", "a\\b.bin"))
def test_curl_snapshot_path_rejects_escape(path):
    with pytest.raises(lab.LabConfigurationError, match="unsafe"):
        lab._safe_snapshot_path(path)


def test_focus_selects_one_sample_per_fixed_scenario():
    manifest = Manifest(
        (
            Sample("clear", "clear-zh"),
            Sample("codes", "standard-codes"),
            Sample("noise", "noisy-bim-zh"),
            Sample("mixed", "mixed-zh-en"),
            Sample("negative-a", "negative-control"),
            Sample("negative-b", "negative-control"),
        )
    )

    qwen = lab.select_development_samples(manifest, "qwen3-asr", "focus")
    whisperx = lab.select_development_samples(manifest, "whisperx", "focus")

    assert [item.sample_id for item in qwen.samples] == [
        "codes",
        "noise",
        "mixed",
        "negative-a",
    ]
    assert [item.sample_id for item in whisperx.samples] == [
        "codes",
        "noise",
        "negative-a",
    ]


def test_local_report_can_never_claim_qualification():
    result = lab._development_report(
        engine="qwen3-asr",
        mode="full",
        candidate_ids=["auto-zh-en"],
        result={"status": "pass"},
    )

    assert result["status"] == "complete"
    assert result["gate_status"] == "pass"
    assert result["scope"] == "local-development"
    assert result["qualification_eligible"] is False


def test_evaluation_exit_code_tracks_gate_status():
    assert lab._evaluation_exit_code("evaluate-qwen", {"gate_status": "pass"}) == 0
    assert lab._evaluation_exit_code("evaluate-qwen", {"gate_status": "fail"}) == 2
    assert lab._evaluation_exit_code("doctor", {"status": "complete"}) == 0


def test_whisperx_focus_uses_full_decode_as_target_candidate():
    target, status = lab._whisperx_local_gate_status(
        "focus",
        {"baseline": {"status": "fail"}, "full-decode": {"status": "pass"}},
    )

    assert (target, status) == ("full-decode", "pass")


def test_local_powershell_has_no_system_service_or_firewall_mutations():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/run-asr-local-lab.ps1").read_text(encoding="utf-8")
    lowered = script.lower()
    for forbidden in (
        "register-scheduledtask",
        "new-netfirewallrule",
        "remove-netfirewallrule",
        "new-service",
        "start-service",
        "stop-service",
        "setx ",
        "pip install --user",
    ):
        assert forbidden not in lowered
    assert "127.0.0.1" in script
    assert 'NO_PROXY = $localNoProxy' in script
    assert '$loopbackNoProxy = "127.0.0.1,localhost,::1"' in script
    assert "function Get-LocalServiceProcessIdentity" in script
    assert "function Stop-LocalServiceProcess" in script
    assert "creation_time_utc_ticks" in script
    assert "whose process identity changed" in script
    assert "$serviceProcess.Kill()" in script
    assert "$Launcher.Kill()" in script
    assert "Stop-Process -Id $Launcher.Id" not in script
    assert "-LauncherProcessId $process.Id" in script
    assert "function Invoke-LocalGateEvaluation" in script
    assert 'schema_version = "asr-local-run-summary/1"' in script
    assert "[Guid]::NewGuid()" in script
    assert "exit 2" in script
    assert "E:\\RAGPinCheng-ASR-Lab" in script
    assert '"--cache-dir", (Join-Path $LabRoot "caches\\pip")' in script
    assert '"torch==2.7.0+cu128"' in script
    assert '"torch==2.8.0+cu128"' in script
    assert '"requests>=2.32,<3"' in script
    assert r'"services\asr_service\requirements-service-core.txt"' in script
    assert '"antlr4-python3-runtime==4.9.3"' in script
    assert "Resolve-HuggingFaceOriginIp" in script
    assert 'HF_HUB_OFFLINE = "1"' in script


def test_local_service_explicitly_allows_scheduler_and_rejects_elevation():
    root = Path(__file__).resolve().parents[1]
    service = (root / "scripts/asr_local_service.py").read_text(encoding="utf-8")

    assert "ensure_unprivileged()" in service
    assert "FixedBgePriorityProbe(BgePriorityDecision.allow)" in service


def test_local_service_uses_nonproduction_ports():
    assert lab.ENGINE_PORTS == {"qwen3-asr": 18310, "whisperx": 18320}
    assert not set(lab.ENGINE_PORTS.values()) & set(lab.FORBIDDEN_PORTS)


def test_fixed_corpus_preparation_accepts_explicit_local_roots():
    root = Path(__file__).resolve().parents[1]
    script = (
        root / "scripts/prepare-qwen3-asr-qualification-samples.ps1"
    ).read_text(encoding="utf-8")

    assert "[string]$ProgramRoot = $env:PRODUCTION_QWEN3_ASR_QUALIFICATION_ROOT" in script
    assert "[string]$InputParent = $env:PRODUCTION_QWEN3_ASR_INPUT_ROOT" in script
    assert "[string]$MachinePythonPath = $env:PRODUCTION_PYTHON311_PATH" in script
    launcher = (root / "scripts/run-asr-local-lab.ps1").read_text(encoding="utf-8")
    assert "-ProgramRoot (Join-Path $LabRoot" in launcher
    assert "-InputParent $CorpusParent" in launcher
    assert "-MachinePythonPath $MachinePython" in launcher
