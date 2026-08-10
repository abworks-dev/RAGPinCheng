Set-StrictMode -Version Latest

function Get-AsrContractSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-AsrRuntimeContractSpec {
    param([Parameter(Mandatory = $true)][ValidateSet("faster-whisper", "qwen3-asr", "whisperx")][string]$Engine)
    switch ($Engine) {
        "faster-whisper" {
            return [pscustomobject][ordered]@{
                engine = $Engine; model_revisions = @("0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")
                paths = @("asr_service/requirements-service-core.txt", "asr_service/requirements-windows.txt", "asr_service/requirements-faster-whisper.txt", "asr_service/app.py", "asr_service/model_cache.py", "src/transcription/profile_catalog.py", "scripts/qualify-faster-whisper-production.ps1", "scripts/run_faster_whisper_qualification.py")
            }
        }
        "qwen3-asr" {
            return [pscustomobject][ordered]@{
                engine = $Engine; model_revisions = @("5eb144179a02acc5e5ba31e748d22b0cf3e303b0", "c7cbfc2048c462b0d63a45797104fc9db3ad62b7")
                paths = @("asr_service/requirements-service-core.txt", "asr_service/requirements-windows.txt", "asr_service/app.py", "src/transcription/profile_catalog.py", "scripts/qualify-qwen3-asr-production.ps1", "scripts/run_qwen3_asr_qualification.py")
            }
        }
        "whisperx" {
            return [pscustomobject][ordered]@{
                engine = $Engine; model_revisions = @("53ecf83a5bedc5597eb8c8b34eac29e5345520ff", "51d27579a1040ee4e967979278d5f76b9c32c375")
                paths = @("asr_service/requirements-service-core.txt", "asr_service/requirements-windows.txt", "asr_service/app.py", "src/transcription/profile_catalog.py", "scripts/qualify-whisperx-production.ps1", "scripts/run_whisperx_runtime_preflight.py")
            }
        }
    }
}

function Get-AsrGitBlobId {
    param([Parameter(Mandatory = $true)][string]$SourceRoot, [Parameter(Mandatory = $true)][string]$CommitSha, [Parameter(Mandatory = $true)][string]$Path)
    $result = & git -C $SourceRoot rev-parse "$CommitSha`:$Path"
    if ($LASTEXITCODE -ne 0 -or ([string]$result).Trim() -notmatch '^[0-9a-f]{40}$') { throw "Unable to resolve ASR contract source: $Path" }
    return ([string]$result).Trim().ToLowerInvariant()
}

function Get-AsrRuntimeContract {
    param([Parameter(Mandatory = $true)][ValidateSet("faster-whisper", "qwen3-asr", "whisperx")][string]$Engine, [Parameter(Mandatory = $true)][string]$SourceRoot, [Parameter(Mandatory = $true)][string]$CommitSha)
    if ($CommitSha -notmatch '^[0-9a-fA-F]{40}$') { throw "ASR runtime contract requires a full commit SHA" }
    $spec = Get-AsrRuntimeContractSpec -Engine $Engine
    $files = @($spec.paths | Sort-Object | ForEach-Object { [ordered]@{ path = $_; git_blob_sha = Get-AsrGitBlobId -SourceRoot $SourceRoot -CommitSha $CommitSha -Path $_ } })
    $manifest = [ordered]@{ schema_version = "asr-runtime-contract/1"; engine = $Engine; model_revisions = @($spec.model_revisions); files = $files }
    $canonical = $manifest | ConvertTo-Json -Depth 8 -Compress
    return [pscustomobject][ordered]@{ schema_version = "asr-runtime-contract/1"; engine = $Engine; source_commit_sha = $CommitSha.ToLowerInvariant(); runtime_contract_sha256 = Get-AsrContractSha256 -Text $canonical; manifest = $manifest }
}

function Get-AsrProductionAdmissionAdapter {
    param([Parameter(Mandatory = $true)][ValidateSet("faster-whisper", "qwen3-asr", "whisperx")][string]$Engine)
    switch ($Engine) {
        "faster-whisper" {
            return [pscustomobject][ordered]@{ engine = $Engine; enabled = $true; evidence_adapter = "faster-whisper-r3/1" }
        }
        default {
            return [pscustomobject][ordered]@{ engine = $Engine; enabled = $false; evidence_adapter = "" }
        }
    }
}

function Get-AsrDeploymentContract {
    param([Parameter(Mandatory = $true)][string]$SourceRoot, [Parameter(Mandatory = $true)][string]$CommitSha)
    $paths = @("scripts/asr-contract.ps1", "scripts/deploy-asr.ps1", "scripts/faster-whisper-production-evidence.ps1", "scripts/preflight-asr-deployment.ps1", "scripts/windows-wheel-cache.ps1", ".github/workflows/deploy-asr-production.yml", ".github/workflows/preflight-asr-production.yml")
    $files = @($paths | Sort-Object | ForEach-Object { [ordered]@{ path = $_; git_blob_sha = Get-AsrGitBlobId -SourceRoot $SourceRoot -CommitSha $CommitSha -Path $_ } })
    $manifest = [ordered]@{ schema_version = "asr-deployment-contract/1"; files = $files }
    return [pscustomobject][ordered]@{ schema_version = "asr-deployment-contract/1"; source_commit_sha = $CommitSha.ToLowerInvariant(); deployment_contract_sha256 = Get-AsrContractSha256 -Text ($manifest | ConvertTo-Json -Depth 8 -Compress); manifest = $manifest }
}
