[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ReadOnly", "Write")]
    [string]$Mode,

    [string]$RepositoryPath = (Join-Path $PSScriptRoot ".."),

    [string]$ExpectedBranch,

    [switch]$AllowNonCodexBranch,

    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-GitText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    $output = @(& git -C $Path @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "Unable to inspect Git state: git $($Arguments -join ' ')"
    }
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Lines = $output
        Text = ($output -join "`n").Trim()
    }
}

function Resolve-GitPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$GitPath
    )

    if ([System.IO.Path]::IsPathRooted($GitPath)) {
        return [System.IO.Path]::GetFullPath($GitPath)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $GitPath))
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$scriptProjectRoot = Get-NormalizedPath -Path (Join-Path $PSScriptRoot "..")

try {
    $resolvedInput = (Resolve-Path -LiteralPath $RepositoryPath).Path
    $repositoryRoot = (Invoke-GitText -Path $resolvedInput -Arguments @("rev-parse", "--show-toplevel")).Text
    $repositoryRoot = Get-NormalizedPath -Path $repositoryRoot
    $projectRoot = (Invoke-GitText -Path $scriptProjectRoot -Arguments @("rev-parse", "--show-toplevel")).Text
    $projectRoot = Get-NormalizedPath -Path $projectRoot

    $repositoryCommonRaw = (Invoke-GitText -Path $repositoryRoot -Arguments @("rev-parse", "--git-common-dir")).Text
    $projectCommonRaw = (Invoke-GitText -Path $projectRoot -Arguments @("rev-parse", "--git-common-dir")).Text
    $repositoryCommon = Get-NormalizedPath -Path (
        Resolve-GitPath -BasePath $repositoryRoot -GitPath $repositoryCommonRaw
    )
    $projectCommon = Get-NormalizedPath -Path (
        Resolve-GitPath -BasePath $projectRoot -GitPath $projectCommonRaw
    )
    $sameRepository = $repositoryCommon -ceq $projectCommon
    if (-not $sameRepository) {
        $errors.Add("RepositoryPath does not belong to this project repository.")
    }

    $worktreeLines = (Invoke-GitText -Path $projectRoot -Arguments @("worktree", "list", "--porcelain")).Lines
    $registeredPaths = @(
        $worktreeLines |
            Where-Object { $_ -like "worktree *" } |
            ForEach-Object { Get-NormalizedPath -Path $_.Substring(9) }
    )
    $isRegistered = $registeredPaths -ccontains $repositoryRoot
    if (-not $isRegistered) {
        $errors.Add("RepositoryPath is not a registered worktree for this project.")
    }
    $primaryPath = if ($registeredPaths.Count -gt 0) { $registeredPaths[0] } else { $null }
    $isPrimary = $null -ne $primaryPath -and $repositoryRoot -ceq $primaryPath

    $branchResult = Invoke-GitText -Path $repositoryRoot -Arguments @("branch", "--show-current")
    $branch = $branchResult.Text
    $isDetached = [string]::IsNullOrWhiteSpace($branch)
    $head = (Invoke-GitText -Path $repositoryRoot -Arguments @("rev-parse", "HEAD")).Text
    $changes = (Invoke-GitText -Path $repositoryRoot -Arguments @("status", "--porcelain=v1")).Lines
    $isDirty = $changes.Count -gt 0
    if ($isDirty) {
        $warnings.Add("Worktree is dirty; existing changes were not modified or cleaned.")
    }

    $originResult = Invoke-GitText -Path $repositoryRoot -Arguments @("rev-parse", "--verify", "origin/master") -AllowFailure
    $originMaster = if ($originResult.ExitCode -eq 0) { $originResult.Text } else { $null }
    $ahead = $null
    $behind = $null
    if ($null -ne $originMaster) {
        $counts = (Invoke-GitText -Path $repositoryRoot -Arguments @(
            "rev-list", "--left-right", "--count", "HEAD...origin/master"
        )).Text -split "\s+"
        $ahead = [int]$counts[0]
        $behind = [int]$counts[1]
    } else {
        $warnings.Add("origin/master is unavailable; base relationship was not calculated.")
    }

    if ($Mode -eq "Write") {
        if ($isPrimary) {
            $errors.Add("Write mode is not allowed in the primary worktree.")
        }
        if ($isDetached) {
            $errors.Add("Write mode requires an attached branch, not detached HEAD.")
        } elseif ($branch -notlike "codex/*") {
            if ($AllowNonCodexBranch) {
                $warnings.Add("Non-codex branch allowed by explicit exception: $branch")
            } else {
                $errors.Add("Write mode requires a codex/* branch unless -AllowNonCodexBranch is explicit.")
            }
        }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedBranch) -and $branch -cne $ExpectedBranch) {
            $errors.Add("Current branch '$branch' does not match ExpectedBranch '$ExpectedBranch'.")
        }
    }

    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        allowed = $errors.Count -eq 0
        repository_path = $repositoryRoot
        project_root = $projectRoot
        same_repository = $sameRepository
        registered_worktree = $isRegistered
        primary_worktree = $isPrimary
        branch = if ($isDetached) { $null } else { $branch }
        detached_head = $isDetached
        head = $head
        origin_master = $originMaster
        ahead = $ahead
        behind = $behind
        dirty = $isDirty
        change_count = $changes.Count
        errors = @($errors)
        warnings = @($warnings)
    }
} catch {
    $errors.Add($_.Exception.Message)
    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        allowed = $false
        repository_path = $RepositoryPath
        project_root = $scriptProjectRoot
        same_repository = $false
        registered_worktree = $false
        primary_worktree = $false
        branch = $null
        detached_head = $false
        head = $null
        origin_master = $null
        ahead = $null
        behind = $null
        dirty = $false
        change_count = 0
        errors = @($errors)
        warnings = @($warnings)
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
} else {
    [pscustomobject]$result | Format-List
}

if (-not $result.allowed) { exit 1 }
