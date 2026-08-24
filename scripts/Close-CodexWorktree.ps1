[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory=$true)][string]$WorktreePath,[switch]$Force)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$path=(Resolve-Path $WorktreePath).Path
$metaPath=Join-Path $path '.codex-worktree.json'
if (-not (Test-Path $metaPath)) { throw 'Missing .codex-worktree.json; refusing cleanup' }
$meta=Get-Content $metaPath -Raw | ConvertFrom-Json
if ($meta.status -eq 'closed') { throw 'Worktree is already closed' }
$status=@(& git -C $path status --porcelain)
if ($status.Count -gt 0 -and -not $Force) { throw 'Worktree has changes; use -Force only after review' }
$repo=$meta.repository
if ($PSCmdlet.ShouldProcess($path,'Remove registered worktree')) { & git -C $repo worktree remove $path $(if($Force){'--force'}); if($LASTEXITCODE -ne 0){throw 'git worktree remove failed'}; & git -C $repo worktree prune }
