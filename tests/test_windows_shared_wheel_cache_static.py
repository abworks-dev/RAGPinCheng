from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "windows-wheel-cache.ps1"


def test_shared_cache_is_content_addressed_and_hash_validated() -> None:
    script = HELPER.read_text(encoding="utf-8")
    assert 'schema_version = $script:SharedWheelCacheSchema' in script
    assert 'Join-Path $CacheRoot "blobs"' in script
    assert "Get-FileHash -LiteralPath $Path -Algorithm SHA256" in script
    assert "Test-SharedWheelBlob" in script
    assert "Shared wheel cache content hash mismatch" in script


def test_shared_cache_uses_staging_lock_and_quarantine() -> None:
    script = HELPER.read_text(encoding="utf-8")
    assert "Global\\RAGPinCheng-ASR-shared-wheel-cache" in script
    assert "Unable to protect shared wheel cache ACL" in script
    assert "Shared wheel cache requires the fixed Administrator or SYSTEM identity" in script
    assert 'Join-Path $CacheRoot "staging"' in script
    assert 'Join-Path $CacheRoot "quarantine"' in script
    assert "Move-Item -LiteralPath $manifestStaging" in script
    assert "Shared wheel cache entry rejected; online resolution will be used" in script
    assert "Conflicting shared wheel name rejected; online resolution will be used" in script
    assert "Shared wheel cache Manifest cannot be a reparse point" in script
    assert "Shared wheel cache Manifest contains duplicate wheel names" in script


def test_shared_cache_never_executes_network_or_package_install() -> None:
    script = HELPER.read_text(encoding="utf-8").lower()
    for forbidden in (
        "invoke-webrequest",
        "invoke-restmethod",
        "pip install",
        "pip download",
        "http_proxy",
        "https_proxy",
        "asr_service_token",
    ):
        assert forbidden not in script


def test_shared_cache_consumers_remain_separate() -> None:
    helper_name = "windows-wheel-cache.ps1"
    consumers = {
        "faster-whisper": ROOT / "scripts" / "qualify-faster-whisper-production.ps1",
        "qwen3-asr": ROOT / "scripts" / "qualify-qwen3-asr-production.ps1",
        "whisperx": ROOT / "scripts" / "qualify-whisperx-production.ps1",
        "funasr": ROOT / "scripts" / "deploy-asr.ps1",
    }
    for consumer, path in consumers.items():
        script = path.read_text(encoding="utf-8")
        assert helper_name in script, consumer
        assert "Publish-SharedWheelBlobs" in script, consumer
        assert "Copy-VerifiedSharedWheelBlobs" in script, consumer


def test_engines_keep_independent_virtual_environments() -> None:
    deploy = (ROOT / "scripts" / "deploy-asr.ps1").read_text(encoding="utf-8")
    faster = (ROOT / "scripts" / "qualify-faster-whisper-production.ps1").read_text(
        encoding="utf-8"
    )
    qwen = (ROOT / "scripts" / "qualify-qwen3-asr-production.ps1").read_text(
        encoding="utf-8"
    )
    whisperx = (ROOT / "scripts" / "qualify-whisperx-production.ps1").read_text(
        encoding="utf-8"
    )
    assert 'Join-Path $ProgramRoot "venv"' in deploy
    assert 'Join-Path $RunRoot "venv"' in faster
    assert 'Join-Path $RunRoot "venv"' in qwen
    assert 'Join-Path $RunRoot "venv"' in whisperx


def test_funasr_builds_staging_venv_offline_before_atomic_swap() -> None:
    deploy = (ROOT / "scripts" / "deploy-asr.ps1").read_text(encoding="utf-8")
    build = deploy.index("& $python311 -m venv $venvStaging")
    offline = deploy.index("--no-index", build)
    stop = deploy.index("Stop-OwnedAsrService", offline)
    swap = deploy.index(
        "Move-Item -LiteralPath $venvStaging -Destination $venvRoot", stop
    )
    assert build < offline < stop < swap
    assert "ASR dependency identity verification failed" in deploy
    assert "Unable to restore the previous ASR venv" in deploy
    assert "pip install --index-url" not in deploy


def test_torch_27_is_reused_only_by_compatible_consumers() -> None:
    faster = (ROOT / "scripts" / "qualify-faster-whisper-production.ps1").read_text(
        encoding="utf-8"
    )
    qwen = (ROOT / "scripts" / "qualify-qwen3-asr-production.ps1").read_text(
        encoding="utf-8"
    )
    funasr = (ROOT / "scripts" / "deploy-asr.ps1").read_text(encoding="utf-8")
    whisperx = (ROOT / "scripts" / "qualify-whisperx-production.ps1").read_text(
        encoding="utf-8"
    )
    for script in (faster, qwen, funasr):
        assert "2.7.0+cu128" in script
        assert 'Join-Path $DataRoot "wheel-cache"' in script
    assert "2.8.0+cu128" in whisperx
    assert "2.7.0+cu128" not in whisperx
