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

# ── 3. Pull latest code ────────────────────────────────────────────────────
Write-Step "Pulling latest code"
Set-Location $RepositoryPath
git config --global --add safe.directory $RepositoryPath 2>$null
# Use GitHub Actions token for auth (available as GITHUB_TOKEN env var)
$gitToken = $env:GIT_TOKEN
if ($gitToken) {
    git remote set-url origin "https://x-access-token:${gitToken}@github.com/abworks-dev/RAGPinCheng.git" 2>$null
}
git pull origin master 2>&1

# ── 4. Update dependencies ─────────────────────────────────────────────────
Write-Step "Updating Python dependencies"
& $PipExe install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$ServiceDir\requirements.txt" 2>&1 | Out-Null

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
    $pid = $existing.ToString().Trim().Split()[-1]
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Start the service in a new background process
$logFile = "$RepositoryPath\gpu_service.log"
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $PythonExe
$psi.Arguments = "-m gpu_service.app"
$psi.WorkingDirectory = $RepositoryPath
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.EnvironmentVariables["GPU_SERVICE_TOKEN"] = $Token
$psi.EnvironmentVariables["HF_ENDPOINT"] = $env:HF_ENDPOINT
$process = [System.Diagnostics.Process]::Start($psi)
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
    Write-Error "GPU service health check failed"
    exit 1
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