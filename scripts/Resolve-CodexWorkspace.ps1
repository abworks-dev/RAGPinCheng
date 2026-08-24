[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ReadOnly", "Write")]
    [string]$Mode,

    [ValidateSet("New", "Continue")]
    [string]$Intent,

    [Parameter(Mandatory = $true)]
    [ValidateSet("R0", "R1", "R2", "R3")]
    [string]$TaskRisk,

    [string]$RepositoryPath = (Join-Path $PSScriptRoot ".."),

    [string]$ExpectedBranch,

    [switch]$AllowNonCodexBranch,

    [string]$ExceptionReason,

    [ValidateSet("Auto", "Change")]
    [string]$DependencyIntent = "Auto",

    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

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

function Invoke-WorkspaceGate {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$CheckMode,
        [string]$CheckIntent,
        [string]$CheckExpectedBranch,
        [switch]$CheckAllowNonCodexBranch,
        [string]$CheckExceptionReason
    )

    $gateArguments = @(
        "-NoProfile", "-File", $gateScript,
        "-Mode", $CheckMode,
        "-RepositoryPath", $Path,
        "-Json"
    )
    if (-not [string]::IsNullOrWhiteSpace($CheckIntent)) {
        $gateArguments += @("-Intent", $CheckIntent)
    }
    if (-not [string]::IsNullOrWhiteSpace($CheckExpectedBranch)) {
        $gateArguments += @("-ExpectedBranch", $CheckExpectedBranch)
    }
    if ($CheckAllowNonCodexBranch) {
        $gateArguments += @("-AllowNonCodexBranch")
    }
    if (-not [string]::IsNullOrWhiteSpace($CheckExceptionReason)) {
        $gateArguments += @("-ExceptionReason", $CheckExceptionReason)
    }

    $output = @(& $pwsh @gateArguments 2>&1)
    $exitCode = $LASTEXITCODE
    try {
        $result = ($output -join "`n") | ConvertFrom-Json
    } catch {
        throw "Workspace gate returned invalid JSON: $($output -join "`n")"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Result = $result }
}

function Get-RegisteredWorktrees {
    param([Parameter(Mandatory = $true)][string]$Path)

    $entries = [System.Collections.Generic.List[object]]::new()
    $current = $null
    foreach ($line in (Invoke-GitText -Path $Path -Arguments @("worktree", "list", "--porcelain")).Lines) {
        if ($line -like "worktree *") {
            if ($null -ne $current) { $entries.Add([pscustomobject]$current) }
            $current = [ordered]@{
                Path = Get-NormalizedPath -Path $line.Substring(9)
                Branch = $null
                Detached = $false
            }
        } elseif ($null -ne $current -and $line -like "branch refs/heads/*") {
            $current.Branch = $line.Substring(18)
        } elseif ($null -ne $current -and $line -eq "detached") {
            $current.Detached = $true
        }
    }
    if ($null -ne $current) { $entries.Add([pscustomobject]$current) }
    return @($entries)
}

function Get-DependencyChanges {
    param([Parameter(Mandatory = $true)][string]$Path)

    $paths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $inspectionSucceeded = $true
    $commands = @(
        @("diff", "--name-only"),
        @("diff", "--cached", "--name-only"),
        @("ls-files", "--others", "--exclude-standard")
    )
    foreach ($arguments in $commands) {
        $command = Invoke-GitText -Path $Path -Arguments $arguments -AllowFailure
        if ($command.ExitCode -ne 0) {
            $inspectionSucceeded = $false
        } else {
            foreach ($line in $command.Lines) {
                if (-not [string]::IsNullOrWhiteSpace($line)) {
                    [void]$paths.Add($line.Replace("\", "/"))
                }
            }
        }
    }

    $origin = Invoke-GitText -Path $Path -Arguments @("rev-parse", "--verify", "origin/master") -AllowFailure
    if ($origin.ExitCode -eq 0) {
        $branchChanges = Invoke-GitText -Path $Path -Arguments @(
            "diff", "--name-only", "origin/master", "HEAD"
        ) -AllowFailure
        if ($branchChanges.ExitCode -ne 0) {
            $inspectionSucceeded = $false
        } else {
            foreach ($line in $branchChanges.Lines) {
                if (-not [string]::IsNullOrWhiteSpace($line)) {
                    [void]$paths.Add($line.Replace("\", "/"))
                }
            }
        }
    } else {
        $inspectionSucceeded = $false
    }

    $dependencyPattern = '(^|/)(requirements[^/]*\.txt|pyproject\.toml|uv\.lock|poetry\.lock|Pipfile(\.lock)?)$|^frontend/(package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$'
    $dependencyFiles = @($paths | Where-Object { $_ -match $dependencyPattern } | Sort-Object)
    return [pscustomobject]@{
        InspectionSucceeded = $inspectionSucceeded
        Files = $dependencyFiles
        Changed = $dependencyFiles.Count -gt 0
    }
}

function Get-VenvPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return Join-Path $Path ".venv"
}

$gateScript = Join-Path $PSScriptRoot "Test-CodexWorkspace.ps1"
$pwsh = (Get-Process -Id $PID).Path
$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$reasonCodes = [System.Collections.Generic.List[string]]::new()

try {
    $initialGate = Invoke-WorkspaceGate -Path $RepositoryPath -CheckMode $Mode `
        -CheckIntent $Intent -CheckExpectedBranch $ExpectedBranch `
        -CheckAllowNonCodexBranch:$AllowNonCodexBranch -CheckExceptionReason $ExceptionReason
    $initial = $initialGate.Result
    foreach ($warning in @($initial.warnings)) { $warnings.Add([string]$warning) }
    $currentWorkspaceReasonCodes = @($initial.reason_codes)

    $riskMismatch = ($Mode -eq "ReadOnly" -and $TaskRisk -ne "R0") -or
        ($Mode -eq "Write" -and $TaskRisk -eq "R0")
    if ($riskMismatch) {
        $errors.Add("TaskRisk '$TaskRisk' is incompatible with Mode '$Mode'.")
        $reasonCodes.Add("TASK_RISK_MODE_MISMATCH")
    }

    $sameProject = [bool]$initial.same_repository -and [bool]$initial.registered_worktree
    if (-not $sameProject) {
        $errors.Add("RepositoryPath is not a registered worktree for this project.")
        foreach ($code in $currentWorkspaceReasonCodes) {
            if (-not $reasonCodes.Contains([string]$code)) { $reasonCodes.Add([string]$code) }
        }
    }

    $selectedGate = $initial
    $candidatePath = $null
    $candidateBranch = $null
    $worktreeAction = "blocked"
    $decisionReason = "WORKSPACE_BLOCKED"

    if ($errors.Count -eq 0 -and $Mode -eq "ReadOnly") {
        if ([bool]$initial.allowed) {
            $candidatePath = [string]$initial.repository_path
            $candidateBranch = $initial.branch
            $worktreeAction = if ([bool]$initial.primary_worktree) {
                "primary_read_only"
            } else {
                "reuse_existing"
            }
            $decisionReason = if ([bool]$initial.primary_worktree) {
                "PRIMARY_WORKTREE_READ_ONLY"
            } else {
                "CURRENT_WORKTREE_READ_ONLY"
            }
        } else {
            $errors.Add("The current workspace is not valid for read-only use.")
            foreach ($code in $currentWorkspaceReasonCodes) {
                if (-not $reasonCodes.Contains([string]$code)) { $reasonCodes.Add([string]$code) }
            }
        }
    } elseif ($errors.Count -eq 0 -and $Intent -eq "New") {
        if ([bool]$initial.allowed) {
            $candidatePath = [string]$initial.repository_path
            $candidateBranch = $initial.branch
            $worktreeAction = "reuse_existing"
            $decisionReason = "CURRENT_WORKTREE_ALLOWED"
        } else {
            $worktreeAction = "create_new"
            $decisionReason = "NEW_MANAGED_WORKTREE_REQUIRED"
        }
    } elseif ($errors.Count -eq 0 -and $Intent -eq "Continue") {
        if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) {
            $errors.Add("Continue intent requires ExpectedBranch.")
            $reasonCodes.Add("EXPECTED_BRANCH_REQUIRED")
        } else {
            $matches = @(
                Get-RegisteredWorktrees -Path ([string]$initial.primary_worktree_path) |
                    Where-Object { $_.Branch -ceq $ExpectedBranch }
            )
            if ($matches.Count -ne 1) {
                $errors.Add("ExpectedBranch '$ExpectedBranch' is not checked out in exactly one registered worktree.")
                $reasonCodes.Add("EXPECTED_WORKTREE_NOT_FOUND")
            } else {
                $candidatePath = [string]$matches[0].Path
                $candidateBranch = [string]$matches[0].Branch
                $candidateGate = Invoke-WorkspaceGate -Path $candidatePath -CheckMode Write `
                    -CheckIntent Continue -CheckExpectedBranch $ExpectedBranch `
                    -CheckAllowNonCodexBranch:$AllowNonCodexBranch -CheckExceptionReason $ExceptionReason
                $selectedGate = $candidateGate.Result
                foreach ($warning in @($selectedGate.warnings)) {
                    if (-not $warnings.Contains([string]$warning)) { $warnings.Add([string]$warning) }
                }
                if ([bool]$selectedGate.allowed) {
                    $worktreeAction = "reuse_existing"
                    $decisionReason = "EXPECTED_WORKTREE_FOUND"
                } else {
                    $errors.Add("The expected worktree failed the write gate.")
                    foreach ($code in @($selectedGate.reason_codes)) {
                        if (-not $reasonCodes.Contains([string]$code)) { $reasonCodes.Add([string]$code) }
                    }
                }
            }
        }
    } elseif ($errors.Count -eq 0) {
        $errors.Add("Write mode requires Intent New or Continue.")
        $reasonCodes.Add("WRITE_INTENT_REQUIRED")
    }

    if ($selectedGate.base_freshness -eq "behind") {
        $warnings.Add("Selected worktree is behind the local origin/master reference; no update was performed.")
    }

    $dependencyFiles = @()
    $dependencyChanged = $false
    $dependencyInspectionSucceeded = $true
    if ($Mode -eq "Write") {
        if ($DependencyIntent -eq "Change") {
            $dependencyChanged = $true
        } elseif ($worktreeAction -ne "blocked") {
            $dependencyInspectionPath = if ($null -ne $candidatePath) {
                $candidatePath
            } else {
                [string]$initial.repository_path
            }
            $dependencyResult = Get-DependencyChanges -Path $dependencyInspectionPath
            $dependencyFiles = @($dependencyResult.Files)
            $dependencyChanged = [bool]$dependencyResult.Changed
            $dependencyInspectionSucceeded = [bool]$dependencyResult.InspectionSucceeded
            if (-not $dependencyInspectionSucceeded) {
                $warnings.Add("Dependency state could not be fully inspected; an isolated environment is recommended.")
            }
        }
    }

    $recommendedEnvironment = "none"
    $environmentPath = $null
    $environmentExists = $false
    $environmentReadOnly = $false
    if ($Mode -eq "Write" -and $worktreeAction -ne "blocked") {
        $candidateVenv = if ($null -ne $candidatePath) { Get-VenvPath -Path $candidatePath } else { $null }
        $primaryVenv = if ($null -ne $initial.primary_worktree_path) {
            Get-VenvPath -Path ([string]$initial.primary_worktree_path)
        } else {
            $null
        }

        if ($dependencyChanged -or -not $dependencyInspectionSucceeded) {
            $recommendedEnvironment = "isolated"
            $environmentPath = $candidateVenv
        } elseif ($null -ne $candidateVenv -and (Test-Path -LiteralPath $candidateVenv -PathType Container)) {
            $recommendedEnvironment = "isolated"
            $environmentPath = $candidateVenv
            $environmentExists = $true
        } elseif ($null -ne $primaryVenv -and (Test-Path -LiteralPath $primaryVenv -PathType Container)) {
            $recommendedEnvironment = "shared"
            $environmentPath = $primaryVenv
            $environmentExists = $true
            $environmentReadOnly = $true
        } else {
            $recommendedEnvironment = "missing"
        }
        if ($null -ne $environmentPath -and -not $environmentExists) {
            $environmentExists = Test-Path -LiteralPath $environmentPath -PathType Container
        }
    }

    $scriptProjectRoot = if ($null -ne $initial.primary_worktree_path) {
        [string]$initial.primary_worktree_path
    } else {
        [string]$initial.repository_path
    }
    $lifecycleScript = Join-Path $scriptProjectRoot "scripts/Register-CodexWorktree.ps1"
    $lifecycleAvailable = Test-Path -LiteralPath $lifecycleScript -PathType Leaf
    $creationRequest = $null
    if ($worktreeAction -eq "create_new") {
        if (-not $lifecycleAvailable) {
            $reasonCodes.Add("HARNESS_CAPABILITY_MISSING")
            $errors.Add("The guarded worktree registration helper is unavailable.")
        }
        $creationRequest = [ordered]@{
            script = $lifecycleScript
            repository_path = [string]$initial.primary_worktree_path
            branch = if ([string]::IsNullOrWhiteSpace($ExpectedBranch)) { $null } else { $ExpectedBranch }
            base_ref = "origin/master"
            intent = if ([string]::IsNullOrWhiteSpace($Intent)) { "New" } else { $Intent }
            requires_target_path = $true
        }
    }
    $allowed = $errors.Count -eq 0 -and $worktreeAction -ne "blocked"
    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        intent = if ([string]::IsNullOrWhiteSpace($Intent)) { $null } else { $Intent }
        task_risk = $TaskRisk
        approval_required = $TaskRisk -in @("R2", "R3")
        allowed = $allowed
        workspace_allowed = [bool]$selectedGate.allowed
        repository_path = $initial.repository_path
        primary_worktree_path = $initial.primary_worktree_path
        recommended_worktree_action = $worktreeAction
        candidate_worktree = $candidatePath
        candidate_branch = $candidateBranch
        lifecycle_capability_available = $lifecycleAvailable
        creation_request = $creationRequest
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        recommended_environment = $recommendedEnvironment
        environment_path = $environmentPath
        environment_exists = $environmentExists
        environment_read_only = $environmentReadOnly
        dependency_intent = $DependencyIntent
        dependency_change_detected = if ($dependencyInspectionSucceeded) { $dependencyChanged } else { $null }
        dependency_files = $dependencyFiles
        base_freshness = $selectedGate.base_freshness
        ahead = $selectedGate.ahead
        behind = $selectedGate.behind
        decision_reason = $decisionReason
        current_workspace_reason_codes = $currentWorkspaceReasonCodes
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
    }
} catch {
    $result = [ordered]@{
        schema_version = 1
        mode = $Mode
        intent = if ([string]::IsNullOrWhiteSpace($Intent)) { $null } else { $Intent }
        task_risk = $TaskRisk
        approval_required = $TaskRisk -in @("R2", "R3")
        allowed = $false
        workspace_allowed = $false
        repository_path = $RepositoryPath
        primary_worktree_path = $null
        recommended_worktree_action = "blocked"
        candidate_worktree = $null
        candidate_branch = $null
        lifecycle_capability_available = $false
        creation_request = $null
        exception_used = [bool]$AllowNonCodexBranch
        exception_reason = if ([string]::IsNullOrWhiteSpace($ExceptionReason)) { $null } else { $ExceptionReason }
        recommended_environment = "missing"
        environment_path = $null
        environment_exists = $false
        environment_read_only = $false
        dependency_intent = $DependencyIntent
        dependency_change_detected = $null
        dependency_files = @()
        base_freshness = "unknown"
        ahead = $null
        behind = $null
        decision_reason = "WORKSPACE_DECISION_FAILED"
        current_workspace_reason_codes = @()
        errors = @($_.Exception.Message)
        warnings = @()
        reason_codes = @("WORKSPACE_DECISION_FAILED")
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 6
} else {
    [pscustomobject]$result | Format-List
}

if (-not $result.allowed) { exit 1 }

exit 0
