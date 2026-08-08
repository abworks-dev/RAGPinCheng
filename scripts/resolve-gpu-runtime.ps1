[CmdletBinding()]
param(
    [string]$RepositoryPath = "D:\RAGPinCheng",
    [string]$BasePython = "C:\Program Files\Python310\python.exe",
    [string]$ResolverRoot = "D:\RAGPinCheng\runtime\resolver",
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+-[0-9]+$')][string]$RunId,
    [Parameter(Mandatory)][string]$ModelCacheSource,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [Int64]$MinimumFreeBytes = 20GB
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-SanitizedLogTail {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path -Tail 120 -ErrorAction SilentlyContinue) {
        $safeLine = [string]$line
        $safeLine = $safeLine -replace '(?i)(https?://)[^/\s:@]+:[^@\s]+@', '$1[REDACTED]@'
        $safeLine = $safeLine -replace '(?i)\b(Bearer|Token|Password|Secret)\s*[:=]\s*\S+', '$1=[REDACTED]'
        $safeLine = $safeLine -replace '(?i)(?:[A-Z]:\\|\\\\)[^\s"''<>]+', '[PATH]'
        if ($safeLine.Length -gt 1000) {
            $safeLine = $safeLine.Substring(0, 1000) + " [TRUNCATED]"
        }
        Write-Host "GPU_RUNTIME_RESOLVER_DIAGNOSTIC $safeLine"
    }
}

function Invoke-LoggedExternal {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][string]$Failure
    )

    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments *>> $LogPath
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($exitCode -ne 0) {
        Write-SanitizedLogTail -Path $LogPath
        throw "$Failure (exit $exitCode; details retained in the run-local log)"
    }
}

function ConvertTo-Version {
    param([Parameter(Mandatory)][string]$Value, [Parameter(Mandatory)][string]$PackageName)
    try {
        return [Version]$Value
    } catch {
        throw "Resolved $PackageName version is not a stable numeric version"
    }
}

$approvedPackageIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
$approvedTorchIndex = "https://download.pytorch.org/whl/cu128"
$resolvedResolverRoot = [IO.Path]::GetFullPath($ResolverRoot).TrimEnd("\")
if (-not $resolvedResolverRoot.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU runtime dependency resolution must stay under D:\"
}
if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) {
    throw "Base Python is missing"
}
if (-not (Test-Path -LiteralPath $ModelCacheSource -PathType Container)) {
    throw "Model cache source is missing"
}
if ($null -eq (Get-ChildItem -LiteralPath $ModelCacheSource -Force -ErrorAction Stop | Select-Object -First 1)) {
    throw "Model cache source is empty"
}

$pythonVersion = ((& $BasePython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')") -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^3\.10\.[0-9]+$') {
    throw "GPU runtime resolution requires Python 3.10"
}
$drive = Get-PSDrive -Name D -PSProvider FileSystem -ErrorAction Stop
if ([Int64]$drive.Free -lt $MinimumFreeBytes) {
    throw "D: does not have the required free space for isolated dependency resolution"
}
$listener = Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue
if ($null -ne $listener) {
    throw "Refusing dependency resolution while production port 8100 is listening"
}
$productionTask = Get-ScheduledTask -TaskName "RAGPinCheng-GPU" -ErrorAction SilentlyContinue
if ($null -ne $productionTask) {
    throw "Refusing dependency resolution while the production GPU task exists"
}

$head = ((& git -C $RepositoryPath rev-parse HEAD) -join "").Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $head -ne $CommitSha.ToLowerInvariant()) {
    throw "GPU runtime resolver repository HEAD does not match the requested commit"
}
& git -C $RepositoryPath diff --quiet $CommitSha -- `
    scripts/resolve-gpu-runtime.ps1 `
    gpu_service/requirements.txt `
    gpu_service/runtime-lock.json
if ($LASTEXITCODE -ne 0) {
    throw "GPU runtime resolver contract has uncommitted changes"
}

$runRoot = Join-Path $resolvedResolverRoot $RunId
if (Test-Path -LiteralPath $runRoot) {
    throw "Resolver run directory already exists"
}
$venvRoot = Join-Path $runRoot "venv"
$tempRoot = Join-Path $runRoot "temp"
$artifactRoot = Join-Path $runRoot "artifacts"
$logRoot = Join-Path $runRoot "logs"
New-Item -ItemType Directory -Path $tempRoot, $artifactRoot, $logRoot -Force | Out-Null

$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:PIP_CACHE_DIR = Join-Path $resolvedResolverRoot "pip-cache"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_INPUT = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
New-Item -ItemType Directory -Path $env:PIP_CACHE_DIR -Force | Out-Null

$torchRequirement = "torch==2.7.0+cu128"
$runtimeConstraints = @(
    "FlagEmbedding>=1.3,<2",
    "transformers>=4.47,<5",
    "tokenizers>=0.21,<0.22",
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.32,<1",
    "pydantic>=2,<3",
    "python-dotenv>=1,<2"
)
$constraints = @($torchRequirement) + $runtimeConstraints
$preflight = [ordered]@{
    schema_version = 1
    status = "passed"
    run_id = $RunId
    repository_commit = $head
    python_version = $pythonVersion
    d_drive_free_bytes = [Int64]$drive.Free
    minimum_free_bytes = $MinimumFreeBytes
    model_cache_source_present = $true
    model_cache_source_nonempty = $true
    production_port_8100_listening = $false
    production_gpu_task_present = $false
    resolver_root_drive = "D:"
}
$preflightPath = Join-Path $artifactRoot "preflight.json"
[IO.File]::WriteAllText(
    $preflightPath,
    (($preflight | ConvertTo-Json -Depth 4) + "`n"),
    [Text.UTF8Encoding]::new($false)
)

Write-Host "GPU_RUNTIME_RESOLVER stage=create_venv"
$venvLog = Join-Path $logRoot "venv.log"
Invoke-LoggedExternal -FilePath $BasePython -Arguments @("-m", "venv", $venvRoot) `
    -LogPath $venvLog -Failure "Unable to create the D-drive resolver venv"
$resolverPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $resolverPython -PathType Leaf)) {
    throw "Resolver venv Python is missing"
}

Write-Host "GPU_RUNTIME_RESOLVER stage=resolve_cuda_torch"
$noProxyHosts = @(
    @($env:NO_PROXY -split ','),
    "download.pytorch.org"
) | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique
$env:NO_PROXY = $noProxyHosts -join ","
$env:no_proxy = $env:NO_PROXY
$installLog = Join-Path $logRoot "pip-install.log"
Invoke-LoggedExternal -FilePath $resolverPython -Arguments @(
    "-m", "pip", "install",
    "--index-url", $approvedTorchIndex,
    "--prefer-binary", "--no-deps", $torchRequirement
) -LogPath $installLog -Failure "CUDA Torch resolution failed"

Write-Host "GPU_RUNTIME_RESOLVER stage=resolve_dependencies"
$installArguments = @(
    "-m", "pip", "install",
    "--index-url", $approvedPackageIndex,
    "--prefer-binary"
) + $constraints
Invoke-LoggedExternal -FilePath $resolverPython -Arguments $installArguments `
    -LogPath $installLog -Failure "GPU runtime dependency resolution failed"

Write-Host "GPU_RUNTIME_RESOLVER stage=pip_check"
$checkLog = Join-Path $logRoot "pip-check.log"
Invoke-LoggedExternal -FilePath $resolverPython -Arguments @("-m", "pip", "check") `
    -LogPath $checkLog -Failure "Resolved GPU runtime dependency closure is inconsistent"

$packageJson = ((& $resolverPython -m pip list --format=json) -join "")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect resolved GPU runtime packages"
}
$packages = $packageJson | ConvertFrom-Json
$packageMap = @{}
foreach ($package in $packages) {
    $packageMap[[string]$package.name.ToLowerInvariant()] = [string]$package.version
}
foreach ($requiredName in @("torch", "flagembedding", "transformers", "tokenizers", "fastapi", "uvicorn", "pydantic", "python-dotenv")) {
    if (-not $packageMap.ContainsKey($requiredName)) {
        throw "Resolved GPU runtime is missing required package $requiredName"
    }
}
if ($packageMap["torch"] -ne "2.7.0+cu128") {
    throw "Resolver did not select the approved CUDA Torch build"
}
$flagEmbeddingVersion = ConvertTo-Version -Value $packageMap["flagembedding"] -PackageName "FlagEmbedding"
$transformersVersion = ConvertTo-Version -Value $packageMap["transformers"] -PackageName "transformers"
$tokenizersVersion = ConvertTo-Version -Value $packageMap["tokenizers"] -PackageName "tokenizers"
if ($flagEmbeddingVersion -lt [Version]"1.3" -or $flagEmbeddingVersion -ge [Version]"2.0") {
    throw "Resolved FlagEmbedding version is outside the approved candidate range"
}
if ($transformersVersion -lt [Version]"4.47" -or $transformersVersion -ge [Version]"5.0") {
    throw "Resolved transformers version is outside the approved candidate range"
}
if ($tokenizersVersion -lt [Version]"0.21" -or $tokenizersVersion -ge [Version]"0.22") {
    throw "Resolved tokenizers version is outside the approved candidate range"
}
if ($packageMap["transformers"] -eq "4.46.3" -or $packageMap["tokenizers"] -eq "0.20.3") {
    throw "Resolver selected a package version from the rejected runtime combination"
}

$freeze = @(& $resolverPython -m pip freeze)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to freeze the resolved GPU runtime"
}
$lockLines = @(
    $freeze |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ } |
        Sort-Object { ($_ -split "==", 2)[0].ToLowerInvariant() }
)
if ($lockLines.Count -eq 0) {
    throw "Resolved GPU runtime lock is empty"
}
foreach ($line in $lockLines) {
    if ($line -notmatch '^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$') {
        throw "Resolved GPU runtime contains a non-exact or non-index requirement"
    }
}
$lockText = ($lockLines -join "`n") + "`n"
$lockPath = Join-Path $artifactRoot "runtime-lock-candidate.txt"
$freezePath = Join-Path $artifactRoot "pip-freeze.txt"
[IO.File]::WriteAllText($lockPath, $lockText, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($freezePath, $lockText, [Text.UTF8Encoding]::new($false))
$lockHash = & (Join-Path $RepositoryPath "scripts\get-gpu-runtime-lock-hash.ps1") -Path $lockPath
if ($lockHash -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute candidate GPU runtime lock hash"
}

$selected = [ordered]@{}
foreach ($name in @("torch", "flagembedding", "transformers", "tokenizers", "fastapi", "uvicorn", "pydantic", "python-dotenv")) {
    $selected[$name] = $packageMap[$name]
}
$report = [ordered]@{
    schema_version = 1
    status = "resolved"
    run_id = $RunId
    repository_commit = $head
    python_version = $pythonVersion
    package_index = "pypi.tuna.tsinghua.edu.cn"
    torch_index = "download.pytorch.org/whl/cu128"
    constraints = $constraints
    selected_packages = $selected
    package_count = $lockLines.Count
    pip_check = "passed"
    lock_sha256 = $lockHash
    resolved_at = [DateTimeOffset]::UtcNow.ToString("o")
}
$reportPath = Join-Path $artifactRoot "resolver-report.json"
[IO.File]::WriteAllText(
    $reportPath,
    (($report | ConvertTo-Json -Depth 6) + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Write-Host "GPU_RUNTIME_RESOLVER status=resolved package_count=$($lockLines.Count) lock_sha256=$lockHash"
exit 0
