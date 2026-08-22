[CmdletBinding()]
param(
    [string]$RepositoryPath = (Join-Path $PSScriptRoot ".."),

    [string]$Repository,

    [ValidateRange(1, [int]::MaxValue)]
    [int]$PullRequestNumber,

    [ValidateRange(0, [int]::MaxValue)]
    [int]$PolicyBaselinePullRequest = 544,

    [string]$PullRequestFixturePath,

    [string]$HistoryFixturePath,

    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$reasonCodes = [System.Collections.Generic.List[string]]::new()
$recommendedAction = $null

function Add-PolicyError {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][string]$Action
    )

    $script:errors.Add($Message)
    $script:reasonCodes.Add($Code)
    if ($null -eq $script:recommendedAction) {
        $script:recommendedAction = $Action
    }
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Invoke-GitHubJson {
    param([Parameter(Mandatory = $true)][string]$Endpoint)

    $output = @(& gh api $Endpoint 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub API request failed for '$Endpoint': $($output -join "`n")"
    }
    return ($output -join "`n") | ConvertFrom-Json
}

function Get-SectionContent {
    param(
        [Parameter(Mandatory = $true)][string]$Body,
        [Parameter(Mandatory = $true)][string]$Heading
    )

    $escaped = [regex]::Escape($Heading)
    $match = [regex]::Match(
        $Body,
        "(?ims)^##\s+$escaped\s*\r?\n(?<content>.*?)(?=^##\s+|\z)"
    )
    if (-not $match.Success) { return $null }
    $content = [regex]::Replace($match.Groups["content"].Value, "(?s)<!--.*?-->", "").Trim()
    return $content
}

function Test-MeaningfulContent {
    param([AllowNull()][string]$Content)

    if ([string]::IsNullOrWhiteSpace($Content)) { return $false }
    return $Content -notmatch '^(?i)(n/?a|none|todo|tbd|not applicable)[.!\s]*$'
}

try {
    $pullRequest = $null
    $history = @()
    $branch = $null
    $baseBranch = "master"

    if (-not [string]::IsNullOrWhiteSpace($PullRequestFixturePath)) {
        $pullRequest = Read-JsonFile -Path $PullRequestFixturePath
        $PullRequestNumber = [int]$pullRequest.number
    } elseif ($PullRequestNumber -gt 0) {
        if ([string]::IsNullOrWhiteSpace($Repository)) {
            throw "Repository is required when PullRequestNumber is specified."
        }
        $pullRequest = Invoke-GitHubJson -Endpoint "repos/$Repository/pulls/$PullRequestNumber"
    }

    if ($null -ne $pullRequest) {
        $branch = [string]$pullRequest.head.ref
        $baseBranch = [string]$pullRequest.base.ref
    } else {
        $branch = (@(& git -C $RepositoryPath branch --show-current 2>$null) -join "`n").Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
            throw "Unable to determine the current branch."
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($HistoryFixturePath)) {
        $history = @(Read-JsonFile -Path $HistoryFixturePath)
    } elseif (-not [string]::IsNullOrWhiteSpace($Repository)) {
        $owner = $Repository.Split('/')[0]
        $head = [uri]::EscapeDataString("${owner}:$branch")
        $history = @(Invoke-GitHubJson -Endpoint "repos/$Repository/pulls?state=all&head=$head&per_page=100")
    } else {
        throw "Repository or HistoryFixturePath is required to inspect branch delivery history."
    }

    $isLegacyPullRequest = $PullRequestNumber -gt 0 -and $PullRequestNumber -le $PolicyBaselinePullRequest
    if ($isLegacyPullRequest) {
        $warnings.Add("Pull request #$PullRequestNumber predates the delivery-policy baseline and is exempt from delivery checks.")
    } else {
        if ($branch -cnotlike "codex/*") {
            Add-PolicyError -Code "NON_CODEX_DELIVERY_BRANCH" `
                -Message "Delivery requires a codex/* branch; current branch is '$branch'." `
                -Action "USE_CODEX_BRANCH"
        }
        if ($baseBranch -ne "master") {
            Add-PolicyError -Code "UNEXPECTED_BASE_BRANCH" `
                -Message "Pull requests must target master; current base is '$baseBranch'." `
                -Action "TARGET_MASTER"
        }

        $otherPullRequests = @($history | Where-Object { [int]$_.number -ne $PullRequestNumber })
        $previouslyMerged = @($otherPullRequests | Where-Object { $null -ne $_.merged_at -and "" -ne [string]$_.merged_at })
        $otherOpen = @($otherPullRequests | Where-Object { [string]$_.state -eq "open" })

        if ($previouslyMerged.Count -gt 0) {
            $numbers = ($previouslyMerged | ForEach-Object { "#$($_.number)" }) -join ", "
            Add-PolicyError -Code "MERGED_BRANCH_REUSED" `
                -Message "Branch '$branch' was already merged through $numbers. Start follow-up work from current master on a new branch." `
                -Action "CREATE_NEW_BRANCH"
        }
        if ($otherOpen.Count -gt 0) {
            $numbers = ($otherOpen | ForEach-Object { "#$($_.number)" }) -join ", "
            Add-PolicyError -Code "PARALLEL_PULL_REQUEST_FOR_BRANCH" `
                -Message "Branch '$branch' already has open pull request $numbers. Continue that pull request." `
                -Action "CONTINUE_EXISTING_PR"
        }
    }

    if (-not $isLegacyPullRequest -and $null -ne $pullRequest) {
        $body = [string]$pullRequest.body
        $riskMatches = @([regex]::Matches($body, '(?im)^\s*-\s*\[[xX]\]\s*R([0-3])(?:\s|$)'))
        if ($riskMatches.Count -ne 1) {
            Add-PolicyError -Code "INVALID_RISK_SELECTION" `
                -Message "Select exactly one R0-R3 risk checkbox in the pull request body." `
                -Action "COMPLETE_PR_TEMPLATE"
        }

        foreach ($heading in @("Scope", "Validation", "Rollback")) {
            $content = Get-SectionContent -Body $body -Heading $heading
            if (-not (Test-MeaningfulContent -Content $content)) {
                Add-PolicyError -Code ("MISSING_" + $heading.ToUpperInvariant()) `
                    -Message "Pull request section '$heading' must contain concrete information." `
                    -Action "COMPLETE_PR_TEMPLATE"
            }
        }

        if ($riskMatches.Count -eq 1 -and [int]$riskMatches[0].Groups[1].Value -ge 2) {
            $approval = Get-SectionContent -Body $body -Heading "Approval Evidence"
            if (-not (Test-MeaningfulContent -Content $approval)) {
                Add-PolicyError -Code "MISSING_APPROVAL_EVIDENCE" `
                    -Message "R2/R3 pull requests require concrete approval evidence in the pull request body." `
                    -Action "ADD_APPROVAL_EVIDENCE"
            }
        }
    }

    if ($null -eq $pullRequest -and $errors.Count -eq 0) {
        $recommendedAction = "CREATE_PR"
    } elseif ($null -ne $pullRequest -and $errors.Count -eq 0) {
        $recommendedAction = "READY_FOR_REVIEW"
    }

    $result = [ordered]@{
        schema_version = 1
        allowed = $errors.Count -eq 0
        repository = if ([string]::IsNullOrWhiteSpace($Repository)) { $null } else { $Repository }
        pull_request_number = if ($PullRequestNumber -gt 0) { $PullRequestNumber } else { $null }
        policy_baseline_pull_request = $PolicyBaselinePullRequest
        legacy_pull_request = $isLegacyPullRequest
        branch = $branch
        base_branch = $baseBranch
        history_count = $history.Count
        errors = @($errors)
        warnings = @($warnings)
        reason_codes = @($reasonCodes)
        recommended_action = $recommendedAction
    }
} catch {
    Add-PolicyError -Code "DELIVERY_INSPECTION_FAILED" -Message $_.Exception.Message -Action "VERIFY_DELIVERY_CONTEXT"
    $result = [ordered]@{
        schema_version = 1
        allowed = $false
        repository = if ([string]::IsNullOrWhiteSpace($Repository)) { $null } else { $Repository }
        pull_request_number = if ($PullRequestNumber -gt 0) { $PullRequestNumber } else { $null }
        policy_baseline_pull_request = $PolicyBaselinePullRequest
        legacy_pull_request = $false
        branch = $null
        base_branch = $null
        history_count = 0
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

exit 0
