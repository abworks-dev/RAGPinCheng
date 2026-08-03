[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = "${PRODUCTION_SERVICE_ROOT}\RAGPinCheng-ASR",
    [string]$DataRoot = "${PRODUCTION_DATA_ROOT}\RAGPinCheng-ASR",
    [switch]$InstallDependencies,
    [switch]$ActivateService
)

$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$actualSha = (& git -C $resolvedSource rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualSha -ne $CommitSha.ToLowerInvariant()) {
    throw "Checked-out commit does not match the requested full SHA"
}

$appRoot = Join-Path $ProgramRoot "app"
$venvRoot = Join-Path $ProgramRoot "venv"
$scriptRoot = Join-Path $ProgramRoot "scripts"
$configRoot = Join-Path $DataRoot "config"
$backupRoot = Join-Path $DataRoot "backups"
foreach ($path in @(
    $ProgramRoot,
    $DataRoot,
    $configRoot,
    $backupRoot,
    (Join-Path $DataRoot "models"),
    (Join-Path $DataRoot "spool"),
    (Join-Path $DataRoot "logs"),
    $scriptRoot
)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$staging = Join-Path $ProgramRoot ("app-staging-" + $CommitSha)
if (Test-Path -LiteralPath $staging) {
    throw "Staging directory already exists: $staging"
}
New-Item -ItemType Directory -Path $staging | Out-Null
foreach ($item in @("asr_service", "src")) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource $item) -Destination $staging -Recurse
}
Copy-Item -LiteralPath (Join-Path $resolvedSource "asr_service\requirements-windows.txt") -Destination $staging
Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\start-asr-service.ps1") -Destination $scriptRoot -Force
Copy-Item -LiteralPath (Join-Path $resolvedSource "scripts\verify-asr-service.ps1") -Destination $scriptRoot -Force
Set-Content -LiteralPath (Join-Path $staging "DEPLOYED_COMMIT") -Value $CommitSha.ToLowerInvariant() -Encoding ascii

$envFile = Join-Path $configRoot "asr.env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $resolvedSource "asr_service\.env.example") -Destination $envFile
}
if (-not [string]::IsNullOrWhiteSpace($env:ASR_SERVICE_TOKEN)) {
    if ($env:ASR_SERVICE_TOKEN.Contains("`r") -or $env:ASR_SERVICE_TOKEN.Contains("`n")) {
        throw "ASR_SERVICE_TOKEN must be one line"
    }
    $lines = Get-Content -LiteralPath $envFile -Encoding UTF8
    $replaced = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match '^ASR_SERVICE_TOKEN=') {
            $replaced = $true
            "ASR_SERVICE_TOKEN=$env:ASR_SERVICE_TOKEN"
        } else {
            $_
        }
    }
    if (-not $replaced) { $lines += "ASR_SERVICE_TOKEN=$env:ASR_SERVICE_TOKEN" }
    Set-Content -LiteralPath $envFile -Value $lines -Encoding utf8
}
& icacls.exe $configRoot /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null

if ($InstallDependencies) {
    if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
        py -3.11 -m venv $venvRoot
    }
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    & $venvPython -m pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.7.0 torchaudio==2.7.0
    if ($LASTEXITCODE -ne 0) { throw "CUDA Torch installation failed" }
    & $venvPython -m pip install -r (Join-Path $staging "requirements-windows.txt")
    if ($LASTEXITCODE -ne 0) { throw "ASR dependency installation failed" }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "ASR dependency check failed" }
}

$backup = $null
try {
    if (Test-Path -LiteralPath $appRoot) {
        $backup = Join-Path $backupRoot ("app-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
        Move-Item -LiteralPath $appRoot -Destination $backup
    }
    Move-Item -LiteralPath $staging -Destination $appRoot

    if ($ActivateService) {
        $python = Join-Path $venvRoot "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python)) { throw "ASR venv is missing" }
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $scriptRoot "start-asr-service.ps1"))
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
    }
} catch {
    if (Test-Path -LiteralPath $appRoot) {
        $failed = Join-Path $backupRoot ("failed-app-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $CommitSha.Substring(0, 12))
        Move-Item -LiteralPath $appRoot -Destination $failed
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $appRoot
    }
    throw
}

Write-Host "Repository payload deployed for commit $CommitSha. InstallDependencies=$InstallDependencies ActivateService=$ActivateService"
