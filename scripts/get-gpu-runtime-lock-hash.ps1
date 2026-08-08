[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "GPU runtime requirements lock is missing"
}
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$content = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $Path), $utf8)
$normalized = $content.Replace("`r`n", "`n").Replace("`r", "`n")
$bytes = [Text.UTF8Encoding]::new($false).GetBytes($normalized)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ""
} finally {
    $sha.Dispose()
}
