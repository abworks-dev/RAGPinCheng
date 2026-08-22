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

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $normalizedPath = Get-NormalizedPath -Path $Path
    $normalizedRoot = Get-NormalizedPath -Path $Root
    if ($pathComparer.Equals($normalizedPath, $normalizedRoot)) { return $true }
    $prefix = $normalizedRoot + [System.IO.Path]::DirectorySeparatorChar
    return $normalizedPath.StartsWith($prefix, $pathComparison)
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
$pathComparison = if ($IsWindows) {
    [System.StringComparison]::OrdinalIgnoreCase
} else {
    [System.StringComparison]::Ordinal
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

    $codexHome = if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        Get-NormalizedPath -Path $env:CODEX_HOME
    } else {
        Get-NormalizedPath -Path (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex")
    }
    $managedWorktreeRoot = Get-NormalizedPath -Path (Join-Path $codexHome "worktrees")
    $primaryParent = Split-Path -Parent $primaryPath
    $repositoryName = Split-Path -Leaf $primaryPath
    $manualWorktreeRoot = Get-NormalizedPath -Path (
        Join-Path (Join-Path $primaryParent ".worktrees") $repositoryName
    )
    $legacyInternalRoot = Get-NormalizedPath -Path (Join-Path $primaryPath ".codex-worktrees")
    $tempRoot = Get-NormalizedPath -Path ([System.IO.Path]::GetTempPath())
    $repositoryParent = Split-Path -Parent $repositoryRoot
    $repositoryLeaf = Split-Path -Leaf $repositoryRoot

    $worktreeLocation = if ($isPrimary) {
        "primary"
    } elseif (Test-PathWithin -Path $repositoryRoot -Root $managedWorktreeRoot) {
        "codex-managed"
    } elseif (Test-PathWithin -Path $repositoryRoot -Root $manualWorktreeRoot) {
        "manual-standard"
    } elseif (Test-PathWithin -Path $repositoryRoot -Root $legacyInternalRoot) {
        "legacy-internal"
    } elseif (
        $pathComparer.Equals($repositoryParent, $primaryParent) -and
        $repositoryLeaf.StartsWith($repositoryName + "-", $pathComparison)
    ) {
        "legacy-sibling"
    } elseif (Test-PathWithin -Path $repositoryRoot -Root $tempRoot) {
        "legacy-temp"
    } else {
        "legacy-other"
    }
    $isStandardWriteLocation = $worktreeLocation -in @("codex-managed", "manual-standard")

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
    $baseFreshness = if ($null -eq $originMaster -or $null -eq $behind) {
        "unknown"
    } elseif ($behind -gt 0) {
        "behind"
    } else {
        "current"
    }

    if ($Mode -eq "ReadOnly" -and -not $isDetached -and
        -not [string]::IsNullOrWhiteSpace($ExpectedBranch) -and
        $branch -cne $ExpectedBranch) {
        $warnings.Add("ExpectedBranch '$ExpectedBranch' does not match current branch '$branch'; read-only mode does not enforce branch matching.")
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
        if (-not $isPrimary -and -not $isStandardWriteLocation) {
            if ($Intent -eq "New") {
                Add-WorkspaceError -Code "NEW_WORKTREE_LOCATION_FORBIDDEN" `
                    -Message "New write tasks require a Codex-managed or standard manual worktree; current location is '$worktreeLocation'." `
                    -RecommendedAction "CREATE_MANAGED_WORKTREE"
            } elseif ($Intent -eq "Continue") {
                $warnings.Add("Legacy worktree location '$worktreeLocation' is allowed only for continuation; do not create new worktrees here.")
            }
        }
        if ($isDetached) {
            Add-WorkspaceError -Code "DETACHED_HEAD_WRITE_FORBIDDEN" `
                -Message "Write mode requires an attached branch, not detached HEAD." `
                -RecommendedAction "ATTACH_CODEX_BRANCH"
        } elseif ($branch -cnotlike "codex/*") {
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

    $recommendedWorktreeAction = if ($Mode -eq "ReadOnly" -and $errors.Count -gt 0) {
        "blocked"
    } elseif ($Mode -eq "ReadOnly") {
        if ($isPrimary) { "primary_read_only" } else { "reuse_existing" }
    } elseif ($isPrimary) {
        "create_new"
    } elseif ($errors.Count -eq 0) {
        "reuse_existing"
    } elseif (
        $Intent -eq "New" -and $sameRepository -and $isRegistered -and
        -not ($reasonCodes -contains "WORKSPACE_INSPECTION_FAILED")
    ) {
        "create_new"
    } else {
        "blocked"
    }

    $currentVenv = Join-Path $repositoryRoot ".venv"
    $primaryVenv = if ($null -ne $primaryPath) { Join-Path $primaryPath ".venv" } else { $null }
    $recommendedEnvironment = if ($Mode -eq "ReadOnly") {
        "none"
    } elseif ($isPrimary) {
        # The primary worktree never accepts writes; its .venv belongs to the shared
        # environment and must not be offered as an isolated task environment.
        "none"
    } elseif (Test-Path -LiteralPath $currentVenv -PathType Container) {
        "isolated"
    } elseif (
        $null -ne $primaryVenv -and
        (Test-Path -LiteralPath $primaryVenv -PathType Container)
    ) {
        "shared"
    } else {
        "missing"
    }
    $environmentPath = if ($recommendedEnvironment -eq "isolated") {
        $currentVenv
    } elseif ($recommendedEnvironment -eq "shared") {
        $primaryVenv
    } else {
        $null
    }
    $recommendationReason = if ($Mode -eq "ReadOnly" -and $isPrimary) {
        "PRIMARY_WORKTREE_READ_ONLY"
    } elseif ($Mode -eq "ReadOnly") {
        "CURRENT_WORKTREE_READ_ONLY"
    } elseif ($recommendedWorktreeAction -eq "create_new") {
        "CURRENT_WORKTREE_UNSUITABLE_FOR_NEW_WRITE_TASK"
    } elseif ($recommendedWorktreeAction -eq "reuse_existing") {
        "CURRENT_WORKTREE_ALLOWED"
    } else {
        "WORKSPACE_BLOCKED"
    }

    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        intent = if ([string]::IsNullOrWhiteSpace($Intent)) { $null } else { $Intent }
        allowed = $errors.Count -eq 0
        repository_path = $repositoryRoot
        project_root = $projectRoot
        primary_worktree_path = $primaryPath
        same_repository = $sameRepository
        registered_worktree = $isRegistered
        primary_worktree = $isPrimary
        worktree_location = $worktreeLocation
        managed_worktree_root = $managedWorktreeRoot
        manual_worktree_root = $manualWorktreeRoot
        branch = if ($isDetached) { $null } else { $branch }
        detached_head = $isDetached
        head = $head
        origin_master = $originMaster
        ahead = $ahead
        behind = $behind
        base_freshness = $baseFreshness
        dirty = $isDirty
        change_count = $changes.Count
        expected_branch = if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) { $null } else { $ExpectedBranch }
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
        recommended_action = $recommendedAction
        recommended_worktree_action = $recommendedWorktreeAction
        recommended_environment = $recommendedEnvironment
        environment_path = $environmentPath
        recommendation_reason = $recommendationReason
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
        primary_worktree_path = $null
        same_repository = $false
        registered_worktree = $false
        primary_worktree = $false
        worktree_location = $null
        managed_worktree_root = $null
        manual_worktree_root = $null
        branch = $null
        detached_head = $false
        head = $null
        origin_master = $null
        ahead = $null
        behind = $null
        base_freshness = "unknown"
        dirty = $false
        change_count = 0
        expected_branch = if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) { $null } else { $ExpectedBranch }
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
        recommended_action = $recommendedAction
        recommended_worktree_action = "blocked"
        recommended_environment = "missing"
        environment_path = $null
        recommendation_reason = "WORKSPACE_INSPECTION_FAILED"
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
} else {
    [pscustomobject]$result | Format-List
}

if (-not $result.allowed) { exit 1 }
