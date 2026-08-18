[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$ProgramRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$ReportPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'asr-release.ps1')
$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$program = [IO.Path]::GetFullPath($ProgramRoot).TrimEnd('\')
$active = Get-Content (Join-Path $data 'release-state\active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$states = @(); $refs = @{}
foreach ($path in @(Get-ChildItem -LiteralPath $BackupRoot -Filter 'candidate-activation-state.json' -File -Recurse -Force)) {
    $state = Get-Content $path.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $activation = Split-Path (Split-Path $path.FullName -Parent) -Leaf
    $ids = @('candidate_id','previous_candidate_id') | Where-Object { $state.PSObject.Properties.Name -contains $_ -and $state.$_ } | ForEach-Object { [string]$state.$_ }
    foreach ($id in $ids) { if (-not $refs.ContainsKey($id)) { $refs[$id] = @() }; $refs[$id] += $activation }
    $states += [ordered]@{ activation_id=$activation; candidate_ids=$ids; state_status=[string]$state.status }
}
$rows = @()
foreach ($candidate in @($refs.Keys | Sort-Object)) {
    $reasons = @(); $status = 'eligible'
    if ($candidate -eq [string]$active.candidate_id) { $status='protected'; $reasons += 'active-candidate' }
    if (@($refs[$candidate]).Count -gt 1) { $status='protected'; $reasons += 'multiple-activation-references' }
    $candidateStates = @($states | Where-Object { $candidate -in @($_.candidate_ids) } | ForEach-Object { $_.state_status })
    if (@($candidateStates | Where-Object { $_ -notin @('rolled-back', 'failed', 'cancelled') }).Count -gt 0) { $status='protected'; $reasons += 'non-terminal-activation-state' }
    $release = Join-Path $program "releases\$candidate"; $config = Join-Path $data "config\releases\$candidate"
    if (-not (Test-Path $release -PathType Container) -or -not (Test-Path $config -PathType Container)) { $status='protected'; $reasons += 'release-closure-incomplete' }
    try { if (Test-Path $release) { Read-AsrReleaseManifest -ProgramRoot $program -DataRoot $data -CandidateId $candidate -AllowLegacyWhisperXV1Profiles | Out-Null } } catch { $status='protected'; $reasons += 'release-contract-invalid' }
    $rows += [ordered]@{ candidate_id=$candidate; status=$status; reasons=$reasons; activation_ids=@($refs[$candidate]) }
}
$report = [ordered]@{ schema_version='asr-rollback-retirement-audit/1'; generated_at_utc=[DateTimeOffset]::UtcNow.ToString('o'); active_candidate_id=[string]$active.candidate_id; activation_states=$states; candidates=$rows }
$parent=Split-Path $ReportPath -Parent; New-Item -ItemType Directory -Path $parent -Force | Out-Null; $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "ASR_ROLLBACK_RETIREMENT_AUDIT report=$ReportPath eligible=$(@($rows|Where-Object status -eq eligible).Count) protected=$(@($rows|Where-Object status -eq protected).Count)"
