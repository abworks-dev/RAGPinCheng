Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceScript = (Resolve-Path (Join-Path $PSScriptRoot "..\Test-CodexDelivery.ps1")).Path
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ragpincheng-delivery-test-" + [guid]::NewGuid().ToString("N"))
$pwsh = (Get-Process -Id $PID).Path
$passed = 0

function Write-JsonFixture {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][object]$Value
    )

    $path = Join-Path $testRoot $Name
    $json = ConvertTo-Json -InputObject $Value -Depth 8
    Set-Content -LiteralPath $path -Value $json -Encoding utf8NoBOM
    return $path
}

function New-PullRequestFixture {
    param(
        [int]$Number = 545,
        [string]$Branch = "codex/delivery-test",
        [string]$Base = "master",
        [string]$Body = @"
## Risk
- [ ] R0
- [x] R1
- [ ] R2
- [ ] R3

## Scope
Add a focused delivery policy.

## Validation
Run the delivery policy fixture suite.

## Rollback
Revert the policy commit.

## Approval Evidence
Not required for R1.
"@
    )

    return [pscustomobject]@{
        number = $Number
        state = "open"
        merged_at = $null
        body = $Body
        head = [pscustomobject]@{ ref = $Branch }
        base = [pscustomobject]@{ ref = $Base }
    }
}

function Invoke-DeliveryCheck {
    param(
        [Parameter(Mandatory = $true)][object]$PullRequest,
        [object[]]$History = @()
    )

    $pullRequestPath = Write-JsonFixture -Name "pr-$([guid]::NewGuid().ToString('N')).json" -Value $PullRequest
    $historyPath = Write-JsonFixture -Name "history-$([guid]::NewGuid().ToString('N')).json" -Value @($History)
    $output = @(
        & $pwsh -NoProfile -File $sourceScript -PullRequestFixturePath $pullRequestPath `
            -HistoryFixturePath $historyPath -Json
    )
    $exitCode = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    return [pscustomobject]@{ ExitCode = $exitCode; Result = $result }
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

function Invoke-GitChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & git @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "git failed: $($Arguments -join ' ')" }
}

try {
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

    $localRepository = Join-Path $testRoot "local repository"
    Invoke-GitChecked @("init", "-b", "master", $localRepository)
    Invoke-GitChecked @("-C", $localRepository, "config", "user.name", "Delivery Test")
    Invoke-GitChecked @("-C", $localRepository, "config", "user.email", "delivery-test@example.invalid")
    Set-Content -LiteralPath (Join-Path $localRepository "README.md") -Value "fixture" -Encoding utf8NoBOM
    Invoke-GitChecked @("-C", $localRepository, "add", "README.md")
    Invoke-GitChecked @("-C", $localRepository, "commit", "-m", "fixture")
    Invoke-GitChecked @("-C", $localRepository, "switch", "-c", "codex/local-delivery")

    $emptyHistoryPath = Write-JsonFixture -Name "local-empty-history.json" -Value @()
    $output = @(
        & $pwsh -NoProfile -File $sourceScript -RepositoryPath $localRepository `
            -HistoryFixturePath $emptyHistoryPath -Json
    )
    $localCase = [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Result = (($output -join "`n") | ConvertFrom-Json)
    }
    Assert-Case "local first delivery recommends PR creation" (
        $localCase.ExitCode -eq 0 -and
        $localCase.Result.recommended_action -eq "CREATE_PR"
    )

    $openHistoryPath = Write-JsonFixture -Name "local-open-history.json" -Value @([pscustomobject]@{
        number = 546
        state = "open"
        merged_at = $null
    })
    $output = @(
        & $pwsh -NoProfile -File $sourceScript -RepositoryPath $localRepository `
            -HistoryFixturePath $openHistoryPath -Json
    )
    $localCase = [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Result = (($output -join "`n") | ConvertFrom-Json)
    }
    Assert-Case "local delivery returns existing PR" (
        $localCase.ExitCode -ne 0 -and
        $localCase.Result.reason_codes -contains "PARALLEL_PULL_REQUEST_FOR_BRANCH" -and
        $localCase.Result.recommended_action -eq "CONTINUE_EXISTING_PR"
    )

    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture)
    if (-not $case.Result.allowed) {
        Write-Host ($case.Result | ConvertTo-Json -Depth 5)
    }
    Assert-Case "valid R1 pull request allowed" (
        $case.ExitCode -eq 0 -and $case.Result.allowed -and
        $case.Result.recommended_action -eq "READY_FOR_REVIEW"
    )

    $history = @([pscustomobject]@{
        number = 540
        state = "closed"
        merged_at = "2026-08-18T12:00:00Z"
    })
    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture) -History $history
    Assert-Case "merged branch reuse rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "MERGED_BRANCH_REUSED" -and
        $case.Result.recommended_action -eq "CREATE_NEW_BRANCH"
    )

    $history = @([pscustomobject]@{
        number = 546
        state = "open"
        merged_at = $null
    })
    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture) -History $history
    Assert-Case "parallel pull request rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "PARALLEL_PULL_REQUEST_FOR_BRANCH"
    )

    $legacy = New-PullRequestFixture -Number 544 -Body ""
    $case = Invoke-DeliveryCheck -PullRequest $legacy -History @([pscustomobject]@{
        number = 500
        state = "closed"
        merged_at = "2026-08-17T10:00:00Z"
    })
    Assert-Case "baseline pull request fully exempt" (
        $case.ExitCode -eq 0 -and $case.Result.legacy_pull_request -and
        $case.Result.warnings.Count -eq 1
    )

    $invalidBody = @"
## Risk
- [ ] R0
- [ ] R1
- [ ] R2
- [ ] R3

## Scope
TODO

## Validation

## Rollback
N/A

## Approval Evidence
N/A
"@
    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Body $invalidBody)
    Assert-Case "incomplete template rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "INVALID_RISK_SELECTION" -and
        $case.Result.reason_codes -contains "MISSING_SCOPE" -and
        $case.Result.reason_codes -contains "MISSING_VALIDATION" -and
        $case.Result.reason_codes -contains "MISSING_ROLLBACK"
    )

    $r2WithoutApproval = @"
## Risk
- [ ] R0
- [ ] R1
- [x] R2
- [ ] R3

## Scope
Change delivery governance.

## Validation
Run policy tests and required checks.

## Rollback
Revert the commit and restore the ruleset snapshot.

## Approval Evidence
N/A
"@
    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Body $r2WithoutApproval)
    Assert-Case "R2 approval evidence required" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "MISSING_APPROVAL_EVIDENCE"
    )

    $r2Approved = $r2WithoutApproval.Replace("N/A", "User approved the concrete R2 plan in the owning Codex task.")
    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Body $r2Approved)
    Assert-Case "R2 approval evidence accepted" ($case.ExitCode -eq 0 -and $case.Result.allowed)

    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Branch "feature/delivery-test")
    Assert-Case "non-codex delivery branch rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "NON_CODEX_DELIVERY_BRANCH"
    )

    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Branch "CODEX/Delivery-Test")
    Assert-Case "uppercase codex delivery branch rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "NON_CODEX_DELIVERY_BRANCH"
    )

    $case = Invoke-DeliveryCheck -PullRequest (New-PullRequestFixture -Base "release")
    Assert-Case "non-master base rejected" (
        $case.ExitCode -ne 0 -and
        $case.Result.reason_codes -contains "UNEXPECTED_BASE_BRANCH"
    )

    Write-Host "Delivery harness tests passed: $passed"
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        Get-ChildItem -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Attributes = [System.IO.FileAttributes]::Normal }
        [System.IO.Directory]::Delete($testRoot, $true)
    }
}

# CI wraps this suite with `pwsh -command`; without an explicit exit the stale
# $LASTEXITCODE of the last child process becomes the step result.
exit 0
