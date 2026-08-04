[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$CommitSha,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ProgramRoot = "D:\Services\RAGPinCheng-ASR",
    [string]$DataRoot = "D:\ServiceData\RAGPinCheng-ASR",
    [switch]$InstallDependencies,
    [switch]$ActivateService
)

$ErrorActionPreference = "Stop"
$taskName = "RAGPinCheng-ASR"

function Get-MachinePython311 {
    $candidates = @()
    foreach ($registryPath in @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Python\PythonCore\3.11\InstallPath",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath"
    )) {
        if (-not (Test-Path -LiteralPath $registryPath)) { continue }
        $registryKey = Get-Item -LiteralPath $registryPath
        $executablePath = $registryKey.GetValue("ExecutablePath")
        if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
            $candidates += [string]$executablePath
        }
        $installRoot = $registryKey.GetValue("")
        if (-not [string]::IsNullOrWhiteSpace($installRoot)) {
            $candidates += Join-Path ([string]$installRoot) "python.exe"
        }
    }
    foreach ($programFilesRoot in @($env:ProgramW6432, $env:ProgramFiles)) {
        if (-not [string]::IsNullOrWhiteSpace($programFilesRoot)) {
            $candidates += Join-Path $programFilesRoot "Python311\python.exe"
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $versionOutput = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and ([string]$versionOutput).Trim() -eq "3.11") {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Machine-wide Python 3.11 was not found in HKLM or Program Files"
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$safeDirectory = $resolvedSource.Replace("\", "/")
$actualShaOutput = & git -c "safe.directory=$safeDirectory" -C $resolvedSource rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($actualShaOutput)) {
    throw "Unable to read the checked-out commit SHA"
}
$actualSha = ([string]$actualShaOutput).Trim()
if ($actualSha -ne $CommitSha.ToLowerInvariant()) {
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

function Move-StagingToBackup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet("stale", "failed")]
        [string]$Reason
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $destination = Join-Path $backupRoot (("{0}-staging-{1}-{2}" -f $Reason, (Get-Date -Format "yyyyMMdd-HHmmssfff"), $CommitSha.Substring(0, 12)))
    Move-Item -LiteralPath $Path -Destination $destination
    Write-Host "Archived $Reason staging directory to $destination"
}

$staging = Join-Path $ProgramRoot ("app-staging-" + $CommitSha)
if (Test-Path -LiteralPath $staging) {
    Move-StagingToBackup -Path $staging -Reason "stale"
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
& icacls.exe $configRoot `
    /inheritance:r `
    /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-20:(OI)(CI)M" | Out-Null

if ($InstallDependencies) {
    try {
        $venvPython = Join-Path $venvRoot "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            $python311 = Get-MachinePython311
            & $python311 -m venv $venvRoot
            if ($LASTEXITCODE -ne 0) { throw "ASR venv creation failed" }
        }
        $venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0 -or ([string]$venvVersion).Trim() -ne "3.11") {
            throw "ASR venv must use Python 3.11"
        }
        & $venvPython -m pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.7.0 torchaudio==2.7.0
        if ($LASTEXITCODE -ne 0) { throw "CUDA Torch installation failed" }
        & $venvPython -m pip install -r (Join-Path $staging "requirements-windows.txt")
        if ($LASTEXITCODE -ne 0) { throw "ASR dependency installation failed" }
        & $venvPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw "ASR dependency check failed" }
    } catch {
        Move-StagingToBackup -Path $staging -Reason "failed"
        throw
    }
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
