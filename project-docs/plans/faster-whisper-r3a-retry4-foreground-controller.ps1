[CmdletBinding()]
param(
  [switch]$SelfTest,
  [ValidateSet('ProbeSuccess','ProbeTimeout','RunA0A1')]
  [string]$Mode,
  [string]$StagingRoot,
  [string]$RunRoot,
  [string]$HelperPath,
  [string]$ExpectedControllerSha256,
  [string]$ExpectedHelperSha256,
  [string]$RetryPlanPath,
  [string]$ExpectedRetryPlanSha256,
  [string]$OriginalPlanPath,
  [string]$StaticPrecheckPath,
  [string]$BgeHelperPath,
  [string]$ExpectedBgeHelperSha256,
  [string]$SamplePath,
  [string]$WindowStart,
  [string]$WindowEnd,
  [string]$ProxyUri = 'http://${PRIVATE_ZEROTIER_IPV4}:7897',
  [int]$ChildTimeoutSeconds = 1200,
  [int]$ReleaseWaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedHost = '${PRODUCTION_HOSTNAME}'
$AllowedStagingParent = '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-inputs'
$AllowedRunParent = '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs'

function Require-Value([string]$Name,[string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { throw "missing required parameter: $Name" }
}

function Get-Sha256([string]$Path) {
  (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-FileHash([string]$Path,[string]$Expected,[string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
  $actual = Get-Sha256 $Path
  if ($actual -ne $Expected.ToLowerInvariant()) {
    throw "$Label SHA-256 mismatch: expected=$Expected actual=$actual path=$Path"
  }
  $actual
}

function ConvertTo-PsLiteral([string]$Value) {
  if ($null -eq $Value) { return '$null' }
  if ($Value.IndexOf([char]0) -ge 0 -or $Value.IndexOf("`r") -ge 0 -or $Value.IndexOf("`n") -ge 0) {
    throw 'PowerShell literal value contains a prohibited control character'
  }
  "'" + $Value.Replace("'","''") + "'"
}

function Write-AtomicText([string]$Path,[string]$Text) {
  $directory = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
  }
  $temp = Join-Path $directory ('.{0}.tmp-{1}' -f (Split-Path -Leaf $Path),[Guid]::NewGuid().ToString('N'))
  try {
    [IO.File]::WriteAllText($temp,$Text,(New-Object Text.UTF8Encoding($true)))
    Move-Item -LiteralPath $temp -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $temp -PathType Leaf) { Remove-Item -LiteralPath $temp -Force }
  }
}

function Write-AtomicJson([string]$Path,$Value) {
  Write-AtomicText $Path (($Value | ConvertTo-Json -Depth 12) + "`r`n")
}

function Assert-DirectChild([string]$Path,[string]$Parent,[string]$Label) {
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
  if ([IO.Path]::GetDirectoryName($full) -ne $parentFull) {
    throw "$Label must be a direct child of $parentFull"
  }
  $full
}

function New-ReleaseWrappedScript([string]$Payload,[string]$ReleasePath,[int]$WaitSeconds) {
  $releaseLiteral = ConvertTo-PsLiteral $ReleasePath
  @(
    '$ErrorActionPreference = ''Stop'''
    ('$releasePath = {0}' -f $releaseLiteral)
    ('$deadline = [DateTimeOffset]::UtcNow.AddSeconds({0})' -f $WaitSeconds)
    'while (-not (Test-Path -LiteralPath $releasePath -PathType Leaf)) {'
    '  if ([DateTimeOffset]::UtcNow -ge $deadline) { Write-Error ''release gate timed out''; exit 125 }'
    '  Start-Sleep -Milliseconds 100'
    '}'
    $Payload
  ) -join "`r`n"
}

function Start-EncodedChild([string]$ScriptText) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($ScriptText))
  $si = New-Object Diagnostics.ProcessStartInfo
  $si.FileName = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
  $si.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
  $si.UseShellExecute = $false
  $si.CreateNoWindow = $true
  $si.RedirectStandardOutput = $true
  $si.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $si
  if (-not $process.Start()) { throw 'failed to start encoded child PowerShell' }
  [pscustomobject]@{
    process = $process
    stdout_task = $process.StandardOutput.ReadToEndAsync()
    stderr_task = $process.StandardError.ReadToEndAsync()
  }
}

function Stop-ExactProcessTree([Diagnostics.Process]$Process) {
  if ($null -eq $Process) { return [ordered]@{requested=$false;already_exited=$true;taskkill_exit_code=$null;output=@()} }
  try { if ($Process.HasExited) { return [ordered]@{requested=$false;already_exited=$true;taskkill_exit_code=$null;output=@()} } } catch {}
  $output = @(& "$env:SystemRoot\System32\taskkill.exe" /PID $Process.Id /T /F 2>&1 | ForEach-Object { [string]$_ })
  $exitCode = $LASTEXITCODE
  try { [void]$Process.WaitForExit(10000) } catch {}
  [ordered]@{requested=$true;already_exited=$false;taskkill_exit_code=$exitCode;output=$output}
}

function Wait-SupervisedChild($Child,[int]$TimeoutSeconds,[string]$StatusPath,$BaseStatus) {
  $process = $Child.process
  $started = [DateTimeOffset]::UtcNow
  $nextHeartbeat = $started
  $timedOut = $false
  while (-not $process.WaitForExit(500)) {
    $now = [DateTimeOffset]::UtcNow
    if (($now - $started).TotalSeconds -ge $TimeoutSeconds) { $timedOut = $true; break }
    if ($now -ge $nextHeartbeat) {
      $heartbeat = [ordered]@{}
      foreach ($key in $BaseStatus.Keys) { $heartbeat[$key] = $BaseStatus[$key] }
      $heartbeat.status = 'running'
      $heartbeat.heartbeat_at = [DateTimeOffset]::Now.ToString('o')
      $heartbeat.elapsed_seconds = [math]::Round(($now - $started).TotalSeconds,1)
      Write-AtomicJson $StatusPath $heartbeat
      $nextHeartbeat = $now.AddSeconds(5)
    }
  }
  $kill = $null
  if ($timedOut) { $kill = Stop-ExactProcessTree $process }
  try { if (-not $process.HasExited) { [void]$process.WaitForExit(10000) } } catch {}
  try { $stdout = [string]$Child.stdout_task.Result } catch { $stdout = '' }
  try { $stderr = [string]$Child.stderr_task.Result } catch { $stderr = $_.Exception.Message }
  $exitCode = if ($timedOut) { 124 } elseif ($process.HasExited) { [int]$process.ExitCode } else { 126 }
  [ordered]@{timed_out=$timedOut;exit_code=$exitCode;stdout=$stdout;stderr=$stderr;kill=$kill}
}

function Invoke-SelfTest {
  $failures = New-Object Collections.Generic.List[string]
  function Check([bool]$Condition,[string]$Message) { if (-not $Condition) { $failures.Add($Message) } }
  $scopeProbe = 'check-scope-probe'
  Check $false $scopeProbe
  if (-not $failures.Remove($scopeProbe)) { $failures.Add('self-test Check failure branch did not capture in local scope') }
  $temp = Join-Path ([IO.Path]::GetTempPath()) ('r3a-retry4-controller-selftest-{0}' -f [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $temp -Force | Out-Null
  try {
    $literal = '$env:COMPUTERNAME|$PID|a''b|space value'
    $quoted = ConvertTo-PsLiteral $literal
    Check ($quoted -eq '''$env:COMPUTERNAME|$PID|a''''b|space value''') 'literal quoting changed caller-side variables'

    $jsonPath = Join-Path $temp 'atomic.json'
    Write-AtomicJson $jsonPath ([ordered]@{schema='selftest';value=$literal})
    $roundTrip = Get-Content -LiteralPath $jsonPath -Raw | ConvertFrom-Json
    Check ($roundTrip.value -eq $literal) 'atomic JSON round-trip failed'

    $release1 = Join-Path $temp 'release-success'
    $payload1 = 'Write-Output ' + (ConvertTo-PsLiteral $literal) + '; exit 0'
    $child1 = Start-EncodedChild (New-ReleaseWrappedScript $payload1 $release1 10)
    Write-AtomicText $release1 "release`r`n"
    $status1 = Join-Path $temp 'status-success.json'
    $result1 = Wait-SupervisedChild $child1 10 $status1 ([ordered]@{schema='selftest';status='starting';child_pid=$child1.process.Id})
    Check (-not $result1.timed_out) 'success probe timed out'
    Check ($result1.exit_code -eq 0) 'success probe exit code was not zero'
    Check ($result1.stdout.Trim() -eq $literal) 'success probe expanded literal variables'

    $release2 = Join-Path $temp 'release-timeout'
    $child2 = Start-EncodedChild (New-ReleaseWrappedScript 'Start-Sleep -Seconds 30; exit 0' $release2 10)
    Write-AtomicText $release2 "release`r`n"
    $status2 = Join-Path $temp 'status-timeout.json'
    $result2 = Wait-SupervisedChild $child2 1 $status2 ([ordered]@{schema='selftest';status='starting';child_pid=$child2.process.Id})
    Check $result2.timed_out 'timeout probe did not time out'
    Check ($result2.exit_code -eq 124) 'timeout probe exit code was not 124'
    try { Check $child2.process.HasExited 'timeout probe child still running' } catch { $failures.Add('timeout probe child state unavailable') }
  } catch {
    $failures.Add($_.Exception.Message)
  } finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
  }
  $result = [ordered]@{tests=9;failures=@($failures);passed=($failures.Count -eq 0)}
  Write-Output ('R3A_RETRY4_CONTROLLER_SELFTEST=' + ($result | ConvertTo-Json -Compress -Depth 6))
  if ($failures.Count -gt 0) { exit 99 }
  exit 0
}

if ($SelfTest) { Invoke-SelfTest }

$controlRoot = $null
$statusPath = $null
$leasePath = $null
$releasePath = $null
$child = $null
$baseStatus = $null
try {
  foreach ($pair in @(@('Mode',$Mode),@('StagingRoot',$StagingRoot),@('ExpectedControllerSha256',$ExpectedControllerSha256))) {
    Require-Value $pair[0] $pair[1]
  }
  if ($env:COMPUTERNAME -ne $ExpectedHost) { throw "hostname mismatch: $($env:COMPUTERNAME)" }
  if ($ChildTimeoutSeconds -lt 1 -or $ChildTimeoutSeconds -gt 1800) { throw 'ChildTimeoutSeconds must be between 1 and 1800' }
  if ($ReleaseWaitSeconds -lt 5 -or $ReleaseWaitSeconds -gt 120) { throw 'ReleaseWaitSeconds must be between 5 and 120' }

  $stagingFull = Assert-DirectChild $StagingRoot $AllowedStagingParent 'StagingRoot'
  if (-not (Test-Path -LiteralPath $stagingFull -PathType Container)) { throw "StagingRoot is missing: $stagingFull" }
  $controllerHash = Assert-FileHash $PSCommandPath $ExpectedControllerSha256 'retry4 foreground controller'

  $runFull = ''
  if (-not [string]::IsNullOrWhiteSpace($RunRoot)) {
    $runFull = Assert-DirectChild $RunRoot $AllowedRunParent 'RunRoot'
    if ((Split-Path -Leaf $runFull) -notmatch '^phase0-fw-r3a-retry4-\d{8}-\d{6}$') { throw 'invalid retry RunRoot identity' }
  }

  $controlRoot = Join-Path $stagingFull 'controller'
  New-Item -ItemType Directory -Path $controlRoot -Force | Out-Null
  $modeSlug = $Mode.ToLowerInvariant()
  $statusPath = Join-Path $controlRoot ("$modeSlug-status.json")
  $leasePath = Join-Path $controlRoot ("$modeSlug-lease.json")
  $releasePath = Join-Path $controlRoot ("$modeSlug-release.txt")
  $stdoutPath = Join-Path $controlRoot ("$modeSlug-stdout.log")
  $stderrPath = Join-Path $controlRoot ("$modeSlug-stderr.log")
  foreach ($path in @($statusPath,$leasePath,$releasePath,$stdoutPath,$stderrPath)) {
    if (Test-Path -LiteralPath $path) { throw "controller artifact already exists; use a new staging identity: $path" }
  }

  $start = [DateTimeOffset]::Now
  $baseStatus = [ordered]@{
    schema_version='faster-whisper-r3a-retry4-foreground-controller/1'
    mode=$Mode
    status='starting'
    started_at=$start.ToString('o')
    hostname=$env:COMPUTERNAME
    user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    controller_pid=$PID
    controller_path=$PSCommandPath
    controller_sha256=$controllerHash
    staging_root=$stagingFull
    run_root=$runFull
    child_timeout_seconds=$ChildTimeoutSeconds
    release_wait_seconds=$ReleaseWaitSeconds
    foreground_ssh_required=$true
    detached_processes_forbidden=$true
  }
  $pendingLease = [ordered]@{}
  foreach ($key in $baseStatus.Keys) { $pendingLease[$key] = $baseStatus[$key] }
  $pendingLease.status = 'pending-child'
  $pendingLease.child_pid = $null
  Write-AtomicJson $leasePath $pendingLease

  if ($Mode -eq 'ProbeSuccess') {
    if ($ChildTimeoutSeconds -gt 30) { throw 'ProbeSuccess timeout must be at most 30 seconds' }
    $payload = "Write-Output 'R3A_RETRY4_PROBE_SUCCESS'; Start-Sleep -Milliseconds 250; exit 0"
  } elseif ($Mode -eq 'ProbeTimeout') {
    if ($ChildTimeoutSeconds -gt 10) { throw 'ProbeTimeout timeout must be at most 10 seconds' }
    $payload = "Write-Output 'R3A_RETRY4_PROBE_TIMEOUT_STARTED'; Start-Sleep -Seconds 120; exit 0"
  } else {
    foreach ($pair in @(
      @('RunRoot',$runFull),@('HelperPath',$HelperPath),@('ExpectedHelperSha256',$ExpectedHelperSha256),
      @('RetryPlanPath',$RetryPlanPath),@('ExpectedRetryPlanSha256',$ExpectedRetryPlanSha256),
      @('OriginalPlanPath',$OriginalPlanPath),@('StaticPrecheckPath',$StaticPrecheckPath),
      @('BgeHelperPath',$BgeHelperPath),@('ExpectedBgeHelperSha256',$ExpectedBgeHelperSha256),
      @('SamplePath',$SamplePath),@('WindowStart',$WindowStart),@('WindowEnd',$WindowEnd)
    )) { Require-Value $pair[0] $pair[1] }
    if (Test-Path -LiteralPath $runFull) { throw "RunRoot already exists; new identity required: $runFull" }
    Assert-FileHash $HelperPath $ExpectedHelperSha256 'A0/A1 helper' | Out-Null
    Assert-FileHash $RetryPlanPath $ExpectedRetryPlanSha256 'retry4 plan' | Out-Null
    Assert-FileHash $BgeHelperPath $ExpectedBgeHelperSha256 'BGE helper' | Out-Null
    $invoke = @(
      '& ' + (ConvertTo-PsLiteral $HelperPath)
      '-RunRoot ' + (ConvertTo-PsLiteral $runFull)
      '-RetryPlanSourcePath ' + (ConvertTo-PsLiteral $RetryPlanPath)
      '-ExpectedRetryPlanSha256 ' + (ConvertTo-PsLiteral $ExpectedRetryPlanSha256)
      '-OriginalPlanSourcePath ' + (ConvertTo-PsLiteral $OriginalPlanPath)
      '-StaticPrecheckSourcePath ' + (ConvertTo-PsLiteral $StaticPrecheckPath)
      '-BgeHelperSourcePath ' + (ConvertTo-PsLiteral $BgeHelperPath)
      '-ExpectedBgeHelperSha256 ' + (ConvertTo-PsLiteral $ExpectedBgeHelperSha256)
      '-SampleSourcePath ' + (ConvertTo-PsLiteral $SamplePath)
      '-ExpectedHelperSha256 ' + (ConvertTo-PsLiteral $ExpectedHelperSha256)
      '-WindowStart ' + (ConvertTo-PsLiteral $WindowStart)
      '-WindowEnd ' + (ConvertTo-PsLiteral $WindowEnd)
      '-ProxyUri ' + (ConvertTo-PsLiteral $ProxyUri)
    ) -join ' '
    $payload = $invoke + "`r`nexit `$LASTEXITCODE"
  }

  $wrapped = New-ReleaseWrappedScript $payload $releasePath $ReleaseWaitSeconds
  $child = Start-EncodedChild $wrapped
  $childStart = $child.process.StartTime.ToUniversalTime().ToString('o')
  $lease = [ordered]@{}
  foreach ($key in $baseStatus.Keys) { $lease[$key] = $baseStatus[$key] }
  $lease.status = 'child-waiting-for-release'
  $lease.child_pid = $child.process.Id
  $lease.child_started_at_utc = $childStart
  $lease.child_executable = $child.process.StartInfo.FileName
  Write-AtomicJson $leasePath $lease
  Write-AtomicText $releasePath ("release=" + [DateTimeOffset]::Now.ToString('o') + "`r`n")

  $running = [ordered]@{}
  foreach ($key in $lease.Keys) { $running[$key] = $lease[$key] }
  $running.status = 'released'
  $result = Wait-SupervisedChild $child $ChildTimeoutSeconds $statusPath $running
  Write-AtomicText $stdoutPath $result.stdout
  Write-AtomicText $stderrPath $result.stderr

  $terminal = [ordered]@{}
  foreach ($key in $running.Keys) { $terminal[$key] = $running[$key] }
  $terminal.finished_at = [DateTimeOffset]::Now.ToString('o')
  $terminal.child_exit_code = $result.exit_code
  $terminal.timed_out = $result.timed_out
  $terminal.kill = $result.kill
  $terminal.stdout_path = $stdoutPath
  $terminal.stderr_path = $stderrPath

  $controllerExit = [int]$result.exit_code
  if ($Mode -eq 'ProbeSuccess') {
    $ok = (-not $result.timed_out -and $result.exit_code -eq 0 -and $result.stdout -match 'R3A_RETRY4_PROBE_SUCCESS')
    $terminal.status = if ($ok) { 'probe-success' } else { 'probe-failed' }
    if (-not $ok) { $controllerExit = 72 }
  } elseif ($Mode -eq 'ProbeTimeout') {
    $ok = ($result.timed_out -and $result.exit_code -eq 124 -and $child.process.HasExited)
    $terminal.status = if ($ok) { 'probe-timeout-controlled' } else { 'probe-timeout-failed' }
    $controllerExit = if ($ok) { 0 } else { 73 }
  } else {
    $required = @(
      (Join-Path $runFull 'evidence\a1-baseline.json'),
      (Join-Path $runFull 'config\approval.json'),
      (Join-Path $runFull 'config\r3a-config.json'),
      (Join-Path $runFull 'state\run-identity.json'),
      (Join-Path $runFull 'reports\preflight.md')
    )
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    $terminal.required_artifacts = $required
    $terminal.missing_artifacts = $missing
    if (-not $result.timed_out -and $result.exit_code -eq 0 -and $missing.Count -eq 0) {
      $terminal.status = 'p1-ready'
      $controllerExit = 0
    } else {
      $terminal.status = 'stopped-before-p1-complete'
      if ($controllerExit -eq 0) { $controllerExit = 74 }
      if (Test-Path -LiteralPath $runFull -PathType Container) {
        $stopPath = Join-Path $runFull 'reports\stop-event.md'
        if (-not (Test-Path -LiteralPath $stopPath -PathType Leaf)) {
          $stop = @(
            '# faster-whisper R3-A retry4 automatic stop','',
            ('- Time: `{0}`' -f [DateTimeOffset]::Now.ToString('o')),
            ('- Run: `{0}`' -f $runFull),
            ('- Controller status: `{0}`' -f $terminal.status),
            ('- Child exit code: `{0}`' -f $result.exit_code),
            ('- Timed out: `{0}`' -f $result.timed_out),
            '- State: `STOPPED_BEFORE_P1_COMPLETE`',
            '- Failure artifact policy: retain complete run; no automatic deletion.'
          ) -join "`r`n"
          Write-AtomicText $stopPath ($stop + "`r`n")
        }
      }
    }
  }

  Write-AtomicJson $statusPath $terminal
  if (-not [string]::IsNullOrWhiteSpace($runFull) -and (Test-Path -LiteralPath $runFull -PathType Container)) {
    $runEvidence = Join-Path $runFull 'evidence\retry4-foreground-controller-status.json'
    Write-AtomicJson $runEvidence $terminal
  }
  Write-Output ('R3A_RETRY4_CONTROLLER_RESULT=' + ($terminal | ConvertTo-Json -Compress -Depth 12))
  exit $controllerExit
} catch {
  $message = $_.Exception.Message
  $kill = $null
  if ($null -ne $child) {
    try { $kill = Stop-ExactProcessTree $child.process } catch { $kill = [ordered]@{error=$_.Exception.Message} }
  }
  if ($null -ne $statusPath) {
    $failure = [ordered]@{
      schema_version='faster-whisper-r3a-retry4-foreground-controller/1'
      mode=$Mode
      status='controller-failed'
      finished_at=[DateTimeOffset]::Now.ToString('o')
      hostname=$env:COMPUTERNAME
      controller_pid=$PID
      staging_root=$StagingRoot
      run_root=$RunRoot
      error=$message
      kill=$kill
    }
    try { Write-AtomicJson $statusPath $failure } catch {}
  }
  Write-Error $message
  exit 75
}