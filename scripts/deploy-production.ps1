[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RepositoryPath = 'D:\RAGPinCheng',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$BackupDirectory = 'D:\RAGBackups',

    [Parameter()]
    [string]$ProxyUrl = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,

        [Parameter()]
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

function Test-BackendHealth {
    param(
        [int]$Attempts = 30,
        [int]$DelaySeconds = 10
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri 'http://localhost/api/health' -TimeoutSec 10
            if ($response.status -eq 'ok') {
                return $true
            }
        }
        catch {
            Write-Host "Health check $attempt/$Attempts is not ready: $($_.Exception.Message)"
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
}

if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
    throw "Production repository does not exist: $RepositoryPath"
}

$composeFile = Join-Path $RepositoryPath 'docker\docker-compose.yml'
if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw "Docker Compose file does not exist: $composeFile"
}

if (-not (Test-Path -LiteralPath (Join-Path $RepositoryPath '.env') -PathType Leaf)) {
    throw 'Production .env is missing. Deployment stopped before changing code or containers.'
}

New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
Push-Location $RepositoryPath
$oldCommit = ''

try {
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'master') {
        throw "Production repository must be on master; current branch is '$branch'."
    }

    $trackedChanges = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect the production working tree.'
    }
    if ($trackedChanges.Count -gt 0) {
        throw 'Production repository has tracked local changes. Deployment stopped without overwriting them.'
    }

    $oldCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to record the current production commit.'
    }
    Set-Content -LiteralPath (Join-Path $BackupDirectory 'last-production-commit.txt') -Value $oldCommit

    Invoke-NativeCommand -Command 'git' -Arguments @('fetch', 'origin', '--prune')
    Invoke-NativeCommand -Command 'git' -Arguments @('merge-base', '--is-ancestor', 'HEAD', 'origin/master')

    $sensitivePaths = @(
        'api/db.py',
        'src/chunk.py',
        'src/embed.py',
        'src/index.py',
        'src/indexing_pipeline.py'
    )
    $changedPaths = @(& git diff --name-only HEAD origin/master)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect files changed by the pending deployment.'
    }
    $sensitiveChanges = @($changedPaths | Where-Object { $_ -in $sensitivePaths })
    if ($sensitiveChanges.Count -gt 0) {
        throw "Automatic deployment blocked because schema/index-sensitive files changed: $($sensitiveChanges -join ', '). Back up application data and Qdrant, then deploy manually after review."
    }

    Invoke-NativeCommand -Command 'git' -Arguments @('pull', '--ff-only', 'origin', 'master')

    $newCommit = (& git rev-parse HEAD).Trim()
    Write-Host "Deploying $newCommit (previous production commit: $oldCommit)"

    Invoke-NativeCommand -Command 'docker' -Arguments @('compose', '-f', $composeFile, 'config', '--quiet')

    $buildArguments = @('compose', '-f', $composeFile, 'build')
    if ($ProxyUrl) {
        $noProxy = 'localhost,127.0.0.1,qdrant'
        $buildArguments += @(
            '--build-arg', "HTTP_PROXY=$ProxyUrl",
            '--build-arg', "HTTPS_PROXY=$ProxyUrl",
            '--build-arg', "NO_PROXY=$noProxy",
            '--build-arg', "http_proxy=$ProxyUrl",
            '--build-arg', "https_proxy=$ProxyUrl",
            '--build-arg', "no_proxy=$noProxy"
        )
    }
    $buildArguments += @('--progress=plain', 'backend')
    Invoke-NativeCommand -Command 'docker' -Arguments $buildArguments
    Invoke-NativeCommand -Command 'docker' -Arguments @('compose', '-f', $composeFile, 'up', '-d', '--remove-orphans')

    if (-not (Test-BackendHealth)) {
        throw 'The new backend did not pass its health check.'
    }

    Set-Content -LiteralPath (Join-Path $BackupDirectory 'current-production-commit.txt') -Value $newCommit
    Invoke-NativeCommand -Command 'docker' -Arguments @('compose', '-f', $composeFile, 'ps')
    Write-Host "Production deployment succeeded: $newCommit"
}
catch {
    $deploymentError = $_
    Write-Warning "Deployment failed: $($deploymentError.Exception.Message)"

    if ($null -ne $oldCommit -and $oldCommit) {
        Write-Host "Attempting rollback to $oldCommit"
        try {
            Invoke-NativeCommand -Command 'git' -Arguments @('switch', '--detach', $oldCommit)
            Invoke-NativeCommand -Command 'docker' -Arguments @('compose', '-f', $composeFile, 'build', '--progress=plain', 'backend')
            Invoke-NativeCommand -Command 'docker' -Arguments @('compose', '-f', $composeFile, 'up', '-d', '--remove-orphans')

            if (-not (Test-BackendHealth)) {
                throw 'Rollback container did not pass its health check.'
            }

            Invoke-NativeCommand -Command 'git' -Arguments @('switch', 'master')
            Write-Host "Rollback succeeded. The running container uses $oldCommit."
        }
        catch {
            Write-Warning "Automatic rollback failed: $($_.Exception.Message)"
        }
    }

    throw $deploymentError
}
finally {
    Pop-Location
}
