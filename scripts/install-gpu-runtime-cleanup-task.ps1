[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RepositoryPath = '${PRODUCTION_REPO_PATH}',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RuntimeRoot = '${PRODUCTION_REPO_PATH}\runtime',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'RAGPinCheng-GPU-Runtime-Cleanup',

    [Parameter()]
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$StartTime = '03:30',

    [Parameter()]
    [switch]$EnableApply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedRepository = [IO.Path]::GetFullPath('${PRODUCTION_REPO_PATH}').TrimEnd('\')
$expectedRuntime = [IO.Path]::GetFullPath('${PRODUCTION_REPO_PATH}\runtime').TrimEnd('\')
$resolvedRepository = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepositoryPath).Path).TrimEnd('\')
$resolvedRuntime = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RuntimeRoot).Path).TrimEnd('\')

if (-not $resolvedRepository.Equals($expectedRepository, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install a task outside the exact production repository: $expectedRepository"
}
if (-not $resolvedRuntime.Equals($expectedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install a task outside the exact production runtime root: $expectedRuntime"
}

$cleanupScript = Join-Path $resolvedRepository 'scripts\cleanup-gpu-runtime.ps1'
if (-not (Test-Path -LiteralPath $cleanupScript -PathType Leaf)) {
    throw "GPU runtime cleanup script is missing: $cleanupScript"
}

$taskArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $cleanupScript),
    '-RuntimeRoot', ('"{0}"' -f $resolvedRuntime),
    '-AuditPath', ('"{0}"' -f (Join-Path $resolvedRuntime 'cleanup-audit\scheduled.json'))
) -join ' '
if ($EnableApply) {
    $taskArguments += ' -Apply'
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments -WorkingDirectory $resolvedRepository
$trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::ParseExact($StartTime, 'HH:mm', $null))
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if ($PSCmdlet.ShouldProcess($TaskName, "Register GPU runtime cleanup task in $(if ($EnableApply) { 'apply' } else { 'dry-run' }) mode")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description 'Dry-run or approved retention cleanup for ${PRODUCTION_REPO_PATH}\runtime' `
        -Force | Out-Null
    Write-Host "Registered: $TaskName"
    Write-Host "Mode: $(if ($EnableApply) { 'APPLY' } else { 'DRY RUN' })"
    Write-Host "Schedule: daily at $StartTime"
}
