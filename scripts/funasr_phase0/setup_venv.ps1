<#
.SYNOPSIS
    Create the isolated FunASR Phase 0 ASR sandbox venv on a Windows host
    and install ASR-only dependencies. Compatible with Windows PowerShell 5.1.

.DESCRIPTION
    Per R2 fix spec §九:
      - Refuses to create the venv under the production gpu_service path,
        under the project repo venv, or inside the repo directory.
      - Installs torch + torchaudio from the cu128 index ONLY.
      - Installs everything else from scripts\funasr_phase0\requirements-asr.txt
        via the approved mirror.
      - After install, runs `pip check` and verifies torch.__version__
        contains `+cu128`.
      - Returns non-zero if CUDA is unavailable.
      - -SkipInstall skips pip operations but validates an existing ready venv.
      - Writes the actual pip freeze + a SHA-256 of the requirements file.

.PARAMETER PythonExe
    Path to the Python interpreter. Configure `PRODUCTION_PYTHON_PATH` in the private environment.
    (matches scripts\deploy-gpu.ps1 §5 on the production Windows GPU host).

.PARAMETER VenvDir
    Target venv path. Default: C:\FunASR-Phase0\venv

.PARAMETER RequirementsFile
    Default: <repo>\scripts\funasr_phase0\requirements-asr.txt

.PARAMETER SkipInstall
    Skip both pip upgrade AND dependency install and validate an existing venv.
    It is rejected when the venv does not already exist.

.PARAMETER TorchIndex
    Default: https://download.pytorch.org/whl/cu128

.PARAMETER PypiIndex
    Default: https://pypi.tuna.tsinghua.edu.cn/simple

.EXAMPLE
    PS> .\setup_venv.ps1
#>

[CmdletBinding()]
param(
    [string]$PythonExe = $env:PRODUCTION_PYTHON_PATH,
    [string]$VenvDir = "C:\FunASR-Phase0\venv",
    [string]$RequirementsFile = "",
    [switch]$SkipInstall,
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu128",
    [string]$PypiIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$LogRoot = Join-Path $env:QUALIFICATION_SANDBOX_ROOT "logs"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$DefaultRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $RequirementsFile) {
    $RequirementsFile = Join-Path $DefaultRepoRoot "scripts\funasr_phase0\requirements-asr.txt"
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n>> $Message" -ForegroundColor Cyan
}

function Fail {
    param([string]$Message)
    Write-Host "`n!! $Message" -ForegroundColor Red
    exit 1
}

function Invoke-PipLogged {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PipArguments,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage,
        [switch]$ReplaceLog
    )

    # Windows venvs refuse to upgrade pip through the generated pip launcher; invoke the
    # module with the venv interpreter instead.  PowerShell 5.1 can also turn a
    # native program's stderr into a terminating NativeCommandError while the
    # script-wide preference is Stop, so capture it under Continue and decide
    # success solely from the native exit code.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($ReplaceLog) {
            & $VenvPython -m pip @PipArguments 2>&1 |
                Tee-Object -FilePath $installLog | Out-Null
        } else {
            & $VenvPython -m pip @PipArguments 2>&1 |
                Tee-Object -FilePath $installLog -Append | Out-Null
        }
        $pipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    if ($pipExitCode -ne 0) {
        Fail "$FailureMessage (exit $pipExitCode). See $installLog."
    }
}

# ── 0. Pre-flight: refuse bad venv locations ────────────────────────────────
Write-Step "0) Pre-flight location check"
$bad_prefixes = @(
    (Join-Path $DefaultRepoRoot ".venv"),                       # project venv
    (Join-Path (Split-Path -Parent $PythonExe) "Lib\site-packages"), # private production environment
    $DefaultRepoRoot                                            # inside repo
)
foreach ($p in $bad_prefixes) {
    if ($VenvDir.StartsWith($p, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "Refusing to place ASR venv under $p (would collide with project or production venv). Use C:\FunASR-Phase0\venv or similar."
    }
}

# ── 1. Pre-flight: Python + requirements ─────────────────────────────────────
Write-Step "1) Python and requirements check"
if (-not (Test-Path $PythonExe)) {
    Fail "Python not found at $PythonExe. Pass -PythonExe."
}
if (-not (Test-Path $RequirementsFile)) {
    Fail "Requirements file not found: $RequirementsFile"
}
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$installLog = Join-Path $LogRoot "install-$Stamp.log"
$freezeLog = Join-Path $LogRoot "freeze-$Stamp.log"
$reqSha = (Get-FileHash -Algorithm SHA256 -Path $RequirementsFile).Hash
Write-Host "  requirements sha256: $reqSha"

# ── 2. Create venv ───────────────────────────────────────────────────────────
Write-Step "2) Create venv at $VenvDir"
$venvExisted = Test-Path $VenvDir
if ($SkipInstall -and -not $venvExisted) {
    Fail "-SkipInstall requires an existing ready venv: $VenvDir"
}
if (-not $venvExisted) {
    & $PythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "venv create failed (exit $LASTEXITCODE)." }
    Write-Host "  created $VenvDir"
} else {
    Write-Host "  (exists) reusing $VenvDir"
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Fail "venv python missing: $VenvPython" }

if ($SkipInstall) {
    Write-Step "3) SkipInstall set; skipping pip upgrade and dep install"
} else {
    # ── 3. Upgrade pip ───────────────────────────────────────────────────────
    Write-Step "3) Upgrade pip (via $PypiIndex)"
    Invoke-PipLogged -PipArguments @("install", "--upgrade", "pip", "-i", $PypiIndex) `
        -FailureMessage "pip upgrade failed"

    # ── 4. Install torch + torchaudio from cu128 index ONLY ─────────────────
    Write-Step "4) Install torch + torchaudio (cu128 index ONLY)"
    Write-Host "  index: $TorchIndex"
    Invoke-PipLogged -PipArguments @(
        "install", "--index-url", $TorchIndex,
        "torch==2.7.0", "torchaudio==2.7.0"
    ) -FailureMessage "torch install failed"

    # ── 5. Install everything else from approved mirror ────────────────────
    Write-Step "5) Install requirements-asr.txt (via $PypiIndex)"
    Invoke-PipLogged -PipArguments @(
        "install", "-i", $PypiIndex, "-r", $RequirementsFile
    ) -FailureMessage "deps install failed"

    # ── 6. Run pip check ────────────────────────────────────────────────────
    Write-Step "6) pip check"
    Invoke-PipLogged -PipArguments @("check") `
        -FailureMessage "pip check found conflicts"
}

# ── 7. Freeze snapshot + SHA-256 of freeze ───────────────────────────────────
Write-Step "7) Write freeze snapshot + SHA-256"
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $VenvPython -m pip freeze 2>&1 |
        Tee-Object -FilePath $freezeLog | Out-Null
    $freezeExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $oldPreference
}
if ($freezeExitCode -ne 0) {
    Fail "pip freeze failed (exit $freezeExitCode). See $freezeLog."
}
$freezeSha = (Get-FileHash -Algorithm SHA256 -Path $freezeLog).Hash
Write-Host "  freeze log: $freezeLog"
Write-Host "  freeze sha256: $freezeSha"

# ── 8. Verify torch.__version__ contains +cu128 ─────────────────────────────
Write-Step "8) Verify torch build is cu128"
$torchVer = & $VenvPython -c "import torch; print(torch.__version__)" 2>&1
$torchVerStr = ($torchVer | Select-Object -Last 1).ToString().Trim()
Write-Host "  torch.__version__ = $torchVerStr"
if (-not $torchVerStr.Contains("+cu128")) {
    Fail "torch $torchVerStr does NOT include +cu128; expected a cu128 build."
}

# ── 9. Verify CUDA availability (NON-ZERO if not available) ─────────────────
Write-Step "9) Verify CUDA is available"
$cudaProbe = & $VenvPython -c @"
import torch, sys
ok = torch.cuda.is_available()
print('cuda_avail', ok)
if not ok:
    sys.exit(2)
"@ 2>&1
$cudaProbe | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "CUDA not available in $VenvPython (exit $LASTEXITCODE)." }

# ── 10. Final summary ────────────────────────────────────────────────────────
Write-Step "Done"
Write-Host "  venv         : $VenvDir"
Write-Host "  python       : $VenvPython"
Write-Host "  freeze log   : $freezeLog  (sha256 $freezeSha)"
Write-Host "  install log  : $installLog"
Write-Host "  requirements : $RequirementsFile  (sha256 $reqSha)"
Write-Host ""
Write-Host "Activate with:" -ForegroundColor Green
Write-Host "  & '$VenvDir\Scripts\Activate.ps1'"
Write-Host ""
Write-Host "NEVER run ASR inside the gpu_service venv or process." -ForegroundColor Yellow
exit 0
