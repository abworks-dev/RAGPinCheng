"""Sandboxed PS 5.1 contracts; load function ASTs, never the production entrypoint.

All filesystem fixtures live below pytest's tmp_path. Process/task/network and
public release-manifest readers are mocked inside the isolated PowerShell process.
No production test switches or production credentials are required.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cleanup-production-current-only.ps1"
POWERSHELL = shutil.which("powershell.exe")


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def run_ps(tmp_path):
    if not POWERSHELL:
        pytest.skip("Windows PowerShell 5.1 is required for executable cleanup contracts")

    def run(body: str):
        # ParseFile validates the entire source, but only FunctionDefinitionAst
        # extents are evaluated. Top-level imports, guards and deletion never run.
        harness = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(SCRIPT_PATH, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($node in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}, $false)) {
    . ([scriptblock]::Create($node.Extent.Text))
}
Set-Location FIXTURE_PATH
$AsrDataRoot = Join-Path $PWD 'asr-data'
$AsrProgramRoot = Join-Path $PWD 'asr-program'
$RuntimeRoot = Join-Path $PWD 'gpu'
$ManifestPath = Join-Path $PWD 'approval.json'
$AuditPath = Join-Path $PWD 'audit.json'
$ExpectedAsrCandidateId = '9'
$ExpectedGpuReleaseId = 'aaaaaaaaaaaa-bbbbbbbbbbbb'
$WhisperXRoot = ''; $FasterWhisperQualificationRoot = ''
$Qwen3AsrQualificationRoot = ''; $GpuConfiguredModelCachePath = ''
$AsrHealthUrl = 'http://127.0.0.1:8200/health'
$GpuHealthUrl = 'http://127.0.0.1:8100/health'
$script:protected = New-Object 'System.Collections.Generic.List[string]'
$script:items = New-Object 'System.Collections.Generic.List[object]'
function Put([string]$relative, [string]$text='payload') {
    $p = Join-Path $PWD $relative
    [IO.Directory]::CreateDirectory((Split-Path $p -Parent)) | Out-Null
    [IO.File]::WriteAllText($p, $text)
    return $p
}
function Read-AsrReleaseManifest { param($ProgramRoot,$DataRoot,$CandidateId,$ExpectedSha256,[switch]$AllowLegacyWhisperXV1Profiles)
    if ($CandidateId -eq '404') { throw 'unknown release' }
    return [pscustomobject]@{candidate_id=$CandidateId}
}
function Get-CimInstance { param($ClassName,$ErrorAction) return @() }
function Get-ScheduledTask { param($ErrorAction) return @() }
function Invoke-RestMethod { param($Uri,$Method,$TimeoutSec)
    if ($Uri -like '*8200*') { return [pscustomobject]@{status='ok';api_version='asr-service/1'} }
    return [pscustomobject]@{status='ok';model_loaded=$true}
}
foreach ($p in @('asr-data/dependency-runs','asr-program/releases','gpu/releases','gpu/resolver')) {
    [IO.Directory]::CreateDirectory((Join-Path $PWD $p)) | Out-Null
}
function Expect-Throw([scriptblock]$Action,[string]$Pattern) {
    try { & $Action | Out-Null } catch {
        if ($_.Exception.Message -notlike $Pattern) { throw }
        return
    }
    throw "Expected exception: $Pattern"
}
BODY
""".replace("SCRIPT_PATH", _quote(SCRIPT)).replace("FIXTURE_PATH", _quote(tmp_path)).replace("BODY", body)
        encoded = base64.b64encode(harness.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    return run


def test_discovery_is_deterministic_and_preserves_current_unknown_and_evidence(run_ps):
    result = run_ps(r"""
foreach ($p in @(
 'asr-data/dependency-runs/candidate-2/venv/python.exe',
 'asr-data/dependency-runs/candidate-2/wheelhouse/pkg.whl',
 'asr-data/dependency-runs/candidate-2/pip-cache/pkg.whl',
 'asr-data/dependency-runs/candidate-9/venv/current.exe',
 'asr-data/dependency-runs/candidate-other/venv/unknown.exe',
 'asr-data/dependency-runs/candidate-2/logs/run.log',
 'asr-data/dependency-runs/candidate-2/report.json',
 'asr-program/releases/2/app/main.py',
 'asr-program/releases/2/venv/python.exe',
 'asr-program/releases/9/app/current.py',
 'asr-program/releases/404/app/unknown.py',
 'asr-program/releases/unknown/venv/unknown.exe',
 'gpu/releases/111111111111-222222222222/venv/python.exe',
 'gpu/releases/111111111111-222222222222/wheelhouse/pkg.whl',
 'gpu/releases/111111111111-222222222222/models/model.bin',
 'gpu/releases/aaaaaaaaaaaa-bbbbbbbbbbbb/venv/current.exe',
 'gpu/releases/333333333333-444444444444/venv/unqualified.exe',
 'gpu/resolver/5/venv/python.exe',
 'gpu/resolver/unknown/venv/unknown.exe')) { Put $p | Out-Null }
Put 'gpu/releases/111111111111-222222222222/runtime-manifest.json' '{"release_id":"111111111111-222222222222","qualification_status":"qualified","lock_validation_status":"validated"}' | Out-Null
$one = @(Discover @()); $two = @(Discover @())
if ((Json $one) -cne (Json $two)) { throw 'Nondeterministic discovery' }
$one | ForEach-Object { $_.path.Substring($PWD.Path.Length+1).Replace('\','/') } | ConvertTo-Json
""")
    assert json.loads(result) == sorted([
        "asr-data/dependency-runs/candidate-2/pip-cache",
        "asr-data/dependency-runs/candidate-2/venv",
        "asr-data/dependency-runs/candidate-2/wheelhouse",
        "asr-program/releases/2/app",
        "asr-program/releases/2/venv",
        "gpu/releases/111111111111-222222222222/venv",
        "gpu/releases/111111111111-222222222222/wheelhouse",
        "gpu/resolver/5/venv",
    ])


@pytest.mark.parametrize("artifact", [
    "models/weights.bin", "model-cache/weights.bin", "audit/result.json",
    "logs/output.txt", "data/value.bin", "config/settings.json", "evidence/result.txt",
    "parents.sqlite", "app.sqlite", "app.sqlite-wal", ".env", "run.log", ".active", "run.lock", "audit.json",
    "audit-result.json", "report.csv",
])
def test_protected_payload_is_never_selected(run_ps, artifact):
    run_ps("Put " + _quote("asr-data/dependency-runs/candidate-2/venv/" + artifact) + r""" | Out-Null
if ((Discover @()).Count -ne 0) { throw 'Protected artifact selected' }
""")


@pytest.mark.parametrize("marker", [".active", ".lock", "run.lock"])
def test_parent_run_markers_preserve_sibling_payload(run_ps, marker):
    run_ps("Put " + _quote("asr-data/dependency-runs/candidate-2/" + marker) + r""" | Out-Null
Put 'asr-data/dependency-runs/candidate-2/venv/python.exe' | Out-Null
if ((Discover @()).Count -ne 0) { throw 'Parent run marker ignored' }
""")


def test_process_task_and_manifest_references_preserve_entire_payload(run_ps):
    run_ps(r"""
$p = Put 'asr-data/dependency-runs/candidate-2/venv/python.exe'
$venv = Split-Path $p -Parent
if ((Discover @('python.exe --root "' + $venv + '"')).Count -ne 0) { throw 'Live reference selected' }
$script:protected.Add($p)
if ((Discover @()).Count -ne 0) { throw 'Protected descendant selected' }
$script:protected.Clear(); $script:protected.Add((Split-Path $venv -Parent))
if ((Discover @()).Count -ne 0) { throw 'Protected ancestor selected' }
function Get-CimInstance { param($ClassName,$ErrorAction)
    [pscustomobject]@{ProcessId=123456;ExecutablePath=$p;CommandLine='mock command'}
}
function Get-ScheduledTask { param($ErrorAction)
    [pscustomobject]@{Actions=@([pscustomobject]@{Execute='mock';Arguments=$venv;WorkingDirectory=$venv})}
}
$refs = References
if ($refs -notcontains $p -or $refs -notcontains $venv) { throw 'Reference inspection lost paths' }
""")


def test_process_inspection_failure_is_closed(run_ps):
    run_ps(r"""
function Get-CimInstance { throw 'access denied' }
Expect-Throw { References } '*inspection failed*'
""")


def test_tree_snapshot_detects_mutation_and_does_not_read_contents(run_ps):
    run_ps(r"""
$p = Put 'payload/code.py' 'first'
$root = Split-Path $p -Parent
$before = Tree $root -Payload
[IO.File]::WriteAllText($p,'different length')
$after = Tree $root -Payload
if ($before.snapshot_sha256 -eq $after.snapshot_sha256 -or $before.bytes -eq $after.bytes) { throw 'Snapshot drift missed' }
if ((Json (Tree $root -Payload)) -cne (Json $after)) { throw 'Unstable snapshot' }
""")


def test_reparse_point_fails_closed_without_following_target(run_ps):
    run_ps(r"""
Put 'outside/secret.txt' | Out-Null
Put 'payload/code.py' | Out-Null
$link = Join-Path $PWD 'payload/link'
New-Item -ItemType Junction -Path $link -Target (Join-Path $PWD 'outside') | Out-Null
Expect-Throw { Tree (Join-Path $PWD 'payload') -Payload } '*Reparse point*'
Expect-Throw { Assert-Ancestors (Join-Path $link 'secret.txt') } '*Reparse point*'
if (-not (Test-Path (Join-Path $PWD 'outside/secret.txt'))) { throw 'Target changed' }
""")


def test_manifest_collector_recurses_and_path_boundary_is_not_prefix(run_ps):
    run_ps(r"""
$keep = Join-Path $PWD 'payload'
Collect-Paths ([pscustomobject]@{nested=@(@{root=$keep},'relative/path')})
if ($script:protected.Count -ne 1 -or $script:protected[0] -ne $keep) { throw 'Closure missed' }
if (Under ($keep+'-other') $keep) { throw 'Prefix confusion' }
if (-not (Under (Join-Path $keep 'child') $keep)) { throw 'Child not recognized' }
Expect-Throw { FullPath 'relative/path' } '*absolute*'
""")


@pytest.mark.parametrize("url", ["https://127.0.0.1/health", "http://example.com/health", "http://localhost/other", "http://localhost/health?secret=x", "http://user@localhost/health"])
def test_health_rejects_nonlocal_or_credential_urls_without_network(run_ps, url):
    run_ps("$AsrHealthUrl = " + _quote(url) + r"""
function Invoke-RestMethod { throw 'NETWORK MUST NOT RUN' }
Expect-Throw { Health } '*Health URLs must be*'
""")


def test_health_mock_success_and_unhealthy_failclosed(run_ps):
    run_ps(r"""
$health = Health
if ($health.asr -ne 'healthy' -or $health.gpu -ne 'healthy') { throw 'Health mock failed' }
function Invoke-RestMethod { return [pscustomobject]@{status='failed';model_loaded=$false} }
Expect-Throw { Health } '*health guard failed*'
""")


def test_approval_raw_byte_hash_create_only_and_public_json_boundary(run_ps):
    run_ps(r"""
Write-Json $ManifestPath ([ordered]@{candidates=@();no_backup=$true}) -CreateOnly
$before = Hash-File $ManifestPath
Expect-Throw { Write-Json $ManifestPath @{} -CreateOnly } '*'
if ((Hash-File $ManifestPath) -ne $before) { throw 'Approval overwritten' }
[IO.File]::AppendAllText($ManifestPath," `n")
if ((Hash-File $ManifestPath) -eq $before) { throw 'Raw-byte mutation not detected' }
$p = Put 'private.env' 'NOT A CREDENTIAL'
Expect-Throw { Read-Json $p } '*Only public JSON metadata*'
""")


def test_discovery_rejects_approval_overlap(run_ps):
    run_ps(r"""
Put 'asr-data/dependency-runs/candidate-2/venv/python.exe' | Out-Null
$ManifestPath = Join-Path $AsrDataRoot 'dependency-runs/candidate-2/venv/approval.json'
Expect-Throw { Discover @() } '*overlaps payload*'
""")


@pytest.mark.parametrize("active", [
    {"candidate_id": "9"},
    {"schema_version": "asr-active-release/1", "candidate_id": "9", "release_manifest_sha256": "invalid"},
    {"schema_version": "unknown", "candidate_id": "9", "release_manifest_sha256": "a" * 64},
])
def test_live_rejects_missing_or_invalid_manifest_pin_before_manifest_read(run_ps, active):
    run_ps("Put 'asr-data/release-state/active.json' " + _quote(json.dumps(active)) + r""" | Out-Null
function Read-AsrReleaseManifest { throw 'MANIFEST READER MUST NOT RUN' }
Expect-Throw { Live } '*Active ASR candidate identity or manifest pin mismatch*'
""")


def test_live_manifest_closure_injection_identity_and_hash_drift(run_ps):
    run_ps(r"""
$gpuRoot = Join-Path $RuntimeRoot ('releases/' + $ExpectedGpuReleaseId)
$closure = Join-Path $AsrDataRoot 'dependency-runs/candidate-2/venv'
Put 'asr-data/dependency-runs/candidate-2/venv/python.exe' | Out-Null
$active = Put 'asr-data/release-state/active.json' '{"candidate_id":"9"}'
$asrManifest = Put 'asr-program/releases/9/release-manifest.json' (Json ([ordered]@{runtime=$closure}))
$pointer = Put 'gpu/current-release.json' (Json ([ordered]@{release_root=$gpuRoot;source_fingerprint='source';lock_sha256='lock'}))
$metadata = Put ('gpu/releases/' + $ExpectedGpuReleaseId + '/runtime-manifest.json') (Json ([ordered]@{
    release_id=$ExpectedGpuReleaseId; qualification_status='qualified';lock_validation_status='validated';
    source_fingerprint='source';lock_sha256='lock'}))
$validActive = Json ([ordered]@{schema_version='asr-active-release/1';candidate_id='9';release_manifest_sha256=(Hash-File $asrManifest)})
[IO.File]::WriteAllText($active,$validActive)
$first = Live
if (-not (Referenced $closure @())) { throw 'Current transitive manifest closure lost' }
if ((Discover @()).Count -ne 0) { throw 'Current transitive payload selected' }
[IO.File]::AppendAllText($active," `n")
$second = Live
if ($first.asr_state_sha256 -eq $second.asr_state_sha256) { throw 'Active raw-byte drift missed' }
[IO.File]::WriteAllText($active,'{"candidate_id":"8"}')
Expect-Throw { Live } '*Active ASR candidate identity*'
[IO.File]::WriteAllText($active,$validActive)
[IO.File]::WriteAllText($pointer,(Json ([ordered]@{release_root=(Join-Path $PWD 'outside');source_fingerprint='source';lock_sha256='lock'})))
Expect-Throw { Live } '*Current GPU release identity mismatch*'
[IO.File]::WriteAllText($pointer,(Json ([ordered]@{release_root=$gpuRoot;source_fingerprint='changed';lock_sha256='lock'})))
Expect-Throw { Live } '*Current GPU manifest contract mismatch*'
""")


# Reconstitute only the parsed operational statements inside an isolated
# scriptblock. The production file is never invoked/dot-sourced; all managed
# roots are temporary, Live is mocked, and real process/task/network are mocked.
_SANDBOX_ENTRY = r"""
$entryStatements = @(); $started = $false
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement.Extent.Text.StartsWith('$AsrDataRoot = FullPath')) { $started = $true }
    if ($started) { $entryStatements += $statement.Extent.Text }
}
if (-not $entryStatements.Count) { throw 'Operational AST not found' }
$entry = [scriptblock]::Create(($entryStatements -join "`n"))
function Live { return [ordered]@{asr_candidate_id='9';gpu_release_id=$ExpectedGpuReleaseId;pin='fixed'} }
$MaxDeleteGB = 1; $Mode = 'Preview'; $ConfirmNoBackup = $false; $ExpectedManifestSha256 = ''
$payload = Put 'asr-data/dependency-runs/candidate-2/venv/python.exe'
$payloadRoot = Split-Path $payload -Parent
$keep = Put 'asr-data/dependency-runs/candidate-9/venv/current.exe'
$unknown = Put 'asr-data/dependency-runs/unknown/venv/private.exe'
$evidence = Put 'asr-data/dependency-runs/candidate-2/report.json'
& $entry
$ExpectedManifestSha256 = Hash-File $ManifestPath
$Mode = 'Apply'; $ConfirmNoBackup = $true
$AuditPath = Join-Path $PWD 'apply-audit.json'
"""


def test_sandbox_preview_apply_deletes_only_approved_old_payload(run_ps):
    run_ps(_SANDBOX_ENTRY + r"""
& $entry
if (Test-Path $payloadRoot) { throw 'Approved payload not removed' }
foreach ($path in @($keep,$unknown,$evidence,$ManifestPath)) {
    if (-not (Test-Path $path)) { throw 'Retained path lost' }
}
$audit = Read-Json $AuditPath
if ($audit.status -ne 'completed' -or @($audit.deleted).Count -ne 1) { throw 'Missing completed audit' }
""")


@pytest.mark.parametrize("mutation, error", [
    ("$ConfirmNoBackup = $false", "*requires explicit ConfirmNoBackup*"),
    ("$ExpectedManifestSha256 = '0' * 64", "*raw manifest SHA256 mismatch*"),
    ("[IO.File]::AppendAllText($payload,'changed')", "*Approval differs from rediscovery*"),
    ("$approval = Read-Json $ManifestPath; $approval.candidates[0].path = $unknown; Write-Json $ManifestPath $approval; $ExpectedManifestSha256 = Hash-File $ManifestPath", "*Approval differs from rediscovery*"),
    ("Put 'asr-data/dependency-runs/candidate-2/run.lock' | Out-Null", "*Approval differs from rediscovery*"),
])
def test_sandbox_apply_guards_fail_closed_before_any_delete(run_ps, mutation, error):
    run_ps(_SANDBOX_ENTRY + mutation + "\nExpect-Throw { & $entry } " + _quote(error) + r"""
foreach ($path in @($payload,$keep,$unknown,$evidence)) {
    if (-not (Test-Path $path)) { throw 'Guard permitted deletion' }
}
if (Test-Path $AuditPath) { throw 'Preapproval rejection wrote apply audit' }
""")


def test_entrypoint_guards_precede_deletion_and_do_not_offer_test_bypass():
    """Static gates complement function tests; never invoke production entrypoint."""
    source = SCRIPT.read_text(encoding="utf-8-sig")
    delete = source.index("Remove-Item -LiteralPath $candidate.path")
    guards = [
        "Apply requires explicit ConfirmNoBackup and ExpectedManifestSha256",
        "Approved raw manifest SHA256 mismatch",
        "Approval differs from rediscovery",
        "Live identity drift before deletion",
        "Payload/reference drift before deletion",
        "Approval file changed",
        "Live identity drift during deletion",
        "New live reference; deletion stopped",
        "Payload snapshot drift; deletion stopped",
    ]
    for guard in guards:
        assert source.index(guard) < delete, guard
    assert source.index("Write-Json $AuditPath $audit -CreateOnly") < delete
    assert "[switch]$Skip" not in source
    assert "[switch]$Test" not in source
    assert "-WhatIf:$false" not in source
