[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory=$true)][string]$RepositoryPath,
    [Parameter(Mandatory=$true)][string]$WorktreePath,
    [Parameter(Mandatory=$true)][string]$Branch,
    [Parameter(Mandatory=$true)][ValidateSet('New','Continue')][string]$Intent,
    [string]$TaskId = ([guid]::NewGuid().ToString('N')),
    [string]$BaseRef = 'origin/master'
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=(Resolve-Path $RepositoryPath).Path
$target=[IO.Path]::GetFullPath($WorktreePath)
if ($Branch -notmatch '^codex/[A-Za-z0-9._/-]+$') { throw 'Branch must use codex/* prefix' }
$primary=((& git -C $repo worktree list --porcelain | Select-Object -First 1) -replace '^worktree ','')
$approvedRoot=Join-Path (Join-Path (Split-Path $primary -Parent) '.worktrees') (Split-Path $primary -Leaf)
if (-not $target.StartsWith($approvedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Worktree path is outside approved .worktrees root' }
if (Test-Path $target) { throw "Worktree path already exists: $target" }
if (-not $PSCmdlet.ShouldProcess($target,"Create worktree branch $Branch from $BaseRef")) {
  return ([ordered]@{status='preview'; repository=$repo; path=$target; branch=$Branch; base_ref=$BaseRef}|ConvertTo-Json)
}
& git -C $repo worktree add -b $Branch $target $BaseRef
if ($LASTEXITCODE -ne 0) { throw 'git worktree add failed' }
$meta=[ordered]@{schema_version=1; task_id=$TaskId; repository=$repo; path=$target; branch=$Branch; intent=$Intent; base_ref=$BaseRef; created_at=[DateTime]::UtcNow.ToString('o'); status='active'}
$common=(& git -C $repo rev-parse --git-common-dir)
if(-not [IO.Path]::IsPathRooted($common)){$common=[IO.Path]::GetFullPath((Join-Path $repo $common))}
$metaRoot=Join-Path $common 'codex-worktrees'; New-Item -ItemType Directory -Force $metaRoot|Out-Null
$key=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($target.ToLowerInvariant()))).ToLowerInvariant()
$meta | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $metaRoot "$key.json") -Encoding UTF8
& pwsh -NoProfile -File (Join-Path $target 'scripts/Test-CodexWorkspace.ps1') -Mode Write -Intent $Intent -ExpectedBranch $Branch
if ($LASTEXITCODE -ne 0) { throw 'Write preflight failed; worktree retained for inspection' }
$meta | ConvertTo-Json -Depth 5
