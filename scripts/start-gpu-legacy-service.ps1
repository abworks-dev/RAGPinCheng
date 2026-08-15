[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepositoryPath,
    [Parameter(Mandatory)][string]$BasePython,
    [Parameter(Mandatory)][string]$ModelCacheSource,
    [Parameter(Mandatory)][string]$LogRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$envFile = Join-Path $RepositoryPath "services\gpu_service\.env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw "GPU service environment file is missing" }
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process") }
}
$env:HF_HOME = $ModelCacheSource; $env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
Set-Location -LiteralPath $RepositoryPath
$process = Start-Process -FilePath $BasePython -ArgumentList @("-X", "utf8", "-m", "gpu_service.app") `
    -WorkingDirectory $RepositoryPath -RedirectStandardOutput (Join-Path $LogRoot "gpu-service.stdout.log") `
    -RedirectStandardError (Join-Path $LogRoot "gpu-service.stderr.log") -NoNewWindow -Wait -PassThru
exit $process.ExitCode
