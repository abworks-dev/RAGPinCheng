Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceScript = (Resolve-Path (Join-Path $PSScriptRoot "..\Test-CodexWorkspace.ps1")).Path
$sourceResolver = (Resolve-Path (Join-Path $PSScriptRoot "..\Resolve-CodexWorkspace.ps1")).Path
$sourceRegister = (Resolve-Path (Join-Path $PSScriptRoot "..\Register-CodexWorktree.ps1")).Path
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ragpincheng-workspace-test-" + [guid]::NewGuid().ToString("N"))
$repository = Join-Path $testRoot "project repo"
$scriptsDirectory = Join-Path $repository "scripts"
$manualRoot = Join-Path $testRoot ".worktrees\project repo"
$linked = Join-Path $manualRoot "linked worktree"
$feature = Join-Path $manualRoot "feature worktree"
$detached = Join-Path $manualRoot "detached worktree"
$uppercaseCodex = Join-Path $manualRoot "uppercase codex worktree"
$testCodexHome = Join-Path $testRoot "codex-home"
$managed = Join-Path $testCodexHome "worktrees\managed\project repo"
$legacyInternal = Join-Path $repository ".codex-worktrees\legacy task"
$legacySibling = Join-Path $testRoot "project repo-legacy-task"
$legacyTemp = Join-Path $testRoot "unstructured temporary task"
$otherRepository = Join-Path $testRoot "other repo"
$testScript = Join-Path $scriptsDirectory "Test-CodexWorkspace.ps1"
$resolverScript = Join-Path $scriptsDirectory "Resolve-CodexWorkspace.ps1"
$pwsh = (Get-Process -Id $PID).Path
$passed = 0
$previousCodexHome = $env:CODEX_HOME

function Invoke-GitChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & git @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git failed: $($Arguments -join ' ')" }
}

function Invoke-WorkspaceCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Mode,
        [string[]]$ExtraArguments = @()
    )

    $output = @(& $pwsh -NoProfile -File $testScript -Mode $Mode -RepositoryPath $Path -Json @ExtraArguments)
    $exitCode = $LASTEXITCODE
    $json = ($output -join "`n") | ConvertFrom-Json
    return [pscustomobject]@{ ExitCode = $exitCode; Result = $json }
}

function Invoke-WorkspaceDecision {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Mode,
        [Parameter(Mandatory = $true)][string]$TaskRisk,
        [string[]]$ExtraArguments = @()
    )

    $output = @(
        & $pwsh -NoProfile -File $resolverScript -Mode $Mode -TaskRisk $TaskRisk `
            -RepositoryPath $Path -Json @ExtraArguments
    )
    $exitCode = $LASTEXITCODE
    $json = ($output -join "`n") | ConvertFrom-Json
    return [pscustomobject]@{ ExitCode = $exitCode; Result = $json }
}

function Assert-Case {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$Condition
    )

    if (-not $Condition) { throw "Failed: $Name" }
    $script:passed++
    Write-Host "PASS $Name"
}

try {
    $env:CODEX_HOME = $testCodexHome
    New-Item -ItemType Directory -Path $scriptsDirectory -Force | Out-Null
    Copy-Item -LiteralPath $sourceScript -Destination $testScript
    Copy-Item -LiteralPath $sourceResolver -Destination $resolverScript
    Copy-Item -LiteralPath $sourceRegister -Destination (Join-Path $scriptsDirectory "Register-CodexWorktree.ps1")

    Invoke-GitChecked @("init", "-b", "master", $repository)
    Invoke-GitChecked @("-C", $repository, "config", "user.name", "Workspace Test")
    Invoke-GitChecked @("-C", $repository, "config", "user.email", "workspace-test@example.invalid")
    Set-Content -LiteralPath (Join-Path $repository "README.md") -Value "fixture" -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $repository ".gitignore") -Value ".venv/" -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $repository "requirements.txt") -Value "fixture==1" -Encoding utf8NoBOM
    Invoke-GitChecked @("-C", $repository, "add", ".")
    Invoke-GitChecked @("-C", $repository, "commit", "-m", "fixture")
    Invoke-GitChecked @("-C", $repository, "remote", "add", "origin", $repository)
    Invoke-GitChecked @("-C", $repository, "fetch", "origin", "master:refs/remotes/origin/master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/test-task", $linked, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "feature/test-task", $feature, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "--detach", $detached, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/managed-task", $managed, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/legacy-internal", $legacyInternal, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/legacy-sibling", $legacySibling, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/legacy-temp", $legacyTemp, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "CODEX/case-test", $uppercaseCodex, "master")
    Invoke-GitChecked @("init", "-b", "master", $otherRepository)

    $primaryVenv = Join-Path $repository ".venv"
    New-Item -ItemType Directory -Path $primaryVenv -Force | Out-Null

    $case = Invoke-WorkspaceCheck -Path $repository -Mode ReadOnly
    Assert-Case "primary readonly allowed" (
        $case.ExitCode -eq 0 -and $case.Result.primary_worktree -and
        $case.Result.reason_codes.Count -eq 0 -and $null -eq $case.Result.recommended_action -and
        $case.Result.recommended_worktree_action -eq "primary_read_only" -and
        $case.Result.recommended_environment -eq "none" -and
        $case.Result.base_freshness -eq "current"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode ReadOnly -TaskRisk R0
    Assert-Case "resolver selects primary for readonly" (
        $decision.ExitCode -eq 0 -and $decision.Result.allowed -and
        $decision.Result.recommended_worktree_action -eq "primary_read_only" -and
        $decision.Result.candidate_worktree -eq $repository -and
        $decision.Result.recommended_environment -eq "none"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New")
    Assert-Case "resolver recommends managed worktree for new task from primary" (
        $decision.ExitCode -eq 0 -and $decision.Result.allowed -and
        -not $decision.Result.workspace_allowed -and
        $decision.Result.recommended_worktree_action -eq "create_new" -and
        $null -eq $decision.Result.candidate_worktree -and
        $decision.Result.recommended_environment -eq "shared" -and
        $decision.Result.environment_read_only
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New", "-DependencyIntent", "Change")
    Assert-Case "resolver isolates planned dependency changes" (
        $decision.ExitCode -eq 0 -and
        $decision.Result.dependency_change_detected -and
        $decision.Result.recommended_environment -eq "isolated" -and
        -not $decision.Result.environment_exists
    )

    $decision = Invoke-WorkspaceDecision -Path $managed -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New")
    Assert-Case "resolver reuses clean managed worktree" (
        $decision.ExitCode -eq 0 -and $decision.Result.workspace_allowed -and
        $decision.Result.recommended_worktree_action -eq "reuse_existing" -and
        $decision.Result.candidate_worktree -eq $managed -and
        $decision.Result.recommended_environment -eq "shared"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R2 `
        -ExtraArguments @("-Intent", "Continue", "-ExpectedBranch", "codex/test-task")
    Assert-Case "resolver finds original continuation worktree" (
        $decision.ExitCode -eq 0 -and $decision.Result.approval_required -and
        $decision.Result.recommended_worktree_action -eq "reuse_existing" -and
        $decision.Result.candidate_worktree -eq $linked -and
        $decision.Result.candidate_branch -eq "codex/test-task"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode ReadOnly -TaskRisk R1
    Assert-Case "resolver rejects risk and mode mismatch" (
        $decision.ExitCode -ne 0 -and -not $decision.Result.allowed -and
        $decision.Result.reason_codes -contains "TASK_RISK_MODE_MISMATCH"
    )

    $managedVenv = Join-Path $managed ".venv"
    New-Item -ItemType Directory -Path $managedVenv -Force | Out-Null
    $decision = Invoke-WorkspaceDecision -Path $managed -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New")
    Assert-Case "resolver prefers existing worktree-local environment" (
        $decision.ExitCode -eq 0 -and
        $decision.Result.recommended_environment -eq "isolated" -and
        $decision.Result.environment_path -eq $managedVenv -and
        $decision.Result.environment_exists
    )

    [System.IO.Directory]::Delete($primaryVenv, $true)
    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New")
    Assert-Case "resolver reports a missing environment" (
        $decision.ExitCode -eq 0 -and
        $decision.Result.recommended_environment -eq "missing" -and
        $null -eq $decision.Result.environment_path -and
        -not $decision.Result.environment_exists
    )
    New-Item -ItemType Directory -Path $primaryVenv -Force | Out-Null

    Set-Content -LiteralPath (Join-Path $linked "requirements.txt") -Value "fixture==2" -Encoding utf8NoBOM
    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R2 `
        -ExtraArguments @("-Intent", "Continue", "-ExpectedBranch", "codex/test-task")
    Assert-Case "resolver isolates detected dependency changes" (
        $decision.ExitCode -eq 0 -and
        $decision.Result.dependency_change_detected -and
        $decision.Result.dependency_files -contains "requirements.txt" -and
        $decision.Result.recommended_environment -eq "isolated" -and
        -not $decision.Result.environment_read_only
    )
    Invoke-GitChecked @("-C", $linked, "restore", "requirements.txt")

    $masterCommit = (@(& git -C $repository rev-parse master) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) { throw "git rev-parse master failed" }
    $masterTree = (@(& git -C $repository rev-parse "master^{tree}") -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) { throw "git rev-parse master tree failed" }
    $futureCommit = ("future origin" | & git -C $repository commit-tree $masterTree -p $masterCommit).Trim()
    if ($LASTEXITCODE -ne 0) { throw "git commit-tree failed" }
    Invoke-GitChecked @("-C", $repository, "update-ref", "refs/remotes/origin/master", $futureCommit)
    $decision = Invoke-WorkspaceDecision -Path $repository -Mode ReadOnly -TaskRisk R0
    Assert-Case "resolver reports stale base without updating it" (
        $decision.ExitCode -eq 0 -and $decision.Result.base_freshness -eq "behind" -and
        $decision.Result.behind -eq 1 -and $decision.Result.warnings.Count -gt 0
    )
    Invoke-GitChecked @("-C", $repository, "update-ref", "refs/remotes/origin/master", $masterCommit)

    $case = Invoke-WorkspaceCheck -Path $repository -Mode ReadOnly -ExtraArguments @(
        "-ExpectedBranch", "no-such-branch"
    )
    Assert-Case "readonly expected branch mismatch warns without failing" (
        $case.ExitCode -eq 0 -and
        @($case.Result.warnings | Where-Object { $_ -like "*does not match current branch*" }).Count -gt 0
    )

    $case = Invoke-WorkspaceCheck -Path $repository -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "primary write rejected" (
        $case.ExitCode -ne 0 -and -not $case.Result.allowed -and
        $case.Result.reason_codes -contains "PRIMARY_WORKTREE_WRITE_FORBIDDEN" -and
        $case.Result.recommended_action -eq "CREATE_MANAGED_WORKTREE" -and
        $case.Result.recommended_environment -eq "none"
    )

    $case = Invoke-WorkspaceCheck -Path $repository -Mode Write
    Assert-Case "primary write action takes priority" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "WRITE_INTENT_REQUIRED" -and
        $case.Result.reason_codes -contains "PRIMARY_WORKTREE_WRITE_FORBIDDEN" -and
        $case.Result.recommended_action -eq "CREATE_MANAGED_WORKTREE"
    )

    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "standard manual codex branch allowed" (
        $case.ExitCode -eq 0 -and $case.Result.registered_worktree -and
        $case.Result.worktree_location -eq "manual-standard"
    )

    $case = Invoke-WorkspaceCheck -Path $managed -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "Codex-managed location allowed" (
        $case.ExitCode -eq 0 -and $case.Result.worktree_location -eq "codex-managed"
    )

    $case = Invoke-WorkspaceCheck -Path $legacyInternal -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "legacy internal location rejected for new task" (
        $case.ExitCode -ne 0 -and
        $case.Result.worktree_location -eq "legacy-internal" -and
        $case.Result.reason_codes -contains "NEW_WORKTREE_LOCATION_FORBIDDEN" -and
        $case.Result.recommended_action -eq "CREATE_MANAGED_WORKTREE"
    )

    $case = Invoke-WorkspaceCheck -Path $legacyInternal -Mode Write -ExtraArguments @(
        "-Intent", "Continue", "-ExpectedBranch", "codex/legacy-internal"
    )
    Assert-Case "legacy internal location allowed for continuation" (
        $case.ExitCode -eq 0 -and $case.Result.warnings.Count -gt 0
    )

    $case = Invoke-WorkspaceCheck -Path $legacySibling -Mode Write -ExtraArguments @(
        "-Intent", "Continue", "-ExpectedBranch", "codex/legacy-sibling"
    )
    Assert-Case "legacy sibling location allowed for continuation" (
        $case.ExitCode -eq 0 -and $case.Result.worktree_location -eq "legacy-sibling"
    )

    $case = Invoke-WorkspaceCheck -Path $legacyTemp -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "temporary location rejected for new task" (
        $case.ExitCode -ne 0 -and
        $case.Result.worktree_location -eq "legacy-temp" -and
        $case.Result.reason_codes -contains "NEW_WORKTREE_LOCATION_FORBIDDEN"
    )

    $case = Invoke-WorkspaceCheck -Path $detached -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "detached write rejected" (
        $case.ExitCode -ne 0 -and $case.Result.detached_head -and
        $case.Result.reason_codes -contains "DETACHED_HEAD_WRITE_FORBIDDEN" -and
        $case.Result.recommended_action -eq "ATTACH_CODEX_BRANCH"
    )

    $case = Invoke-WorkspaceCheck -Path $feature -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "non-codex branch rejected" (
        $case.ExitCode -ne 0 -and -not $case.Result.allowed -and
        $case.Result.reason_codes -contains "NON_CODEX_BRANCH_FORBIDDEN" -and
        $case.Result.recommended_action -eq "USE_CODEX_BRANCH"
    )

    $case = Invoke-WorkspaceCheck -Path $feature -Mode Write -ExtraArguments @(
        "-Intent", "New", "-AllowNonCodexBranch"
    )
    Assert-Case "non-codex exception without reason rejected" ($case.ExitCode -ne 0 -and -not $case.Result.allowed)

    $case = Invoke-WorkspaceCheck -Path $feature -Mode Write -ExtraArguments @(
        "-Intent", "New", "-AllowNonCodexBranch", "-ExceptionReason", "approved release branch"
    )
    Assert-Case "explicit non-codex exception allowed" (
        $case.ExitCode -eq 0 -and $case.Result.exception_used -and
        $case.Result.exception_reason -eq "approved release branch"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R2 `
        -ExtraArguments @("-Intent", "Continue", "-ExpectedBranch", "feature/test-task")
    Assert-Case "resolver blocks non-codex continuation without exception" (
        $decision.ExitCode -ne 0 -and -not $decision.Result.allowed -and
        $decision.Result.reason_codes -contains "NON_CODEX_BRANCH_FORBIDDEN"
    )

    $decision = Invoke-WorkspaceDecision -Path $repository -Mode Write -TaskRisk R2 `
        -ExtraArguments @(
            "-Intent", "Continue", "-ExpectedBranch", "feature/test-task",
            "-AllowNonCodexBranch", "-ExceptionReason", "approved release continuation"
        )
    Assert-Case "resolver forwards non-codex exception to continuation gate" (
        $decision.ExitCode -eq 0 -and $decision.Result.allowed -and
        $decision.Result.workspace_allowed -and $decision.Result.exception_used -and
        $decision.Result.exception_reason -eq "approved release continuation" -and
        $decision.Result.candidate_worktree -eq $feature -and
        $decision.Result.recommended_worktree_action -eq "reuse_existing"
    )

    $case = Invoke-WorkspaceCheck -Path $uppercaseCodex -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "uppercase codex prefix rejected for write" (
        $case.ExitCode -ne 0 -and -not $case.Result.allowed -and
        $case.Result.reason_codes -contains "NON_CODEX_BRANCH_FORBIDDEN"
    )

    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @(
        "-Intent", "Continue", "-ExpectedBranch", "codex/other"
    )
    Assert-Case "expected branch mismatch rejected" ($case.ExitCode -ne 0 -and -not $case.Result.allowed)

    $dirtyPath = Join-Path $linked "dirty file.txt"
    Set-Content -LiteralPath $dirtyPath -Value "preserve me" -Encoding utf8NoBOM
    $before = Get-Content -LiteralPath $dirtyPath -Raw
    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @("-Intent", "New")
    $after = Get-Content -LiteralPath $dirtyPath -Raw
    Assert-Case "dirty new task rejected without cleanup" (
        $case.ExitCode -ne 0 -and $case.Result.dirty -and $case.Result.change_count -eq 1 -and
        $case.Result.reason_codes -contains "DIRTY_WORKTREE_FOR_NEW_TASK" -and
        $case.Result.recommended_action -eq "USE_CLEAN_MANAGED_WORKTREE" -and $before -ceq $after
    )

    $decision = Invoke-WorkspaceDecision -Path $linked -Mode Write -TaskRisk R1 `
        -ExtraArguments @("-Intent", "New")
    Assert-Case "resolver does not reuse a dirty worktree for a new task" (
        $decision.ExitCode -eq 0 -and -not $decision.Result.workspace_allowed -and
        $decision.Result.recommended_worktree_action -eq "create_new" -and
        $null -eq $decision.Result.candidate_worktree
    )

    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @(
        "-Intent", "Continue", "-ExpectedBranch", "codex/test-task"
    )
    Assert-Case "dirty continuation allowed" ($case.ExitCode -eq 0 -and $case.Result.dirty)

    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @("-Intent", "Continue")
    Assert-Case "continuation without expected branch rejected" ($case.ExitCode -ne 0 -and -not $case.Result.allowed)

    if ($IsWindows) {
        $case = Invoke-WorkspaceCheck -Path $linked.ToUpperInvariant() -Mode Write -ExtraArguments @(
            "-Intent", "Continue", "-ExpectedBranch", "codex/test-task"
        )
        Assert-Case "Windows path comparison ignores case" ($case.ExitCode -eq 0 -and $case.Result.same_repository)
    }

    $case = Invoke-WorkspaceCheck -Path $otherRepository -Mode ReadOnly
    Assert-Case "other repository rejected" (
        $case.ExitCode -ne 0 -and -not $case.Result.same_repository -and
        $case.Result.reason_codes -contains "DIFFERENT_REPOSITORY" -and
        $case.Result.recommended_action -eq "USE_PROJECT_WORKTREE" -and
        $case.Result.recommended_worktree_action -eq "blocked"
    )

    $decision = Invoke-WorkspaceDecision -Path $otherRepository -Mode ReadOnly -TaskRisk R0
    Assert-Case "resolver blocks another repository" (
        $decision.ExitCode -ne 0 -and -not $decision.Result.allowed -and
        $decision.Result.recommended_worktree_action -eq "blocked" -and
        $decision.Result.reason_codes -contains "DIFFERENT_REPOSITORY"
    )

    Write-Host "Workspace harness tests passed: $passed"
} finally {
    $env:CODEX_HOME = $previousCodexHome
    if (Test-Path -LiteralPath $testRoot) {
        if (Test-Path -LiteralPath $repository) {
            foreach ($fixtureWorktree in @(
                $linked, $feature, $detached, $managed, $legacyInternal, $legacySibling, $legacyTemp,
                $uppercaseCodex
            )) {
                if (Test-Path -LiteralPath $fixtureWorktree) {
                    & git -C $repository worktree remove --force -- $fixtureWorktree 2>$null
                }
            }
        }
        Get-ChildItem -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Attributes = [System.IO.FileAttributes]::Normal }
        [System.IO.Directory]::Delete($testRoot, $true)
    }
}

# CI wraps this suite with `pwsh -command`; without an explicit exit the stale
# $LASTEXITCODE of the last child process becomes the step result.
exit 0
