<#
.SYNOPSIS
    Deploy GPU Inference Service on the Windows GPU host.
    Called by GitHub Actions (deploy-gpu job).
.DESCRIPTION
    Pulls latest code, backs up the current service, updates dependencies,
    restarts the service, and runs health checks.
#>

param(
    [Parameter(Mandatory)]
    [string]$RepositoryPath,

    [Parameter(Mandatory)]
    [string]$BackupDirectory,

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,

    [string]$ProxyUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Python path — NETWORK SERVICE account doesn't inherit user PATH,
# so use the full absolute path.
$PythonExe = "${PRODUCTION_PYTHON_PATH}"
$PipExe = "C:\Program Files\Python310\Scripts\pip.exe"

# ── Resolve paths ───────────────────────────────────────────────────────────
$ServiceDir = Join-Path $RepositoryPath "gpu_service"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupPath = Join-Path $BackupDirectory "gpu-service-backup-$Timestamp"

# ── Helpers ─────────────────────────────────────────────────────────────────
function Write-Step {
    param([string]$Message)
    Write-Host "`n>> $Message" -ForegroundColor Cyan
}

# ── 1. Check prerequisites ─────────────────────────────────────────────────
Write-Step "Checking prerequisites"

if (-not (Test-Path $RepositoryPath)) {
    throw "Repository path not found: $RepositoryPath"
}
if (-not (Test-Path $ServiceDir)) {
    throw "GPU service directory not found: $ServiceDir"
}
if (-not (Test-Path $PythonExe)) {
    throw "Python not found at $PythonExe"
}

# ── 2. Backup current service ──────────────────────────────────────────────
Write-Step "Backing up current service to $BackupPath"
New-Item -ItemType Directory -Force -Path $BackupPath | Out-Null
Copy-Item -Recurse -Force "$ServiceDir\*" -Destination $BackupPath
Copy-Item -Force "$RepositoryPath\requirements-gpu.txt" -Destination $BackupPath

# ── 3. Synchronize exact approved commit ───────────────────────────────────
Write-Step "Synchronizing exact commit $CommitSha"
Set-Location $RepositoryPath
git config --global --add safe.directory $RepositoryPath 2>$null
$oldPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$headBefore = git rev-parse HEAD 2>&1
$headExitCode = $LASTEXITCODE
if ($headExitCode -ne 0) {
    $ErrorActionPreference = $oldPref
    throw "Unable to read repository HEAD: $headBefore"
}

if ($headBefore.Trim() -ne $CommitSha.ToLowerInvariant()) {
    $gitToken = $env:GIT_TOKEN
    if (-not $gitToken) {
        $ErrorActionPreference = $oldPref
        throw "GIT_TOKEN is required when the runner checkout is not already at CommitSha"
    }
    $basic = [Convert]::ToBase64String(
        [Text.Encoding]::ASCII.GetBytes("x-access-token:$gitToken")
    )
    $proxyArgs = @()
    if ($ProxyUrl) {
        $proxyArgs = @("-c", "http.proxy=$ProxyUrl")
    }
    $fetched = $false
    $gitOutput = @()
    foreach ($attempt in 1..4) {
        $gitArgs = @("-c", "http.version=HTTP/1.1") + $proxyArgs + @(
            "-c", "http.extraHeader=AUTHORIZATION: basic $basic", "fetch",
            "https://github.com/abworks-dev/RAGPinCheng.git", $CommitSha
        )
        $gitOutput = & git @gitArgs 2>&1
        $gitExitCode = $LASTEXITCODE
        if ($gitExitCode -eq 0) {
            $fetched = $true
            break
        }
        if ($attempt -lt 4) {
            $delay = [math]::Pow(2, $attempt)
            Write-Warning "git fetch attempt $attempt/4 failed; retrying in ${delay}s"
            Start-Sleep -Seconds $delay
        }
    }
    if (-not $fetched) {
        $ErrorActionPreference = $oldPref
        throw "git fetch failed after 4 attempts (last exit $gitExitCode): $gitOutput"
    }
    $gitOutput = git merge --ff-only $CommitSha 2>&1
    $gitExitCode = $LASTEXITCODE
    if ($gitExitCode -ne 0) {
        $ErrorActionPreference = $oldPref
        throw "git fast-forward failed (exit $gitExitCode): $gitOutput"
    }
}
$headAfter = (git rev-parse HEAD 2>&1).Trim()
$verifyExitCode = $LASTEXITCODE
$ErrorActionPreference = $oldPref
if ($verifyExitCode -ne 0 -or $headAfter -ne $CommitSha.ToLowerInvariant()) {
    throw "Deployed HEAD mismatch: expected $CommitSha, found $headAfter"
}

# ── 4. Update dependencies ─────────────────────────────────────────────────
Write-Step "Updating Python dependencies"
$oldPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# Read requirements with UTF-8 to avoid GBK decode issues, write to temp ASCII file
$reqContent = Get-Content -Path "$ServiceDir\requirements.txt" -Encoding UTF8 | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' }
$tempReq = Join-Path $env:TEMP "gpu_reqs_$([System.IO.Path]::GetRandomFileName())"
$reqContent | Out-File -FilePath $tempReq -Encoding ASCII
$pipResult = & $PipExe install -i https://pypi.tuna.tsinghua.edu.cn/simple -r $tempReq 2>&1
$pipExitCode = $LASTEXITCODE
Remove-Item -Force $tempReq -ErrorAction SilentlyContinue
$ErrorActionPreference = $oldPref
if ($pipExitCode -ne 0) {
    Write-Warning "pip install failed (exit $pipExitCode): $pipResult"
}

# ── 5. Create/update .env for GPU service ──────────────────────────────────
Write-Step "Configuring GPU service"
$EnvFile = "$ServiceDir\.env"
$Token = $env:GPU_SERVICE_TOKEN
if (-not $Token) {
    Write-Warning "GPU_SERVICE_TOKEN not set in environment; generating a new one"
    $Token = & $PythonExe -c "import secrets; print(secrets.token_hex(32))"
}
@"
GPU_SERVICE_TOKEN=$Token
HOST=${PRIVATE_IPV4}
PORT=8100
LOG_LEVEL=INFO
"@ | Set-Content -Path $EnvFile -Encoding UTF8

# ── 6. Restart service ─────────────────────────────────────────────────────
Write-Step "Restarting GPU service"
# Stop any existing process on port 8100
$existing = netstat -ano | findstr ":8100 " | Select-String "LISTENING"
if ($existing) {
    $processId = $existing.ToString().Trim().Split()[-1]
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Start the service in a detached background process. GitHub Actions tags child
# processes with RUNNER_TRACKING_ID and removes them at job cleanup, so the
# long-running service must not inherit that marker. Do not let the service
# inherit the short-lived checkout credential either.
$logFile = "$RepositoryPath\gpu_service.log"
$errorLogFile = "$RepositoryPath\gpu_service.error.log"
$runnerTrackingId = [Environment]::GetEnvironmentVariable(
    "RUNNER_TRACKING_ID",
    [EnvironmentVariableTarget]::Process
)
$gitTokenForRestore = [Environment]::GetEnvironmentVariable(
    "GIT_TOKEN",
    [EnvironmentVariableTarget]::Process
)
try {
    [Environment]::SetEnvironmentVariable(
        "RUNNER_TRACKING_ID",
        $null,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        "GIT_TOKEN",
        $null,
        [EnvironmentVariableTarget]::Process
    )
    $process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList @("-m", "gpu_service.app") `
        -WorkingDirectory $RepositoryPath `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError $errorLogFile `
        -PassThru
} finally {
    [Environment]::SetEnvironmentVariable(
        "RUNNER_TRACKING_ID",
        $runnerTrackingId,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        "GIT_TOKEN",
        $gitTokenForRestore,
        [EnvironmentVariableTarget]::Process
    )
}
Start-Sleep -Seconds 5

# ── 7. Health check ───────────────────────────────────────────────────────
Write-Step "Running health checks"
$retries = 12
$healthy = $false
for ($i = 1; $i -le $retries; $i++) {
    try {
        $response = & curl.exe -s http://${PRIVATE_IPV4}:8100/health 2>&1
        if ($response -match '"ok"') {
            $healthy = $true
            break
        }
    } catch {}
    Write-Host "  Waiting for service... attempt $i/$retries"
    Start-Sleep -Seconds 5
}

if (-not $healthy) {
    Write-Warning "GPU service health check failed; recent service logs follow"
    foreach ($path in @($logFile, $errorLogFile)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host "`n--- $path (last 120 lines) ---"
            Get-Content -LiteralPath $path -Tail 120
        }
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    throw "GPU service health check failed"
}

# ── 8. Smoke test ──────────────────────────────────────────────────────────
Write-Step "Running smoke tests"
# Test embedding
$embedResult = & $PythonExe -c @"
import requests, json
r = requests.post('http://${PRIVATE_IPV4}:8100/v1/embeddings',
    headers={'Authorization': 'Bearer $Token', 'Content-Type': 'application/json'},
    json={'texts': ['test']})
assert r.status_code == 200, f'embedding returned {r.status_code}'
data = r.json()
assert len(data['embeddings']) == 1
assert len(data['embeddings'][0]['dense']) == 1024
print('embedding OK')
"@ 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Smoke test failed: $embedResult"
    exit 1
}
Write-Host "  $embedResult"

# Test rerank
$rerankResult = & $PythonExe -c @"
import requests, json
r = requests.post('http://${PRIVATE_IPV4}:8100/v1/rerank',
    headers={'Authorization': 'Bearer $Token', 'Content-Type': 'application/json'},
    json={'query': 'test', 'passages': ['a', 'b']})
assert r.status_code == 200, f'rerank returned {r.status_code}'
data = r.json()
assert len(data['scores']) == 2
print('rerank OK')
"@ 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Smoke test failed: $rerankResult"
    exit 1
}
Write-Host "  $rerankResult"

Write-Step "GPU service deployed successfully"
exit 0
