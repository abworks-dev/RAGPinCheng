Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceScript = (Resolve-Path (Join-Path $PSScriptRoot "..\Test-CodexWorkspace.ps1")).Path
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ragpincheng-workspace-test-" + [guid]::NewGuid().ToString("N"))
$repository = Join-Path $testRoot "project repo"
$scriptsDirectory = Join-Path $repository "scripts"
$linked = Join-Path $testRoot "linked worktree"
$feature = Join-Path $testRoot "feature worktree"
$detached = Join-Path $testRoot "detached worktree"
$otherRepository = Join-Path $testRoot "other repo"
$testScript = Join-Path $scriptsDirectory "Test-CodexWorkspace.ps1"
$pwsh = (Get-Process -Id $PID).Path
$passed = 0

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
    New-Item -ItemType Directory -Path $scriptsDirectory -Force | Out-Null
    Copy-Item -LiteralPath $sourceScript -Destination $testScript

    Invoke-GitChecked @("init", "-b", "master", $repository)
    Invoke-GitChecked @("-C", $repository, "config", "user.name", "Workspace Test")
    Invoke-GitChecked @("-C", $repository, "config", "user.email", "workspace-test@example.invalid")
    Set-Content -LiteralPath (Join-Path $repository "README.md") -Value "fixture" -Encoding utf8NoBOM
    Invoke-GitChecked @("-C", $repository, "add", ".")
    Invoke-GitChecked @("-C", $repository, "commit", "-m", "fixture")
    Invoke-GitChecked @("-C", $repository, "remote", "add", "origin", $repository)
    Invoke-GitChecked @("-C", $repository, "fetch", "origin", "master:refs/remotes/origin/master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "codex/test-task", $linked, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "-b", "feature/test-task", $feature, "master")
    Invoke-GitChecked @("-C", $repository, "worktree", "add", "--detach", $detached, "master")
    Invoke-GitChecked @("init", "-b", "master", $otherRepository)

    $case = Invoke-WorkspaceCheck -Path $repository -Mode ReadOnly
    Assert-Case "primary readonly allowed" ($case.ExitCode -eq 0 -and $case.Result.primary_worktree)

    $case = Invoke-WorkspaceCheck -Path $repository -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "primary write rejected" ($case.ExitCode -ne 0 -and -not $case.Result.allowed)

    $case = Invoke-WorkspaceCheck -Path $linked -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "linked codex branch allowed" ($case.ExitCode -eq 0 -and $case.Result.registered_worktree)

    $case = Invoke-WorkspaceCheck -Path $detached -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "detached write rejected" ($case.ExitCode -ne 0 -and $case.Result.detached_head)

    $case = Invoke-WorkspaceCheck -Path $feature -Mode Write -ExtraArguments @("-Intent", "New")
    Assert-Case "non-codex branch rejected" ($case.ExitCode -ne 0 -and -not $case.Result.allowed)

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
        $case.ExitCode -ne 0 -and $case.Result.dirty -and $case.Result.change_count -eq 1 -and $before -ceq $after
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
    Assert-Case "other repository rejected" ($case.ExitCode -ne 0 -and -not $case.Result.same_repository)

    Write-Host "Workspace harness tests passed: $passed"
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        if (Test-Path -LiteralPath $repository) {
            foreach ($fixtureWorktree in @($linked, $feature, $detached)) {
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
