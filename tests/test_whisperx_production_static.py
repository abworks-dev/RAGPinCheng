from __future__ import annotations

import ast
from pathlib import Path

import yaml

from scripts.run_whisperx_cuda_smoke import prepare_smoke_punkt

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workflow_is_manual_immutable_and_production_scoped():
    workflow = read(".github/workflows/smoke-whisperx-production.yml")
    parsed = yaml.safe_load(workflow)
    assert "workflow_dispatch" in parsed[True]
    assert "pull_request" not in parsed[True]
    assert "push" not in parsed[True]
    assert "environment: production-asr" in workflow
    assert "runs-on: [self-hosted, Windows, X64, asr-production]" in workflow
    assert "commit_sha must equal the dispatch revision" in workflow
    assert "execute_smoke must be explicitly enabled" in workflow
    assert "scripts\\smoke-whisperx-production.ps1" in workflow


def test_windows_runner_is_isolated_and_cannot_mutate_production_controls():
    script = read("scripts/smoke-whisperx-production.ps1")
    assert all(value < 128 for value in script.encode("utf-8"))
    lowered = script.lower()
    assert "$env:PRODUCTION_WHISPERX_ROOT" in script
    assert "PRODUCTION_WHISPERX_PROGRAM_ROOT" not in script
    assert "PRODUCTION_WHISPERX_DATA_ROOT" not in script
    for child in ("models", "nltk", "qualification", "wheel-cache", "reports"):
        assert child in script
    assert "torch==2.8.0+cu128" in script
    assert "torch.__version__ == '2.8.0+cu128'" in script
    assert "whisperx==3.8.6" in script
    assert "httpx>=0.27.0" in script
    assert "RTX 5060 Ti" in script
    assert "Get-ScheduledTask" in script
    assert "Get-NetFirewallRule" in script
    for forbidden in (
        "start-scheduledtask",
        "stop-scheduledtask",
        "register-scheduledtask",
        "unregister-scheduledtask",
        "new-netfirewallrule",
        "set-netfirewallrule",
        "remove-netfirewallrule",
        "netsh advfirewall",
        "asr_enabled",
        "qdrant",
        "app.sqlite",
        "start-process",
    ):
        assert forbidden not in lowered


def test_model_and_smoke_identity_are_pinned_and_use_existing_contracts():
    runner = read("scripts/run_whisperx_cuda_smoke.py")
    engine = read("asr_service/engines/whisperx.py")
    ast.parse(runner)
    ast.parse(engine)
    assert '"pipe:0"' in engine
    assert "wave.open(BytesIO(content)" in engine
    assert "audioop.ratecv" in engine
    assert "NamedTemporaryFile" not in engine
    assert 'ASR_MODEL_ID = "Systran/faster-whisper-large-v3"' in runner
    assert 'ASR_REVISION = "53ecf83a5bedc5597eb8c8b34eac29e5345520ff"' in runner
    assert 'ALIGN_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"' in runner
    assert 'ALIGN_REVISION = "51d27579a1040ee4e967979278d5f76b9c32c375"' in runner
    assert runner.count("max_workers=1") == 2
    assert "nltk.download" not in runner
    assert "save_punkt_params(PunktParameters()" in runner
    assert '"punkt_source": "generated-default-smoke-only"' in runner
    for required in (
        "WhisperXEngine",
        "PreparedAudioChunk",
        "ProviderCandidate",
        "normalize_candidate",
        "ProfileSnapshot",
        "TranscriptionExecutionConfig",
        "validate_whisperx_cache",
        "validate_whisperx_align_cache",
        "model-manifest.json",
        "stage={stage}; type={failure_type}",
    ):
        assert required in runner
    assert 'os.environ["HF_HUB_OFFLINE"] = "1"' in runner
    assert "DiarizationPipeline" not in runner


def test_generated_smoke_punkt_is_loadable_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prepare_smoke_punkt(tmp_path)
    import nltk.data

    monkeypatch.setattr(nltk.data, "path", [str(tmp_path)])
    tokenizer = nltk.data.load("tokenizers/punkt_tab/english.pickle")
    assert list(tokenizer.span_tokenize("Synthetic sentence.")) == [(0, 19)]
