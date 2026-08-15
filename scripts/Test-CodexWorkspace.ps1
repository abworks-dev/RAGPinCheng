[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ReadOnly", "Write")]
    [string]$Mode,

    [ValidateSet("New", "Continue")]
    [string]$Intent,

    [string]$RepositoryPath = (Join-Path $PSScriptRoot ".."),

    [string]$ExpectedBranch,

    [switch]$AllowNonCodexBranch,

    [string]$ExceptionReason,

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
$reasonCodes = [System.Collections.Generic.List[string]]::new()
$recommendedAction = $null

function Add-WorkspaceError {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][string]$RecommendedAction
    )

    $script:errors.Add($Message)
    $script:reasonCodes.Add($Code)
    if ($null -eq $script:recommendedAction) {
        $script:recommendedAction = $RecommendedAction
    }
}

$scriptProjectRoot = Get-NormalizedPath -Path (Join-Path $PSScriptRoot "..")
$pathComparer = if ($IsWindows) {
    [System.StringComparer]::OrdinalIgnoreCase
} else {
    [System.StringComparer]::Ordinal
}

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
    $sameRepository = $pathComparer.Equals($repositoryCommon, $projectCommon)
    if (-not $sameRepository) {
        Add-WorkspaceError -Code "DIFFERENT_REPOSITORY" `
            -Message "RepositoryPath does not belong to this project repository." `
            -RecommendedAction "USE_PROJECT_WORKTREE"
    }

    $worktreeLines = (Invoke-GitText -Path $projectRoot -Arguments @("worktree", "list", "--porcelain")).Lines
    $registeredPaths = @(
        $worktreeLines |
            Where-Object { $_ -like "worktree *" } |
            ForEach-Object { Get-NormalizedPath -Path $_.Substring(9) }
    )
    $isRegistered = @(
        $registeredPaths | Where-Object { $pathComparer.Equals($_, $repositoryRoot) }
    ).Count -gt 0
    if (-not $isRegistered) {
        Add-WorkspaceError -Code "UNREGISTERED_WORKTREE" `
            -Message "RepositoryPath is not a registered worktree for this project." `
            -RecommendedAction "USE_REGISTERED_WORKTREE"
    }
    $primaryPath = if ($registeredPaths.Count -gt 0) { $registeredPaths[0] } else { $null }
    $isPrimary = $null -ne $primaryPath -and $pathComparer.Equals($repositoryRoot, $primaryPath)

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
        if ([string]::IsNullOrWhiteSpace($Intent)) {
            Add-WorkspaceError -Code "WRITE_INTENT_REQUIRED" `
                -Message "Write mode requires -Intent New or -Intent Continue." `
                -RecommendedAction "SPECIFY_WRITE_INTENT"
        }
        if ($isPrimary) {
            Add-WorkspaceError -Code "PRIMARY_WORKTREE_WRITE_FORBIDDEN" `
                -Message "Write mode is not allowed in the primary worktree." `
                -RecommendedAction "CREATE_MANAGED_WORKTREE"
            $recommendedAction = "CREATE_MANAGED_WORKTREE"
        }
        if ($isDetached) {
            Add-WorkspaceError -Code "DETACHED_HEAD_WRITE_FORBIDDEN" `
                -Message "Write mode requires an attached branch, not detached HEAD." `
                -RecommendedAction "ATTACH_CODEX_BRANCH"
        } elseif ($branch -notlike "codex/*") {
            if ($AllowNonCodexBranch) {
                if ([string]::IsNullOrWhiteSpace($ExceptionReason)) {
                    Add-WorkspaceError -Code "EXCEPTION_REASON_REQUIRED" `
                        -Message "A non-codex branch exception requires -ExceptionReason." `
                        -RecommendedAction "PROVIDE_EXCEPTION_REASON"
                } else {
                    $warnings.Add("Non-codex branch allowed by explicit exception: $branch")
                }
            } else {
                Add-WorkspaceError -Code "NON_CODEX_BRANCH_FORBIDDEN" `
                    -Message "Write mode requires a codex/* branch unless -AllowNonCodexBranch is explicit." `
                    -RecommendedAction "USE_CODEX_BRANCH"
            }
        }
        if ($Intent -eq "New" -and $isDirty) {
            Add-WorkspaceError -Code "DIRTY_WORKTREE_FOR_NEW_TASK" `
                -Message "A new write task requires a clean worktree." `
                -RecommendedAction "USE_CLEAN_MANAGED_WORKTREE"
        }
        if ($Intent -eq "Continue") {
            if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) {
                Add-WorkspaceError -Code "EXPECTED_BRANCH_REQUIRED" `
                    -Message "Continue intent requires -ExpectedBranch." `
                    -RecommendedAction "SPECIFY_EXPECTED_BRANCH"
            } elseif ($branch -cne $ExpectedBranch) {
                Add-WorkspaceError -Code "EXPECTED_BRANCH_MISMATCH" `
                    -Message "Current branch '$branch' does not match ExpectedBranch '$ExpectedBranch'." `
                    -RecommendedAction "RETURN_TO_EXPECTED_WORKTREE"
            }
        } elseif (-not [string]::IsNullOrWhiteSpace($ExpectedBranch) -and $branch -cne $ExpectedBranch) {
            Add-WorkspaceError -Code "EXPECTED_BRANCH_MISMATCH" `
                -Message "Current branch '$branch' does not match ExpectedBranch '$ExpectedBranch'." `
                -RecommendedAction "RETURN_TO_EXPECTED_WORKTREE"
        }
    }

    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        intent = if ([string]::IsNullOrWhiteSpace($Intent)) { $null } else { $Intent }
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
        expected_branch = if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) { $null } else { $ExpectedBranch }
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
        recommended_action = $recommendedAction
    }
} catch {
    Add-WorkspaceError -Code "WORKSPACE_INSPECTION_FAILED" `
        -Message $_.Exception.Message `
        -RecommendedAction "VERIFY_REPOSITORY_PATH"
    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        intent = if ([string]::IsNullOrWhiteSpace($Intent)) { $null } else { $Intent }
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
        expected_branch = if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) { $null } else { $ExpectedBranch }
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
        recommended_action = $recommendedAction
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
} else {
    [pscustomobject]$result | Format-List
}

if (-not $result.allowed) { exit 1 }
