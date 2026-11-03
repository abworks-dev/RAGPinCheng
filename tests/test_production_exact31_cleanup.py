"""Real Windows PS5.1 temporary-only tests; OS/health probes mocked, deletion real."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cleanup-production-exact31.ps1"
WORKFLOW = ROOT / ".github/workflows/cleanup-production-exact31.yml"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Requires Windows PowerShell5.1")

PRELUDE = r'''
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath $env:EXACT31_SOURCE -Raw
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'PowerShell parse failed' }
foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
 . ([scriptblock]::Create($function.Extent.Text))
}
$entry=[scriptblock]::Create($source.Substring($source.IndexOf('# ENTRYPOINT.')))
function Expect-Throw([scriptblock]$Action) { $caught=$false;try { & $Action } catch { $caught=$true };if (-not $caught) {throw 'Expected guard failure'} }
$fixture=$env:EXACT31_FIXTURE
$AsrDataRoot=Join-Path $fixture 'asr-data';$AsrProgramRoot=Join-Path $fixture 'asr-program';$RuntimeRoot=Join-Path $fixture 'runtime'
$ExpectedAsrCandidateId='35048453327';$ExpectedGpuReleaseId='e351a5d5d78f-f9a0f015f3e8'
$WhisperXRoot='';$FasterWhisperQualificationRoot='';$Qwen3AsrQualificationRoot='';$GpuConfiguredModelCachePath=''
$AsrHealthUrl='http://127.0.0.1:8200/health';$GpuHealthUrl='http://127.0.0.1:8100/health'
$ManifestPath=Join-Path $fixture 'manifest.json';$AuditPath=Join-Path $fixture 'preview-audit.json'
$ExpectedManifestSha256='';$ConfirmNoBackup=$false;$MaxDeleteGB=600;$Mode='Preview'
foreach ($path in @($AsrDataRoot,$AsrProgramRoot,$RuntimeRoot)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
function Put([string]$Path,[string]$Text) { New-Item -ItemType Directory -Path (Split-Path $Path -Parent) -Force | Out-Null;[IO.File]::WriteAllText($Path,$Text) }
$asrPath=Join-Path $AsrProgramRoot 'releases/35048453327/release-manifest.json'
Put $asrPath (Json @{schema_version='asr-production-release/1';candidate_id='35048453327'})
$activePath=Join-Path $AsrDataRoot 'release-state/active.json'
Put $activePath (Json @{schema_version='asr-active-release/1';candidate_id='35048453327';release_manifest_sha256=(Hash-File $asrPath)})
$gpuRoot=Join-Path $RuntimeRoot 'releases/e351a5d5d78f-f9a0f015f3e8'
$gpuPath=Join-Path $gpuRoot 'runtime-manifest.json';$pointerPath=Join-Path $RuntimeRoot 'current-release.json'
$gpuMeta=@{release_id='e351a5d5d78f-f9a0f015f3e8';qualification_status='qualified';lock_validation_status='validated';source_fingerprint='e351a5d5d78f';lock_sha256=('a'*64)}
Put $gpuPath (Json $gpuMeta)
Put $pointerPath (Json @{release_root=$gpuRoot;source_fingerprint=$gpuMeta.source_fingerprint;lock_sha256=$gpuMeta.lock_sha256})
$specs=Specs
if ($specs.Count -ne 31 -or @($specs | ForEach-Object {$_.path} | Sort-Object -Unique).Count -ne 31) {throw 'Fixed scope invalid'}
foreach ($spec in $specs) {
 Put (Join-Path $spec.path 'data/private.env') 'PRIVATE_CANARY'
 Put (Join-Path (Split-Path $spec.path -Parent) 'owner-audit.json') 'OWNER_AUDIT_CANARY'
}
$first=$specs[0].path;$secret=Join-Path $first 'data/private.env'
$outside=Join-Path $AsrDataRoot 'models/keep.bin';Put $outside 'MODEL_CANARY'
$unknown=Join-Path $AsrProgramRoot 'releases/99999999999/app/data/private.env';Put $unknown 'UNKNOWN_CANARY'
function References { return ,@() }
function Health { return @{asr='healthy';gpu='healthy'} }
function Preview { $script:Mode='Preview'; & $entry }
function Apply { $script:Mode='Apply';$script:ConfirmNoBackup=$true;$script:ExpectedManifestSha256=Hash-File $ManifestPath;$script:AuditPath=Join-Path $fixture 'apply-audit.json'; & $entry }
'''

def run_ps(tmp_path: Path, body: str) -> str:
    driver = tmp_path / "driver.ps1"
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    driver.write_text(PRELUDE + "\n" + body, encoding="utf-8")
    env = dict(os.environ, EXACT31_SOURCE=str(SCRIPT), EXACT31_FIXTURE=str(fixture))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRIVATE_CANARY" not in result.stdout + result.stderr
    return result.stdout


def test_actual_preview_apply_exact31_waiver_no_content_reads(tmp_path):
    run_ps(tmp_path, r'''
$locked=[IO.File]::Open($secret,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
try { Preview } finally { $locked.Dispose() }
$approved=Read-Json $ManifestPath
if ($approved.targets.Count -ne 31 -or @($approved.targets | Where-Object status -eq 'present').Count -ne 31) {throw 'Missing exact scope'}
Apply
$a=Read-Json $AuditPath
if ($a.status -ne 'completed' -or $a.deleted.Count -ne 31 -or $a.absent_after -ne 31 -or $a.remaining_after.Count -ne 0 -or $null -ne $a.pending) {throw 'Invalid completed audit'}
foreach ($s in (Specs)) {if ((Test-Path $s.path) -or -not (Test-Path (Join-Path (Split-Path $s.path -Parent) 'owner-audit.json'))) {throw 'Target/owner scope failure'}}
if (-not (Test-Path $outside) -or -not (Test-Path $unknown) -or -not (Test-Path $asrPath) -or -not (Test-Path $gpuPath)) {throw 'Protected outside scope deleted'}
''')


@pytest.mark.parametrize("mutation", [
    "Put (Join-Path $first '.active') 'marker'",
    "Put (Join-Path (Split-Path $first -Parent) 'run.lock') 'marker'",
    "Put (Join-Path $first 'data/.lock') 'marker'",
    "function References {return ,@('-CandidateId:34283259608')}",
    "function References {return ,@('-CandidateId \"34283259608\"')}",
    "function References {return ,@('D:/ServiceData/RAGPinCheng-ASR/config/releases/34283259608/asr.env')}",
    "function References {return ,@('candidate-34283259608')}",
    "function References {throw 'inspection failed'}",
    "function Health {throw 'unhealthy'}",
    "$WhisperXRoot=$first",
    "$GpuConfiguredModelCachePath=Join-Path $first 'data'",
    "Put $activePath (Json @{schema_version='asr-active-release/1';candidate_id='BAD';release_manifest_sha256=('a'*64)})",
    "Put $asrPath '{}'",
    "Put $pointerPath (Json @{release_root=(Split-Path $first -Parent)})",
    "Put $gpuPath '{}'",
])
def test_preview_guards_stop_before_audit_and_deletion(tmp_path, mutation):
    run_ps(tmp_path, mutation + r'''
Expect-Throw {Preview}
if (-not (Test-Path $secret) -or (Test-Path $ManifestPath)) {throw 'Unsafe guard behavior'}
''')


@pytest.mark.parametrize("mutation", [
    "Put $secret 'DRIFT'",
    "Put (Join-Path $first '.hidden-extra') 'extra'",
    "Put (Join-Path (Split-Path $first -Parent) '.lock') 'active'",
    "function References {return ,@('-CandidateId=34283259608')}",
    "Put $pointerPath (Json @{release_root='C:/wrong'})",
])
def test_apply_rejects_metadata_identity_reference_drift(tmp_path, mutation):
    run_ps(tmp_path, "Preview\n" + mutation + r'''
Expect-Throw {Apply}
if (-not (Test-Path $first)) {throw 'Deletion despite drift'}
''')


def test_forged_rehashed_path_and_raw_hash_confirmation(tmp_path):
    run_ps(tmp_path, r'''
Preview
$m=Read-Json $ManifestPath;$m.targets[0].path=$unknown;[IO.File]::WriteAllText($ManifestPath,(Json $m))
Expect-Throw {Apply}
if (-not (Test-Path $unknown) -or -not (Test-Path $first)) {throw 'Forged target deleted'}
$Mode='Apply';$ConfirmNoBackup=$false;Expect-Throw {& $entry}
$ConfirmNoBackup=$true;$ExpectedManifestSha256=('0'*64);Expect-Throw {& $entry}
''')


@pytest.mark.parametrize("phase", ["pointer", "tree", "reference"])
def test_late_pending_mutation_caught_before_remove(tmp_path, phase):
    mutation = {"pointer": "Put $pointerPath (Json @{release_root='C:/wrong'})", "tree": "Put (Join-Path $first 'late-new-file') 'new'", "reference": "$script:lateReference=$true"}[phase]
    run_ps(tmp_path, "Preview\n" + r'''
$script:writer=(Get-Command Write-Json).ScriptBlock
$script:mutated=$false;$script:lateReference=$false
function Write-Json([string]$Path,[object]$Value,[switch]$CreateOnly) {
 & $script:writer $Path $Value -CreateOnly:$CreateOnly
 if (-not $script:mutated -and (Prop $Value 'pending')) {
  $script:mutated=$true
''' + mutation + r'''
 }
}
function References {if ($script:lateReference) {return ,@('-CandidateId 34283259608')};return ,@()}
Expect-Throw {Apply}
$a=Read-Json $AuditPath
if (-not $script:mutated -or -not (Test-Path $first) -or $a.deleted.Count -ne 0 -or $a.status -ne 'failed-possibly-partial') {throw 'Late mutation not safely stopped'}
''')


def test_atomic_audit_replace_failure_keeps_previous_json(tmp_path):
    run_ps(tmp_path, r'''
$path=Join-Path $fixture 'atomic.json'
Write-Json $path @{pending='old';deleted=@()} -CreateOnly
$before=Hash-File $path
function Replace-AuditFile {throw 'Injected replacement failure'}
Expect-Throw {Write-Json $path @{pending='new';deleted=@('fake')}}
if ((Hash-File $path) -cne $before -or (Read-Json $path).pending -ne 'old') {throw 'Previous durable JSON lost'}
if (@(Get-ChildItem $fixture -Filter '.exact31-audit-*.tmp').Count) {throw 'Owned temporary file left behind'}
''')


def test_missing_noop_and_final_reappearance_not_completed(tmp_path):
    run_ps(tmp_path, r'''
Remove-Item -LiteralPath $first -Recurse -Force
Preview
if ((Read-Json $ManifestPath).targets[0].status -ne 'missing') {throw 'Missing target not explicit'}
$script:writer=(Get-Command Write-Json).ScriptBlock;$script:recreated=$false
function Write-Json([string]$Path,[object]$Value,[switch]$CreateOnly) {
 & $script:writer $Path $Value -CreateOnly:$CreateOnly
 if (-not $script:recreated -and (Prop $Value 'mode') -eq 'Apply' -and @($Value.deleted).Count -eq 30 -and -not $Value.pending) {$script:recreated=$true;Put (Join-Path $first 'reappeared') 'new'}
}
Expect-Throw {Apply}
$a=Read-Json $AuditPath
if (-not $script:recreated -or $a.status -ne 'failed-possibly-partial' -or $a.absent_after -ne 30 -or $a.remaining_after.Count -ne 1) {throw 'Final absence proof missing'}
''')


def test_reparse_descendant_and_owner_fail_closed(tmp_path):
    run_ps(tmp_path, r'''
$external=Join-Path $fixture 'external';New-Item -ItemType Directory $external | Out-Null
$link=Join-Path $first 'data/junction'
New-Item -ItemType Junction -Path $link -Target $external | Out-Null
try {Expect-Throw {Preview};if (Test-Path $ManifestPath) {throw 'Junction accepted'}} finally {[IO.Directory]::Delete($link)}
''')


def test_all_disabled_task_and_process_reference_fields(tmp_path):
    run_ps(tmp_path, r'''
. ([scriptblock]::Create(($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'References'},$false))[0].Extent.Text))
function Get-CimInstance {return @([pscustomobject]@{ProcessId=999;ExecutablePath='exe-marker';CommandLine='cmd-marker'})}
function Get-ScheduledTask {return @([pscustomobject]@{TaskName='task-34283259608';TaskPath='taskpath';State='Disabled';Actions=@([pscustomobject]@{Execute='execute-marker';Arguments='arg-marker';WorkingDirectory='wd-marker'})})}
$r=References
foreach ($expected in @('exe-marker','cmd-marker','task-34283259608','taskpath','execute-marker','arg-marker','wd-marker')) {if ($expected -notin $r) {throw 'Inspection field missing'}}
$script:protected=New-Object 'System.Collections.Generic.List[string]'
if (-not (Referenced $specs[0] $r)) {throw 'Disabled task ignored'}
''')


def test_preview_metadata_exception_redacts_private_descendant(tmp_path):
    run_ps(tmp_path, r'''
function Tree([string]$Path) {throw 'PRIVATE_CUSTOMER_FILENAME_CANARY/secret-document.env'}
try {Preview;throw 'Expected metadata failure'} catch {
 if ($_.Exception.Message -notlike '*read-metadata-failed' -or ($_ | Out-String) -match 'PRIVATE_CUSTOMER_FILENAME_CANARY|secret-document') {throw 'Private metadata leaked or guard did not stop'}
}
if (-not (Test-Path $first) -or (Test-Path $ManifestPath) -or (Test-Path $AuditPath)) {throw 'Metadata failure changed payload or created approval'}
''')


def test_native_workflow_attempt_provenance_and_api_fail_closed(tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = next(s["run"] for s in workflow["jobs"]["cleanup"]["steps"] if s.get("name", "").startswith("Verify exact"))
    run_ps(tmp_path, r'''
$env:PREVIEW_RUN_ID='12345';$env:PREVIEW_ARTIFACT='production-exact31-12345-2-preview'
$env:EXPECTED_COMMIT=('a'*40);$env:WORKFLOW_TOKEN='FAKE_TEST_ONLY';$env:GITHUB_API_URL='https://api.github.invalid';$env:GITHUB_REPOSITORY='test/repo'
$script:run=[pscustomobject]@{id=12345;run_attempt=2;conclusion='success';event='workflow_dispatch';head_sha=('a'*40);head_branch='master';path='.github/workflows/cleanup-production-exact31.yml'}
function Invoke-RestMethod {param($Uri,$Headers,$Method,$TimeoutSec,$MaximumRedirection)
 if ($Uri -cne 'https://api.github.invalid/repos/test/repo/actions/runs/12345/attempts/2' -or -not $PSBoundParameters.ContainsKey('MaximumRedirection') -or $MaximumRedirection -ne 0) {throw 'Invalid request'}
 return $script:run
}
$block={
''' + code + r'''
}
& $block
foreach ($pair in @(@('id',999),@('run_attempt',1),@('conclusion','failure'),@('event','push'),@('head_sha',('b'*40)),@('head_branch','other'),@('path','.github/workflows/cleanup-production-current-only.yml'))) {
 $name=$pair[0];$before=$script:run.$name;$script:run.$name=$pair[1];Expect-Throw {& $block};$script:run.$name=$before
}
function Invoke-RestMethod {throw 'Injected API failure'}
Expect-Throw {& $block}
''')


def test_native_workflow_approved_production_root_tuple(tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = workflow["jobs"]["cleanup"]["steps"][0]["run"]
    run_ps(tmp_path, r'''
$env:GITHUB_REF='refs/heads/master';$env:EXPECTED_COMMIT=('a'*40);$env:GITHUB_SHA=('a'*40);$env:OPERATION='preview'
$env:ASR_DATA_ROOT='D:/ServiceData/RAGPinCheng-ASR';$env:ASR_PROGRAM_ROOT='D:/Services/RAGPinCheng-ASR';$env:RUNTIME_ROOT='d:\ragpincheng\runtime\'
$block={
''' + code + r'''
}
& $block
foreach ($name in @('ASR_DATA_ROOT','ASR_PROGRAM_ROOT','RUNTIME_ROOT')) {
 $before=[Environment]::GetEnvironmentVariable($name);[Environment]::SetEnvironmentVariable($name,'C:/wrong')
 Expect-Throw {& $block};[Environment]::SetEnvironmentVariable($name,$before)
}
$env:OPERATION='apply';$env:CONFIRM_NO_BACKUP='false';Expect-Throw {& $block}
$env:CONFIRM_NO_BACKUP='true';$env:PREVIEW_RUN_ID='12345';$env:PREVIEW_ARTIFACT='production-exact31-12345-2-preview';$env:MANIFEST_SHA256=('a'*64);& $block
$env:PREVIEW_ARTIFACT='production-current-only-12345-2-preview';Expect-Throw {& $block}
''')


def test_actual_health_local_ipv4_predicates_and_no_redirect(tmp_path):
    run_ps(tmp_path, r'''
. ([scriptblock]::Create(($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Health'},$false))[0].Extent.Text))
$script:healthCalls=0
function Get-NetIPAddress {param($AddressFamily) return @([pscustomobject]@{IPAddress='192.168.11.11'})}
function Invoke-RestMethod {param($Uri,$Method,$TimeoutSec,$MaximumRedirection)
 if (-not $PSBoundParameters.ContainsKey('MaximumRedirection') -or $MaximumRedirection -ne 0) {throw 'Redirect option missing'}
 $script:healthCalls++
 if ($Uri -match ':8200/health$') {return [pscustomobject]@{status='ok';api_version='asr-service/1'}}
 return [pscustomobject]@{status='ok';model_loaded=$true}
}
$AsrHealthUrl='http://192.168.11.11:8200/health';$GpuHealthUrl='http://192.168.11.11:8100/health'
Health | Out-Null
if ($script:healthCalls -ne 2) {throw 'Assigned-LAN health not requested'}
foreach ($bad in @('http://192.168.11.12:8100/health','http://192.168.11.11:8200/health','http://example.invalid:8100/health','http://127.0.0.1:8100/health?x=y')) {$GpuHealthUrl=$bad;Expect-Throw {Health}}
if ($script:healthCalls -ne 2) {throw 'Invalid URL reached network'}
$GpuHealthUrl='http://192.168.11.11:8100/health'
function Invoke-RestMethod {return [pscustomobject]@{status='ok';api_version='asr-service/1';model_loaded=$false}}
Expect-Throw {Health}
''')


def test_script_and_workflow_narrow_static_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "Read-AsrReleaseManifest" not in text
    assert "asr-release.ps1" not in text
    assert "[IO.FileMode]::Create }" not in text
    assert "[IO.File]::Replace" in text
    assert "absent_after" in text and "Late per-target snapshot drift" in text
    assert "Get-ChildItem -LiteralPath $directory -Force" in text
    assert "Remove-Item -LiteralPath $row.path -Recurse -Force" in text
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    dispatch = workflow.get("on", workflow.get(True))
    assert set(dispatch) == {"workflow_dispatch"}
    steps = workflow["jobs"]["cleanup"]["steps"]
    assert {s["uses"] for s in steps if "uses" in s} == {"actions/checkout@v4", "actions/download-artifact@v4", "actions/upload-artifact@v4"}
    provenance = next(s["run"] for s in steps if s.get("name", "").startswith("Verify exact"))
    for required in ("/attempts/$attempt", "$run.conclusion", "$run.path", "$run.head_sha", "$run.run_attempt", "$run.id", ".github/workflows/cleanup-production-exact31.yml"):
        assert required in provenance
    validation = steps[0]["run"]
    for root in ("D:/ServiceData/RAGPinCheng-ASR", "D:/Services/RAGPinCheng-ASR", "D:/RAGPinCheng/runtime"):
        assert root in validation
