[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RepositoryPath,[string]$OutputPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=(Resolve-Path $RepositoryPath).Path
$items=@()
$raw=@(& git -C $repo worktree list --porcelain)
$paths=@($raw | Where-Object {$_ -like 'worktree *'} | ForEach-Object {$_.Substring(9)})
foreach($path in $paths){
  $common=(& git -C $path rev-parse --git-common-dir); if(-not [IO.Path]::IsPathRooted($common)){$common=[IO.Path]::GetFullPath((Join-Path $path $common))}
  $key=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes(([IO.Path]::GetFullPath($path)).ToLowerInvariant()))).ToLowerInvariant()
  $metaPath=Join-Path (Join-Path $common 'codex-worktrees') "$key.json"; $meta=$null; $metadataStatus='missing'
  if(Test-Path $metaPath){try{$meta=Get-Content $metaPath -Raw | ConvertFrom-Json; $metadataStatus='valid'}catch{$metadataStatus='invalid'}}
  $changes=@(& git -C $path status --porcelain 2>$null)
  $items += [ordered]@{path=$path; branch=(& git -C $path branch --show-current); metadata_status=$metadataStatus; task_id=if($meta){$meta.task_id}else{$null}; status=if($meta){$meta.status}else{$null}; dirty=($changes.Count -gt 0); change_count=$changes.Count; orphan=($metadataStatus -ne 'valid' -or ($meta -and $meta.status -eq 'closed'))}
}
$report=[ordered]@{schema_version=1; generated_at=[DateTime]::UtcNow.ToString('o'); repository=$repo; worktrees=$items; orphan_count=@($items|Where-Object {$_.orphan}).Count}
$json=$report|ConvertTo-Json -Depth 8
if($OutputPath){$json|Set-Content -LiteralPath $OutputPath -Encoding UTF8}; $json
