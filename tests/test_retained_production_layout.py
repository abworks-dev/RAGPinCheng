"""Exercise the standalone PS5.1 observer only on disposable metadata fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/diagnose-retained-production-layout.ps1"
WORKFLOW = ROOT / ".github/workflows/diagnose-retained-production-layout.yml"
PS = shutil.which("powershell.exe")
requires_ps = pytest.mark.skipif(not PS, reason="Windows PowerShell 5.1 required")
OLD = "34283259608"
CURRENT = "35048453327"
GPU = "e351a5d5d78f-f9a0f015f3e8"


@pytest.fixture
def layout(tmp_path):
    program = tmp_path / "program"
    runtime = tmp_path / "runtime"
    reports = tmp_path / "reports"
    for path in (program, runtime, reports):
        path.mkdir()
    return program, runtime, reports


def invoke(layout, *extra, expected_asr=CURRENT, report=None):
    program, runtime, reports = layout
    report = report or reports / "result.json"
    env = dict(os.environ, RUNNER_TEMP=str(reports))
    result = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), "-AsrProgramRoot", str(program),
         "-RuntimeRoot", str(runtime), "-ExpectedAsrCandidateId", expected_asr,
         "-ExpectedGpuReleaseId", GPU, "-ReportPath", str(report), *map(str, extra)],
        capture_output=True, text=True, env=env, timeout=40,
    )
    return result, json.loads(report.read_text(encoding="utf-8-sig")) if report.is_file() else None


def app(layout):
    return layout[0] / "releases" / OLD / "app"


def venv(layout):
    return layout[0] / "releases" / OLD / "venv"


def row(report, kind, identity=OLD):
    return next(r for r in report["payloads"] if r["kind"] == kind and r["identity"] == identity)


def assert_retained(report):
    assert report["schema_version"] == "retained-production-layout/1"
    assert report["read_only"] is True
    assert report["content_reads"] is False
    assert report["eligibility_change"] is False
    assert len(report["payloads"]) == 31
    assert all(r["retained"] is True for r in report["payloads"])


@requires_ps
def test_fixed_scope_missing_rows_are_not_clean(layout):
    result, report = invoke(layout)
    assert result.returncode == 0, result.stderr
    assert_retained(report)
    assert all(r["status"] == "missing" for r in report["payloads"])
    assert all(r["reason_code"] == "missing" for r in report["payloads"])
    assert sum(r["kind"] == "asr-app" for r in report["payloads"]) == 14
    assert sum(r["kind"] == "asr-venv" for r in report["payloads"]) == 14


@requires_ps
def test_source_shaped_boundary_never_reads_or_lists_children(layout):
    boundary = app(layout) / "src/transcription"
    boundary.mkdir(parents=True)
    secret = boundary / "private-client-transcript-canary.txt"
    secret.write_text("NEVER-OUTPUT-PRIVATE-BODY", encoding="utf-8")
    result, report = invoke(layout)
    assert result.returncode == 0, result.stderr
    assert_retained(report)
    observed = row(report, "asr-app")
    assert observed["status"] == "observed"
    assert observed["boundary_counts"] == {"source_shaped": 1, "package_shaped_unverified": 0, "unknown": 0}
    assert observed["stopped_subtrees"] == 1
    assert observed["observed_entries"] == 2
    serialized = json.dumps(report) + result.stdout + result.stderr
    assert secret.name not in serialized
    assert "NEVER-OUTPUT-PRIVATE-BODY" not in serialized
    assert secret.read_text(encoding="utf-8") == "NEVER-OUTPUT-PRIVATE-BODY"


@requires_ps
def test_package_layout_is_unverified_and_outside_boundary_is_unknown(layout):
    package = venv(layout) / "Lib/site-packages/private-package-canary/data"
    package.mkdir(parents=True)
    (package / "secret.bin").write_bytes(b"private")
    (venv(layout) / "data").mkdir()
    result, report = invoke(layout)
    assert result.returncode == 0, result.stderr
    observed = row(report, "asr-venv")
    assert observed["boundary_counts"] == {"source_shaped": 0, "package_shaped_unverified": 1, "unknown": 1}
    assert observed["stopped_subtrees"] == 2
    assert observed["retained"] is True
    assert "private-package-canary" not in json.dumps(report)
    assert "secret.bin" not in json.dumps(report)


@requires_ps
@pytest.mark.parametrize("filename", [".env", "app.sqlite", "app.sqlite-wal", "run.lock", "service.log", "audit.json"])
def test_protected_files_count_without_names_or_content(layout, filename):
    root = app(layout)
    root.mkdir(parents=True)
    path = root / filename
    path.write_bytes(b"PRIVATE-CONTENT-CANARY")
    result, report = invoke(layout)
    assert result.returncode == 0, result.stderr
    observed = row(report, "asr-app")
    assert observed["protected_files"] == 1
    assert observed["stopped_subtrees"] == 0
    assert observed["retained"] is True
    text = json.dumps(report) + result.stdout + result.stderr
    assert filename not in text
    assert "PRIVATE-CONTENT-CANARY" not in text
    assert path.read_bytes() == b"PRIVATE-CONTENT-CANARY"


@requires_ps
def test_current_identity_collision_is_never_enumerated(layout):
    (app(layout) / "src/transcription").mkdir(parents=True)
    result, report = invoke(layout, expected_asr=OLD)
    assert result.returncode == 0, result.stderr
    for kind in ("asr-app", "asr-venv"):
        observed = row(report, kind)
        assert observed["status"] == "incomplete"
        assert observed["reason_code"] == "current-identity"
        assert observed["observed_entries"] == 0


@requires_ps
@pytest.mark.parametrize("option,value,reason", [("-MaxEntries", 1, "entry-cap"), ("-MaxDepth", 1, "depth-cap")])
def test_caps_never_claim_complete_observation(layout, option, value, reason):
    (app(layout) / "outer/inner/deeper").mkdir(parents=True)
    result, report = invoke(layout, option, value)
    assert result.returncode == 0, result.stderr
    observed = row(report, "asr-app")
    assert observed["status"] == "incomplete"
    assert observed["reason_code"] == reason
    assert_retained(report)


@requires_ps
def test_report_never_overwrites_existing_file(layout):
    target = layout[2] / "result.json"
    target.write_text('{"sentinel": true}', encoding="utf-8")
    result, _ = invoke(layout)
    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == '{"sentinel": true}'


@requires_ps
def test_report_escape_is_rejected(layout):
    target = layout[2].parent / "outside.json"
    result, report = invoke(layout, report=target)
    assert result.returncode != 0
    assert report is None
    assert not target.exists()


@requires_ps
@pytest.mark.parametrize("link_name", ["linked", "data"])
def test_reparse_boundary_is_incomplete_and_target_untouched(layout, link_name):
    root = app(layout)
    root.mkdir(parents=True)
    outside = layout[2].parent / "outside-secret-canary"
    outside.mkdir()
    canary = outside / "private.txt"
    canary.write_bytes(b"PRIVATE")
    env = dict(os.environ, JUNCTION_PATH=str(root / link_name), JUNCTION_TARGET=str(outside))
    link = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command",
                           "$null = New-Item -ItemType Junction -Path $env:JUNCTION_PATH -Target $env:JUNCTION_TARGET -ErrorAction Stop"],
                          capture_output=True, text=True, env=env, timeout=20)
    assert link.returncode == 0, link.stderr
    try:
        result, report = invoke(layout)
        assert result.returncode == 0, result.stderr
        observed = row(report, "asr-app")
        assert observed["status"] == "incomplete"
        assert observed["reason_code"] == "reparse"
        assert "outside-secret-canary" not in json.dumps(report)
        assert canary.read_bytes() == b"PRIVATE"
    finally:
        os.rmdir(root / link_name)


@requires_ps
def test_global_deadline_prevents_any_filesystem_observation(layout):
    driver = layout[2] / "deadline-test.ps1"
    driver.write_text('''$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:OBSERVER_SCRIPT,[ref]$tokens,[ref]$errors)
$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$MaxTotalSeconds=1
$script:totalClock=[pscustomobject]@{Elapsed=[pscustomobject]@{TotalSeconds=2}}
Observe-Payload 'Z:\\never-access-this-path' 'asr-app' '34283259608' $false | ConvertTo-Json -Depth 6
''', encoding="utf-8")
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                            capture_output=True, text=True,
                            env=dict(os.environ, OBSERVER_SCRIPT=str(SCRIPT)), timeout=20)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == "incomplete"
    assert observed["reason_code"] == "time-cap"
    assert observed["observed_entries"] == 0
    assert observed["retained"] is True


@requires_ps
def test_locked_ordinary_file_is_not_opened_by_observer(layout):
    root = app(layout)
    root.mkdir(parents=True)
    canary = root / "unprotected-file.txt"
    canary.write_bytes(b"CANARY-NO-CONTENT-READ")
    driver = layout[2] / "locked-test.ps1"
    report_path = layout[2] / "result.json"
    driver.write_text('''$h=[IO.File]::Open($env:CANARY,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
 & $env:OBSERVER_SCRIPT -AsrProgramRoot $env:PROGRAM -RuntimeRoot $env:RUNTIME -ExpectedAsrCandidateId '35048453327' -ExpectedGpuReleaseId 'e351a5d5d78f-f9a0f015f3e8' -ReportPath $env:REPORT
} finally { $h.Dispose() }
''', encoding="utf-8")
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, RUNNER_TEMP=str(layout[2]), OBSERVER_SCRIPT=str(SCRIPT),
                                     CANARY=str(canary), PROGRAM=str(layout[0]), RUNTIME=str(layout[1]), REPORT=str(report_path)))
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    assert row(report, "asr-app")["status"] == "observed"
    assert canary.read_bytes() == b"CANARY-NO-CONTENT-READ"
    assert "CANARY-NO-CONTENT-READ" not in json.dumps(report) + result.stdout + result.stderr


@requires_ps
def test_report_reparse_ancestor_cannot_write_outside_runner_temp(layout):
    outside = layout[2].parent / "outside-reports"
    outside.mkdir()
    link = layout[2] / "linked"
    env = dict(os.environ, LINK_PATH=str(link), LINK_TARGET=str(outside))
    created = subprocess.run([PS, "-NoProfile", "-Command",
                              "$null=New-Item -ItemType Junction -Path $env:LINK_PATH -Target $env:LINK_TARGET -ErrorAction Stop"],
                             capture_output=True, text=True, env=env, timeout=20)
    assert created.returncode == 0, created.stderr
    try:
        result, report = invoke(layout, report=link / "escaped.json")
        assert result.returncode != 0
        assert report is None
        assert not (outside / "escaped.json").exists()
    finally:
        os.rmdir(link)


@requires_ps
def test_payload_ancestor_reparse_is_not_followed(layout):
    outside = layout[2].parent / "outside-private-ancestor"
    (outside / OLD / "app").mkdir(parents=True)
    canary = outside / OLD / "app/private-canary.txt"
    canary.write_bytes(b"PRIVATE-ANCESTOR-CONTENT")
    link = layout[0] / "releases"
    env = dict(os.environ, LINK_PATH=str(link), LINK_TARGET=str(outside))
    created = subprocess.run([PS, "-NoProfile", "-Command",
                              "$null=New-Item -ItemType Junction -Path $env:LINK_PATH -Target $env:LINK_TARGET -ErrorAction Stop"],
                             capture_output=True, text=True, env=env, timeout=20)
    assert created.returncode == 0, created.stderr
    try:
        result, report = invoke(layout)
        assert result.returncode == 0, result.stderr
        observed = row(report, "asr-app")
        assert observed["status"] == "incomplete"
        assert observed["reason_code"] == "reparse"
        assert observed["observed_entries"] == 0
        assert canary.read_bytes() == b"PRIVATE-ANCESTOR-CONTENT"
        assert "outside-private-ancestor" not in json.dumps(report)
    finally:
        os.rmdir(link)


def test_manual_workflow_does_not_change_cleanup_or_load_models():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert set(workflow.get("on", workflow.get(True))) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["diagnose"]
    assert job["timeout-minutes"] == 20
    assert workflow["concurrency"]["group"] == "production-gpu-exclusive"
    steps = job["steps"]
    uses = {step["uses"] for step in steps if "uses" in step}
    assert uses == {"actions/checkout@v4", "actions/upload-artifact@v4"}
    body = "\n".join(step.get("run", "") for step in steps)
    assert "diagnose-retained-production-layout.ps1" in body
    for forbidden in ("cleanup-production-current-only.ps1", "Read-AsrReleaseManifest", "Start-Process", "Register-ScheduledTask", "Remove-Item"):
        assert forbidden not in body
