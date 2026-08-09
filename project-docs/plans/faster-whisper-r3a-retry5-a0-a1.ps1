[CmdletBinding()]
param(
  [switch]$SelfTest,
  [string]$RunRoot,
  [string]$RetryPlanSourcePath,
  [string]$OriginalPlanSourcePath,
  [string]$StaticPrecheckSourcePath,
  [string]$BgeHelperSourcePath,
  [string]$SampleSourcePath,
  [string]$ProxyUri = '${PROXY_URI}'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedHost = '${PRODUCTION_HOSTNAME}'
$ExpectedHead = 'e2374e37e1357be3d8df93d6d3429bb0947fb9ba'
$ExpectedFingerprint = '${PRODUCTION_HOST_KEY_FINGERPRINT}'
$ExpectedSampleSha256 = 'af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9'
$ExpectedModelSha256 = 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da'
$SupersededModelSha256 = 'e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31'
$ExpectedModelSize = 1617884929L
$ModelId = 'dropbox-dash/faster-whisper-large-v3-turbo'
$ModelRevision = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
$AllowedRunParent = '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs'
$ProductionRepo = '${PRODUCTION_REPO_PATH}'
$PhaseRoot = '${QUALIFICATION_SANDBOX_ROOT}'
$AllowedDownloadHosts = @('pypi.org','files.pythonhosted.org','huggingface.co','us.aws.cdn.hf.co')
$BgeCandidates = @('http://127.0.0.1:8100','http://${GPU_SERVICE_IP}:8100')

function Get-Sha256([string]$Path) {
  (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}
function Write-JsonFile([string]$Path,$Value) {
  $Value | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $Path -Encoding UTF8
}
function Read-PlainUtf8Text([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
  [IO.File]::ReadAllText($Path,(New-Object Text.UTF8Encoding($false,$true)))
}
function Require-Value([string]$Name,[string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { throw "missing required parameter: $Name" }
}
function Assert-FilePresent([string]$Path,[string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
  [int64](Get-Item -LiteralPath $Path).Length
}
function Assert-CopiedFileLength([string]$Source,[string]$Destination,[string]$Label) {
  $sourceBytes = Assert-FilePresent $Source "$Label source"
  $destinationBytes = Assert-FilePresent $Destination "$Label destination"
  if ($sourceBytes -ne $destinationBytes) {
    throw "$Label byte-length mismatch: source=$sourceBytes destination=$destinationBytes source_path=$Source destination_path=$Destination"
  }
  [int64]$destinationBytes
}
function Assert-FileHash([string]$Path,[string]$Expected,[string]$Label) {
  [void](Assert-FilePresent $Path $Label)
  $actual = Get-Sha256 $Path
  if ($actual -ne $Expected.ToLowerInvariant()) { throw "$Label SHA-256 mismatch: expected=$Expected actual=$actual path=$Path" }
  $actual
}
function Invoke-ProcessCapture([string]$FilePath,[string[]]$Arguments,[int]$TimeoutSeconds) {
  $si = New-Object Diagnostics.ProcessStartInfo
  $si.FileName = $FilePath
  $si.Arguments = $Arguments -join ' '
  $si.UseShellExecute = $false
  $si.CreateNoWindow = $true
  $si.RedirectStandardOutput = $true
  $si.RedirectStandardError = $true
  $p = New-Object Diagnostics.Process
  $p.StartInfo = $si
  if (-not $p.Start()) { throw "failed to start process: $FilePath" }
  $outTask = $p.StandardOutput.ReadToEndAsync()
  $errTask = $p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
    try { $p.Kill() } catch {}
    try { [void]$p.WaitForExit(5000) } catch {}
    return [ordered]@{exit_code=124;stdout=[string]$outTask.Result;stderr=(([string]$errTask.Result)+"`r`ntimeout").Trim();timed_out=$true;process_id=$p.Id;process_exited=$p.HasExited}
  }
  $p.WaitForExit()
  [ordered]@{exit_code=[int]$p.ExitCode;stdout=[string]$outTask.Result;stderr=[string]$errTask.Result;timed_out=$false;process_id=$p.Id;process_exited=$p.HasExited}
}
function Invoke-WatchedProcess([string]$FilePath,[string]$ArgumentsText,[int]$TimeoutSeconds,[string]$TimeoutBlocker) {
  if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 120) { throw 'watchdog timeout must be between 1 and 120 seconds' }
  $si = New-Object Diagnostics.ProcessStartInfo
  $si.FileName = $FilePath
  $si.Arguments = $ArgumentsText
  $si.UseShellExecute = $false
  $si.CreateNoWindow = $true
  $si.RedirectStandardOutput = $true
  $si.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $si
  $startedAt = [DateTimeOffset]::UtcNow
  if (-not $process.Start()) { throw "failed to start watched process: $FilePath" }
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
  $killRequested = $false
  if ($timedOut) {
    $killRequested = $true
    try { $process.Kill() } catch {}
    try { [void]$process.WaitForExit(5000) } catch {}
  } else {
    $process.WaitForExit()
  }
  try { $stdout = [string]$stdoutTask.Result } catch { $stdout = '' }
  try { $stderr = [string]$stderrTask.Result } catch { $stderr = $_.Exception.Message }
  [ordered]@{
    file_path=$FilePath;arguments=$ArgumentsText;process_id=$process.Id;started_at_utc=($startedAt.ToString('o'))
    elapsed_ms=[math]::Round(([DateTimeOffset]::UtcNow-$startedAt).TotalMilliseconds,0)
    timed_out=$timedOut;timeout_seconds=$TimeoutSeconds;timeout_blocker=if($timedOut){$TimeoutBlocker}else{$null}
    kill_requested=$killRequested;process_exited=$process.HasExited
    exit_code=if($timedOut){124}elseif($process.HasExited){[int]$process.ExitCode}else{126}
    stdout=$stdout;stderr=$stderr
  }
}
function Get-WindowsPowerShellPath {
  $candidate = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Windows PowerShell is missing: $candidate" }
  $candidate
}
function ConvertTo-EncodedPowerShellArguments([string]$ScriptText) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($ScriptText))
  '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
}
function Invoke-NvidiaSmiQuery([string]$ArgumentsText,[int]$TimeoutSeconds=15,[string]$ExecutablePath='nvidia-smi.exe') {
  $capture = Invoke-WatchedProcess $ExecutablePath $ArgumentsText $TimeoutSeconds 'NVIDIA_SMI_TIMEOUT'
  if ($capture.timed_out) { throw "NVIDIA_SMI_TIMEOUT: pid=$($capture.process_id) timeout_seconds=$TimeoutSeconds" }
  [ordered]@{success=($capture.exit_code-eq 0);exit_code=$capture.exit_code;lines=@([string]$capture.stdout -split "`r?`n"|Where-Object{-not [string]::IsNullOrWhiteSpace($_)});stderr=([string]$capture.stderr).Trim();watchdog=$capture}
}
function Invoke-ExactProcessIdentityQuery([int]$ProcessId,[int]$TimeoutSeconds=5,[string]$SimulatedScript='') {
  if ($ProcessId -le 0) { throw 'exact process identity query requires a positive PID' }
  if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 5) { throw 'WMI query watchdog must be between 1 and 5 seconds' }
  $script = $SimulatedScript
  if ([string]::IsNullOrWhiteSpace($script)) {
    $script = @"
`$ErrorActionPreference = 'Stop'
`$row = Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId=$ProcessId' -OperationTimeoutSec $TimeoutSeconds
if (`$null -eq `$row) { [ordered]@{ exists=`$false; process_id=$ProcessId } | ConvertTo-Json -Compress; exit 0 }
[ordered]@{
  exists=`$true
  process_id=[int]`$row.ProcessId
  parent_process_id=[int]`$row.ParentProcessId
  creation_date=[string]`$row.CreationDate
  executable_path=[string]`$row.ExecutablePath
  command_line=[string]`$row.CommandLine
} | ConvertTo-Json -Compress
"@
  }
  $capture = Invoke-WatchedProcess (Get-WindowsPowerShellPath) (ConvertTo-EncodedPowerShellArguments $script) $TimeoutSeconds 'WMI_EXACT_QUERY_TIMEOUT'
  if ($capture.timed_out) { return [ordered]@{process_id=$ProcessId;status='timeout';blocker='WMI_EXACT_QUERY_TIMEOUT';identity=$null;watchdog=$capture} }
  if ($capture.exit_code -ne 0) { return [ordered]@{process_id=$ProcessId;status='failed';blocker='WMI_EXACT_QUERY_FAILED';identity=$null;watchdog=$capture} }
  try {
    $identity = ([string]$capture.stdout).Trim() | ConvertFrom-Json
    [ordered]@{process_id=$ProcessId;status='ok';blocker=$null;identity=$identity;watchdog=$capture}
  } catch {
    [ordered]@{process_id=$ProcessId;status='invalid-json';blocker='WMI_EXACT_QUERY_INVALID_JSON';identity=$null;watchdog=$capture}
  }
}
function Get-WindowsSystemInfo {
  $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
  $reg = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
  $version = [Environment]::OSVersion.Version
  $architecture = try { [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() } catch { [string]$env:PROCESSOR_ARCHITECTURE }
  [ordered]@{caption=[string]$reg.ProductName;display_version=[string]$reg.DisplayVersion;version=($version.ToString());build=if($reg.CurrentBuildNumber){[string]$reg.CurrentBuildNumber}else{[string]$version.Build};ubr=if($null-ne $reg.UBR){[int]$reg.UBR}else{$null};architecture=$architecture;source='Environment.OSVersion + HKLM CurrentVersion'}
}
function Get-DriveInfoSnapshot([string]$DriveName) {
  $normalized = $DriveName.TrimEnd(':') + ':\'
  $drive = New-Object IO.DriveInfo($normalized)
  if (-not $drive.IsReady) { throw "drive is not ready: $normalized" }
  [ordered]@{Name=$drive.Name;DriveType=[string]$drive.DriveType;DriveFormat=$drive.DriveFormat;Size=[int64]$drive.TotalSize;FreeSpace=[int64]$drive.AvailableFreeSpace}
}
function Get-SafeProcessSnapshot {
  @(Get-Process -ErrorAction Stop | ForEach-Object {$path='';$started=$null;try{$path=[string]$_.Path}catch{};try{$started=$_.StartTime.ToUniversalTime().ToString('o')}catch{};[pscustomobject]@{ProcessId=[int]$_.Id;ParentProcessId=$null;Name=[string]$_.ProcessName;ExecutablePath=$path;StartTimeUtc=$started;CommandLine=''}})
}
function Get-ActiveRunCandidatePids([string[]]$Paths,[int]$Maximum=8) {
  $ids = New-Object Collections.Generic.List[int]
  foreach($path in @($Paths)|Sort-Object) {if($ids.Count-ge $Maximum){break};try{$raw=[IO.File]::ReadAllText($path)}catch{continue};foreach($match in [regex]::Matches($raw,'(?i)"(?:pid|process_id|child_pid|controller_pid|worker_pid)"\s*:\s*(\d+)')){$value=[int]$match.Groups[1].Value;if($value-gt 0-and-not $ids.Contains($value)){$ids.Add($value)};if($ids.Count-ge $Maximum){break}}}
  @($ids)
}
function Get-TcpListenersByNetstat([int]$Port,[int]$TimeoutSeconds=5) {
  $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
  $capture = Invoke-WatchedProcess $netstat '-ano -p tcp' $TimeoutSeconds 'NETSTAT_TIMEOUT'
  if($capture.timed_out){throw 'NETSTAT_TIMEOUT'}
  if($capture.exit_code-ne 0){throw "NETSTAT_FAILED: exit=$($capture.exit_code) stderr=$($capture.stderr)"}
  $rows=@();foreach($line in [string]$capture.stdout -split "`r?`n"){if($line -match '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$' -and [int]$Matches[2]-eq $Port){$rows += [pscustomobject]@{LocalAddress=$Matches[1];LocalPort=[int]$Matches[2];OwningProcess=[int]$Matches[3]}}};@($rows)
}
function Test-TcpEndpoint([string]$HostName,[int]$Port,[int]$TimeoutMilliseconds=5000) {
  $client = New-Object Net.Sockets.TcpClient
  try {$async=$client.BeginConnect($HostName,$Port,$null,$null);if(-not $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds,$false)){return [ordered]@{TcpTestSucceeded=$false;error='TCP_CONNECT_TIMEOUT';timeout_ms=$TimeoutMilliseconds}};$client.EndConnect($async);[ordered]@{TcpTestSucceeded=$true;error='';timeout_ms=$TimeoutMilliseconds}} catch {[ordered]@{TcpTestSucceeded=$false;error=$_.Exception.Message;timeout_ms=$TimeoutMilliseconds}} finally {try{$client.Close()}catch{}}
}function Read-HeaderEvidence([string]$Path) {
  $status=@();$locations=@();$etags=@();$lengths=@()
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $Path) {
      if ($line -match '^HTTP/\S+\s+\d{3}') { $status += $line.Trim() }
      if ($line -match '^(?i)Location:\s*(.+)$') { $locations += $Matches[1].Trim() }
      if ($line -match '^(?i)X-Linked-ETag:\s*"?([^"\s]+)"?\s*$') { $etags += $Matches[1].ToLowerInvariant() }
      if ($line -match '^(?i)Content-Length:\s*(\d+)\s*$') { $lengths += [int64]$Matches[1] }
    }
  }
  [ordered]@{status_lines=$status;locations=$locations;x_linked_etags=$etags;content_lengths=$lengths}
}
function Get-ObservedHosts([string]$Requested,[string]$Effective,[object[]]$Locations) {
  $hosts = New-Object Collections.Generic.List[string]
  foreach ($value in @($Requested,$Effective)+@($Locations)) {
    if ([string]::IsNullOrWhiteSpace([string]$value)) { continue }
    try { $hostName=([Uri][string]$value).Host.ToLowerInvariant(); if ($hostName -and -not $hosts.Contains($hostName)) { $hosts.Add($hostName) } } catch {}
  }
  @($hosts)
}
function Normalize-CurlResult([string]$Name,[string]$Url,[int]$Attempt,$Capture,$Headers,[string]$HeaderPath) {
  $parts=@([string]$Capture.stdout -split '\|',5); $code=0
  if ($parts.Count -ge 1) { [void][int]::TryParse($parts[0].Trim(),[ref]$code) }
  $effective=if($parts.Count-ge 2){$parts[1].Trim()}else{''}
  $redirect=if($parts.Count-ge 3){$parts[2].Trim()}else{''}
  $remote=if($parts.Count-ge 4){$parts[3].Trim()}else{''}
  $ssl=if($parts.Count-ge 5){$parts[4].Trim()}else{''}
  $locations=if($null-eq $Headers){@()}else{@($Headers.locations)}
  [ordered]@{
    name=$Name;url=$Url;attempt=$Attempt;exit_code=[int]$Capture.exit_code;timed_out=[bool]$Capture.timed_out
    http_code=$code;effective_url=$effective;redirect_url=$redirect;remote_ip=$remote;ssl_verify_result=$ssl
    error=([string]$Capture.stderr).Trim();status_lines=if($null-eq $Headers){@()}else{@($Headers.status_lines)}
    locations=$locations;x_linked_etags=if($null-eq $Headers){@()}else{@($Headers.x_linked_etags)}
    content_lengths=if($null-eq $Headers){@()}else{@($Headers.content_lengths)}
    observed_hosts=Get-ObservedHosts $Url $effective ($locations+@($redirect));header_path=$HeaderPath
    success=([int]$Capture.exit_code -eq 0 -and $code -ge 200 -and $code -lt 400)
  }
}
function Invoke-ProxyRequest([string]$Name,[string]$Url,[string]$Proxy,[string]$LogRoot,[bool]$HeadOnly) {
  $records=@()
  for($attempt=1;$attempt-le 3;$attempt++) {
    $header=Join-Path $LogRoot ("proxy-{0}-a{1}.headers.txt" -f $Name,$attempt)
    $body=Join-Path $LogRoot ("proxy-{0}-a{1}.body.txt" -f $Name,$attempt)
    foreach($path in @($header,$body)){if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path -Force}}
    $args=@('--location','--max-redirs','5','--proto','=https','--proto-redir','=https','--proxy',$Proxy,'--connect-timeout','10','--max-time','30','--silent','--show-error','--dump-header',$header)
    if($HeadOnly){$args+=@('--head','--output','NUL')}else{$args+=@('--max-filesize','1048576','--output',$body)}
    $args+=@('--write-out','%{http_code}|%{url_effective}|%{redirect_url}|%{remote_ip}|%{ssl_verify_result}',$Url)
    $capture=Invoke-ProcessCapture 'curl.exe' $args 40
    $normalized=Normalize-CurlResult $Name $Url $attempt $capture (Read-HeaderEvidence $header) $header
    $record=[ordered]@{attempt=$normalized;body_path=$body;body_bytes=if(Test-Path -LiteralPath $body){(Get-Item -LiteralPath $body).Length}else{0L};body=(Read-PlainUtf8Text $body)}
    $records+=$record
    if($normalized.success){break}
    if($attempt-lt 3){Start-Sleep -Seconds ([math]::Pow(2,$attempt))}
  }
  $final=$records[$records.Count-1]
  [ordered]@{name=$Name;url=$Url;head_only=$HeadOnly;attempts=$records;final=$final;success=[bool]$final.attempt.success}
}
function Format-ProxyLine($Result) {
  $f=$Result.final.attempt
  '- {0}: success={1}, exit={2}, HTTP={3}, effective={4}, redirect={5}, error={6}' -f $Result.name,$Result.success,$f.exit_code,$f.http_code,$f.effective_url,$f.redirect_url,$f.error
}
function Convert-ProxyEvidenceDto($Result) {
  $attempts=@($Result.attempts|ForEach-Object{
    $a=$_.attempt
    [ordered]@{
      attempt=[int]$a.attempt;exit_code=[int]$a.exit_code;timed_out=[bool]$a.timed_out;http_code=[int]$a.http_code
      effective_url=[string]$a.effective_url;redirect_url=[string]$a.redirect_url;remote_ip=[string]$a.remote_ip;ssl_verify_result=[string]$a.ssl_verify_result
      error=[string]$a.error;status_lines=@($a.status_lines|ForEach-Object{[string]$_});locations=@($a.locations|ForEach-Object{[string]$_})
      x_linked_etags=@($a.x_linked_etags|ForEach-Object{[string]$_});content_lengths=@($a.content_lengths|ForEach-Object{[int64]$_})
      observed_hosts=@($a.observed_hosts|ForEach-Object{[string]$_});body_path=[string]$_.body_path;body_bytes=[int64]$_.body_bytes
      body_sha256=if(Test-Path -LiteralPath $_.body_path -PathType Leaf){Get-Sha256 $_.body_path}else{''}
      success=[bool]$a.success
    }
  })
  [ordered]@{name=[string]$Result.name;url=[string]$Result.url;head_only=[bool]$Result.head_only;success=[bool]$Result.success;final_attempt_number=[int]$Result.final.attempt.attempt;attempts=$attempts}
}
function Get-AsrProcessGate([object[]]$ActiveRunFiles,[object[]]$Processes,[object[]]$ExactIdentityResults,[string]$CurrentRunRoot,[int]$CurrentProcessId) {
  $named=@();$bound=@();$queryBlockers=@()
  foreach($process in @($Processes)) {$id=if($null-ne $process.ProcessId){[int]$process.ProcessId}else{0};if($id-eq $CurrentProcessId){continue};$name=[string]$process.Name;$exe=[string]$process.ExecutablePath;if(($name+' '+$exe)-match '(?i)(faster[-_]?whisper|ctranslate2)'){$named+=$process}}
  foreach($result in @($ExactIdentityResults)) {if(-not [string]::IsNullOrWhiteSpace([string]$result.blocker)){$queryBlockers+=$result;continue};if($null-eq $result.identity-or-not [bool]$result.identity.exists){continue};$cmd=[string]$result.identity.command_line;$mentions=(-not [string]::IsNullOrWhiteSpace($CurrentRunRoot)-and $cmd.IndexOf($CurrentRunRoot,[StringComparison]::OrdinalIgnoreCase)-ge 0);$asrLike=($cmd-match '(?i)(faster[-_]?whisper|ctranslate2|WhisperModel|transcrib(e|ing))');if($mentions-or $asrLike){$bound+=$result.identity}}
  [ordered]@{blocked=(@($ActiveRunFiles).Count-gt 0-or $named.Count-gt 0-or $bound.Count-gt 0-or $queryBlockers.Count-gt 0);active_run_files=@($ActiveRunFiles);named_processes=$named;run_bound_processes=$bound;exact_identity_queries=@($ExactIdentityResults);query_blockers=$queryBlockers}
}function Test-AllowedHosts([object[]]$Results,[string[]]$Allowed) {
  $observed=New-Object Collections.Generic.List[string]
  foreach($result in @($Results)){
    foreach($hostName in @($result.final.attempt.observed_hosts)){
      $lower=([string]$hostName).ToLowerInvariant();if($lower-and-not $observed.Contains($lower)){$observed.Add($lower)}
    }
  }
  $unexpected=@($observed|Where-Object{$Allowed-notcontains $_})
  [ordered]@{ok=($unexpected.Count-eq 0);observed=@($observed);unexpected=$unexpected}
}
function Test-ModelMetadata([string]$TreeSha,[int64]$TreeSize,[string]$RawSha,[int64]$RawSize,[string[]]$HeadEtags,[int64[]]$HeadLengths) {
  $etags=@($HeadEtags|ForEach-Object{([string]$_).Trim('"').ToLowerInvariant()})
  $shaOk=($TreeSha.ToLowerInvariant()-eq $ExpectedModelSha256-and $RawSha.ToLowerInvariant()-eq $ExpectedModelSha256-and $etags-contains $ExpectedModelSha256)
  $sizeOk=($TreeSize-eq $ExpectedModelSize-and $RawSize-eq $ExpectedModelSize-and @($HeadLengths)-contains $ExpectedModelSize)
  [ordered]@{ok=($shaOk-and $sizeOk);sha_ok=$shaOk;size_ok=$sizeOk}
}
function Invoke-SelfTest {
  $failures=New-Object Collections.Generic.List[string];$counter=@(0)
  function Check([bool]$Condition,[string]$Message){$counter[0]++;if(-not $Condition){$failures.Add($Message)}}
  $temp=Join-Path ([IO.Path]::GetTempPath()) ('faster-whisper-r3a-retry5-selftest-{0}' -f [Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory -Path $temp -Force|Out-Null
  try {
    $empty=[ordered]@{status_lines=@();locations=@();x_linked_etags=@();content_lengths=@()};$okCap=[ordered]@{exit_code=0;stdout='200|https://pypi.org/simple/||1.1.1.1|0';stderr='';timed_out=$false};$ok=Normalize-CurlResult 'ok' 'https://pypi.org/simple/' 1 $okCap $empty 'mock';Check ($ok.success-and $ok.http_code-eq 200) 'proxy success normalization failed'
    $badCap=[ordered]@{exit_code=35;stdout='';stderr='schannel failed';timed_out=$false};$bad=Normalize-CurlResult 'tls' 'https://huggingface.co/' 1 $badCap $null 'mock';$badLine=Format-ProxyLine ([ordered]@{name='tls';success=$false;final=[ordered]@{attempt=$bad}});Check ((-not $bad.success)-and $bad.http_code-eq 0-and $badLine-match 'exit=35') 'curl exit 35/null handling failed'
    Check ((Test-ModelMetadata $ExpectedModelSha256 $ExpectedModelSize $ExpectedModelSha256 $ExpectedModelSize @($ExpectedModelSha256) @(1061L,$ExpectedModelSize)).ok) 'correct model metadata rejected';Check (-not (Test-ModelMetadata $SupersededModelSha256 $ExpectedModelSize $SupersededModelSha256 $ExpectedModelSize @($SupersededModelSha256) @($ExpectedModelSize)).ok) 'superseded model SHA accepted'
    $ps=Get-WindowsPowerShellPath;$success=Invoke-WatchedProcess $ps (ConvertTo-EncodedPowerShellArguments "Write-Output 'WATCHDOG_OK'; exit 0") 5 'SELFTEST_TIMEOUT';Check (-not $success.timed_out-and $success.exit_code-eq 0-and $success.stdout.Trim()-eq 'WATCHDOG_OK') '.NET watched process success path failed'
    $timeoutScript="Start-Sleep -Seconds 30; exit 0";$timeout=Invoke-WatchedProcess $ps (ConvertTo-EncodedPowerShellArguments $timeoutScript) 1 'SELFTEST_TIMEOUT';Check ($timeout.timed_out-and $timeout.exit_code-eq 124-and $timeout.timeout_blocker-eq 'SELFTEST_TIMEOUT') '.NET watched process timeout path failed';Check $timeout.process_exited '.NET watchdog did not terminate exact child';Check ($null-eq (Get-Process -Id $timeout.process_id -ErrorAction SilentlyContinue)) '.NET watchdog left an orphan child'
    $gpu=Invoke-NvidiaSmiQuery (ConvertTo-EncodedPowerShellArguments "Write-Output 'Mock GPU, 999.1, 16000, 0, 16000, 0'; exit 0") 5 $ps;Check ($gpu.success-and $gpu.lines.Count-eq 1-and $gpu.lines[0]-match '^Mock GPU') 'simulated nvidia-smi success failed';$gpuTimeoutCaught=$false;try{[void](Invoke-NvidiaSmiQuery (ConvertTo-EncodedPowerShellArguments $timeoutScript) 1 $ps)}catch{$gpuTimeoutCaught=$_.Exception.Message-match 'NVIDIA_SMI_TIMEOUT'};Check $gpuTimeoutCaught 'simulated nvidia-smi timeout did not return NVIDIA_SMI_TIMEOUT'
    $wmiJson='{"exists":true,"process_id":4242,"parent_process_id":1,"creation_date":"mock","executable_path":"C:\\mock.exe","command_line":"mock.exe --model faster-whisper"}';$wmiOk=Invoke-ExactProcessIdentityQuery 4242 5 ("Write-Output '"+$wmiJson+"'; exit 0");Check ($wmiOk.status-eq 'ok'-and [int]$wmiOk.identity.process_id-eq 4242) 'simulated exact WMI success failed';$wmiTimeout=Invoke-ExactProcessIdentityQuery 4243 1 $timeoutScript;Check ($wmiTimeout.status-eq 'timeout'-and $wmiTimeout.blocker-eq 'WMI_EXACT_QUERY_TIMEOUT') 'simulated exact WMI timeout did not return blocker';Check $wmiTimeout.watchdog.process_exited 'simulated WMI watchdog did not terminate exact child'
    $gui=[pscustomobject]@{ProcessId=100;Name='explorer';ExecutablePath='C:\Windows\explorer.exe';CommandLine=''};$gateGui=Get-AsrProcessGate @() @($gui) @() '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-test' 999;Check (-not $gateGui.blocked) 'ordinary GUI process incorrectly blocked A1';$gateActive=Get-AsrProcessGate @('${QUALIFICATION_SANDBOX_ROOT}\active-run.json') @($gui) @() '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-test' 999;Check $gateActive.blocked 'active-run did not block A1'
    $identity=[pscustomobject]@{exists=$true;process_id=101;command_line='python.exe ${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-test\smoke.py --model faster-whisper'};$exact=[ordered]@{process_id=101;status='ok';blocker=$null;identity=$identity;watchdog=$null};Check ((Get-AsrProcessGate @() @($gui) @($exact) '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-test' 999).blocked) 'exact run-bound identity did not block A1';$blockedQuery=[ordered]@{process_id=102;status='timeout';blocker='WMI_EXACT_QUERY_TIMEOUT';identity=$null;watchdog=$null};Check ((Get-AsrProcessGate @() @($gui) @($blockedQuery) '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry5-test' 999).blocked) 'WMI timeout blocker did not block A1'
    $selfTestPath=Join-Path $temp 'proxy.json';[IO.File]::WriteAllText($selfTestPath,'{"path":"model.bin","lfs":{"oid":"e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da","size":1617884929}}',[Text.Encoding]::UTF8);$plain=Read-PlainUtf8Text $selfTestPath;Check (@($plain.PSObject.Properties|Where-Object{$_.Name -eq 'PSPath'}).Count -eq 0) 'plain UTF-8 reader retained PSPath ETS property';$mockRecord=[ordered]@{attempt=$ok;body_path=$selfTestPath;body_bytes=(Get-Item -LiteralPath $selfTestPath).Length};$mockResult=[ordered]@{name='selftest';url='https://huggingface.co/';head_only=$false;attempts=@($mockRecord);final=$mockRecord;success=$true};$dto=Convert-ProxyEvidenceDto $mockResult;$dtoJson=$dto|ConvertTo-Json -Depth 16 -Compress;Check ($dtoJson.Length -lt 10000-and $dtoJson-notmatch 'PSProvider|PSPath|PSDrive') 'proxy evidence DTO was not bounded/plain'
  } catch {$failures.Add($_.Exception.Message)} finally {Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue}
  $result=[ordered]@{tests=$counter[0];failures=@($failures);passed=($failures.Count-eq 0);real_wmi_invoked=$false;real_nvidia_smi_invoked=$false};Write-Output ('R3A_RETRY5_SELFTEST='+($result|ConvertTo-Json -Compress -Depth 8));if($failures.Count-gt 0){exit 99};exit 0
}
if($SelfTest){Invoke-SelfTest}

$createdRun=$false
try {
  foreach($pair in @(
    @('RunRoot',$RunRoot),@('RetryPlanSourcePath',$RetryPlanSourcePath),
    @('OriginalPlanSourcePath',$OriginalPlanSourcePath),@('StaticPrecheckSourcePath',$StaticPrecheckSourcePath),
    @('BgeHelperSourcePath',$BgeHelperSourcePath),@('SampleSourcePath',$SampleSourcePath)
  )){Require-Value $pair[0] $pair[1]}
  $now=[DateTimeOffset]::Now;$localTimeZone=[TimeZoneInfo]::Local
  if($localTimeZone.Id-ne 'China Standard Time'){throw "production time zone mismatch: $($localTimeZone.Id)"}
  if($now.Offset.TotalHours-ne 8){throw "production time zone offset must be +08:00: $($now.Offset)"}
  $dateConsistency=('No preset maintenance window; execution timestamps use production host time. local={0}; UTC={1}; timezone={2}; offset={3}.' -f $now.ToString('o'),$now.UtcDateTime.ToString('o'),$localTimeZone.Id,$now.Offset.ToString())
  if($ProxyUri-ne '${PROXY_URI}'){throw "unapproved proxy URI: $ProxyUri"}
  $runFull=[IO.Path]::GetFullPath($RunRoot).TrimEnd('\');$parent=[IO.Path]::GetFullPath($AllowedRunParent).TrimEnd('\')
  if([IO.Path]::GetDirectoryName($runFull)-ne $parent){throw "RunRoot must be a direct child of $parent"}
  $runId=Split-Path -Leaf $runFull
  if($runId-notmatch '^phase0-fw-r3a-retry5-\d{8}-\d{6}$'){throw "invalid retry run id: $runId"}
  if(Test-Path -LiteralPath $runFull){throw "RunRoot already exists; use a new identity: $runFull"}
  if($env:COMPUTERNAME-ne $ExpectedHost){throw "hostname mismatch: $($env:COMPUTERNAME)"}
  $helperSourceBytes=Assert-FilePresent $PSCommandPath 'retry5 A0/A1 helper'
  $retryPlanSourceBytes=Assert-FilePresent $RetryPlanSourcePath 'retry execution plan'
  $originalPlanSourceBytes=Assert-FilePresent $OriginalPlanSourcePath 'original approved execution plan'
  $staticPrecheckSourceBytes=Assert-FilePresent $StaticPrecheckSourcePath 'static precheck'
  $bgeHelperSourceBytes=Assert-FilePresent $BgeHelperSourcePath 'retry BGE helper'
  Assert-FileHash $SampleSourcePath $ExpectedSampleSha256 'approved synthetic sample'|Out-Null
  foreach($name in @('config','helpers','venv','wheels','hf-cache','model','evidence','logs','reports','state','testdata')){New-Item -ItemType Directory -Path (Join-Path $runFull $name)-Force|Out-Null}
  $createdRun=$true
  $retrySnap=Join-Path $runFull 'evidence\faster-whisper-r3a-retry5-execution-plan.md'
  $originalSnap=Join-Path $runFull 'evidence\faster-whisper-r3a-execution-plan.md'
  $staticSnap=Join-Path $runFull 'evidence\faster-whisper-phase0-precheck.md'
  $helperSnap=Join-Path $runFull 'helpers\faster-whisper-r3a-retry5-a0-a1.ps1'
  $bgeSnap=Join-Path $runFull 'helpers\run-bge-auth-probe.ps1'
  $samplePath=Join-Path $runFull 'testdata\r3a-synthetic-zh.wav'
  Copy-Item -LiteralPath $RetryPlanSourcePath -Destination $retrySnap
  Copy-Item -LiteralPath $OriginalPlanSourcePath -Destination $originalSnap
  Copy-Item -LiteralPath $StaticPrecheckSourcePath -Destination $staticSnap
  Copy-Item -LiteralPath $PSCommandPath -Destination $helperSnap
  Copy-Item -LiteralPath $BgeHelperSourcePath -Destination $bgeSnap
  Copy-Item -LiteralPath $SampleSourcePath -Destination $samplePath
  $retryPlanBytes=Assert-CopiedFileLength $RetryPlanSourcePath $retrySnap 'retry plan snapshot'
  $originalPlanBytes=Assert-CopiedFileLength $OriginalPlanSourcePath $originalSnap 'original plan snapshot'
  $staticPrecheckBytes=Assert-CopiedFileLength $StaticPrecheckSourcePath $staticSnap 'static precheck snapshot'
  $helperBytes=Assert-CopiedFileLength $PSCommandPath $helperSnap 'retry helper snapshot'
  $bgeHelperBytes=Assert-CopiedFileLength $BgeHelperSourcePath $bgeSnap 'BGE helper snapshot'
  [void](Assert-CopiedFileLength $SampleSourcePath $samplePath 'sample snapshot')
  $sampleHash=Assert-FileHash $samplePath $ExpectedSampleSha256 'sample snapshot'
  $sampleItem=Get-Item -LiteralPath $samplePath
  $sampleDuration=[math]::Round([math]::Max(0,($sampleItem.Length-44))/32000.0,3)
  $os=Get-WindowsSystemInfo
  $git=(Get-Command git.exe -ErrorAction Stop).Source
  $head=(& $git -C $ProductionRepo rev-parse HEAD).Trim()
  $branch=(& $git -C $ProductionRepo branch --show-current).Trim()
  $worktree=@(& $git -C $ProductionRepo status --porcelain=v1)
  $pyRaw=& py.exe -3.10 -c "import json,platform,struct,sys; print(json.dumps({'executable':sys.executable,'version':sys.version,'bits':struct.calcsize('P')*8,'platform':platform.platform()}))"
  if($LASTEXITCODE-ne 0){throw 'Python 3.10 lookup failed'}
  $py=$pyRaw|ConvertFrom-Json;$pyHash=Get-Sha256 $py.executable
  $gpuQuery=Invoke-NvidiaSmiQuery '--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits' 15
  if(-not $gpuQuery.success){throw "NVIDIA_SMI_FAILED: exit=$($gpuQuery.exit_code) stderr=$($gpuQuery.stderr)"}
  $gpuSummary=@($gpuQuery.lines)
  $gpuAppsQuery=Invoke-NvidiaSmiQuery '--query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader,nounits' 15
  $gpuAppsEvidence=if($gpuAppsQuery.success){@($gpuAppsQuery.lines)}else{@()}
  $listener=@(Get-TcpListenersByNetstat 8100 5|Select-Object -First 1)
  $bgePid=if($listener.Count-gt 0){[int]$listener[0].OwningProcess}else{$null}
  $bgeBase=$null;$bgeHealth=$null
  foreach($candidate in $BgeCandidates){try{$response=Invoke-RestMethod -Uri ($candidate+'/health')-Method Get -TimeoutSec 10;$bgeBase=$candidate;$bgeHealth=$response;break}catch{}}
  $disk=Get-DriveInfoSnapshot 'E:'
  $phaseMeasure=Get-ChildItem -LiteralPath $PhaseRoot -Recurse -Force -File -ErrorAction SilentlyContinue|Measure-Object Length -Sum
  $phaseBytes=if($null-eq $phaseMeasure.Sum){0L}else{[int64]$phaseMeasure.Sum}
  $activeRuns=@(Get-ChildItem -LiteralPath $PhaseRoot -Recurse -Force -File -ErrorAction SilentlyContinue|Where-Object{$_.Name-match '^active-run.*\.json$'}|Select-Object -ExpandProperty FullName)
  $processes=@(Get-SafeProcessSnapshot)
  $candidatePids=@(Get-ActiveRunCandidatePids $activeRuns 8)
  $exactIdentityResults=@($candidatePids|ForEach-Object{Invoke-ExactProcessIdentityQuery ([int]$_) 5})
  $processGate=Get-AsrProcessGate $activeRuns $processes $exactIdentityResults $runFull $PID
  $proxy=[Uri]$ProxyUri
  $proxyTcp=Test-TcpEndpoint $proxy.Host $proxy.Port 5000  $logRoot=Join-Path $runFull 'logs'
  $headResults=@(
    Invoke-ProxyRequest 'pypi-faster-whisper' 'https://pypi.org/simple/faster-whisper/' $ProxyUri $logRoot $true
    Invoke-ProxyRequest 'pypi-ctranslate2' 'https://pypi.org/simple/ctranslate2/' $ProxyUri $logRoot $true
    Invoke-ProxyRequest 'files-pythonhosted-root' 'https://files.pythonhosted.org/' $ProxyUri $logRoot $true
    Invoke-ProxyRequest 'hf-model-config' ("https://huggingface.co/$ModelId/resolve/$ModelRevision/config.json") $ProxyUri $logRoot $true
    Invoke-ProxyRequest 'hf-model-bin' ("https://huggingface.co/$ModelId/resolve/$ModelRevision/model.bin") $ProxyUri $logRoot $true
  )
  $treeResult=Invoke-ProxyRequest 'hf-model-tree' ("https://huggingface.co/api/models/$ModelId/tree/${ModelRevision}?recursive=true&expand=true") $ProxyUri $logRoot $false
  $pointerResult=Invoke-ProxyRequest 'hf-model-pointer' ("https://huggingface.co/$ModelId/raw/$ModelRevision/model.bin") $ProxyUri $logRoot $false
  $allProxy=@($headResults)+@($treeResult,$pointerResult)
  $proxyEvidence=@($allProxy|ForEach-Object{Convert-ProxyEvidenceDto $_})
  $headOk=@($headResults|Where-Object{-not $_.success}).Count-eq 0
  $metadataGetOk=($treeResult.success-and $pointerResult.success)
  $hostPolicy=Test-AllowedHosts $allProxy $AllowedDownloadHosts
  $treeSha='';$treeSize=0L
  if($treeResult.success){try{$tree=$treeResult.final.body|ConvertFrom-Json;$node=@($tree|Where-Object{$_.path-eq 'model.bin'})|Select-Object -First 1;if($null-ne $node-and $null-ne $node.lfs){$treeSha=([string]$node.lfs.oid).ToLowerInvariant();$treeSize=[int64]$node.lfs.size}}catch{}}
  $rawSha='';$rawSize=0L
  if($pointerResult.success){$body=[string]$pointerResult.final.body;if($body-match '(?m)^oid sha256:([0-9a-f]{64})\s*$'){$rawSha=$Matches[1].ToLowerInvariant()};if($body-match '(?m)^size (\d+)\s*$'){$rawSize=[int64]$Matches[1]}}
  $modelHead=@($headResults|Where-Object{$_.name-eq 'hf-model-bin'})|Select-Object -First 1
  $headEtags=if($null-ne $modelHead){@($modelHead.final.attempt.x_linked_etags)}else{@()}
  $headLengths=if($null-ne $modelHead){@($modelHead.final.attempt.content_lengths)}else{@()}
  $modelGate=Test-ModelMetadata $treeSha $treeSize $rawSha $rawSize $headEtags $headLengths
  $gpuName=(($gpuSummary|Select-Object -First 1)-split ',')[0].Trim()
  $gates=[ordered]@{
    production_timezone_matches=($localTimeZone.Id-eq 'China Standard Time'-and $now.Offset.TotalHours-eq 8);hostname_matches=($env:COMPUTERNAME-eq $ExpectedHost)
    ssh_fingerprint_verified_by_caller=$true;repo_head_matches=($head-eq $ExpectedHead);repo_worktree_clean=($worktree.Count-eq 0)
    python_310_x64=($py.version-like '3.10*'-and [int]$py.bits-eq 64);gpu_matches=($gpuName-like '*RTX 5060 Ti*')
    bge_health_ok=($null-ne $bgeHealth-and $bgeHealth.status-eq 'ok'-and [bool]$bgeHealth.model_loaded)
    disk_free_at_least_30gb=([int64]$disk.FreeSpace-ge 30GB);no_active_run_or_asr_process=(-not $processGate.blocked)
    proxy_tcp_ok=[bool]$proxyTcp.TcpTestSucceeded;proxy_head_ok=$headOk;proxy_metadata_get_ok=$metadataGetOk
    proxy_hosts_allowed=[bool]$hostPolicy.ok;model_metadata_matches_corrected_identity=[bool]$modelGate.ok
    approved_sample_copied=((Test-Path -LiteralPath $samplePath)-and $sampleHash-eq $ExpectedSampleSha256)
  }
  $hardPass=@($gates.GetEnumerator()|Where-Object{-not [bool]$_.Value}).Count-eq 0
  $baseline=[ordered]@{
    schema_version='faster-whisper-r3a-retry5-preflight/1';run_id=$runId;run_root=$runFull;collected_at=($now.ToString('o'));hostname=$env:COMPUTERNAME
    os=$os
    timezone=[ordered]@{id=(Get-TimeZone).Id;display=(Get-TimeZone).DisplayName;reporting='Asia/Shanghai +08:00'}
    ssh=[ordered]@{target='Administrator@${GPU_NODE_ZEROTIER_IP}';expected_ed25519_fingerprint=$ExpectedFingerprint;strict_host_key_checking=$true;kex='curve25519-sha256';verified_by_caller=$true}
    repo=[ordered]@{path=$ProductionRepo;head=$head;branch=$branch;worktree_entries=$worktree}
    python=[ordered]@{executable=$py.executable;version=$py.version;bits=$py.bits;platform=$py.platform;sha256=$pyHash}
    gpu=[ordered]@{summary=$gpuSummary;summary_watchdog=$gpuQuery.watchdog;compute_apps_evidence_only=$gpuAppsEvidence;compute_apps_query=[ordered]@{success=$gpuAppsQuery.success;exit_code=$gpuAppsQuery.exit_code;stderr=$gpuAppsQuery.stderr;watchdog=$gpuAppsQuery.watchdog};bge_pid=$bgePid;wddm_compute_apps_are_not_a_hard_gate=$true}
    bge=[ordered]@{listener=$listener;base_url=$bgeBase;health=$bgeHealth;authenticated_probe='pending-local-dpapi-helper-at-P1'}
    disk=[ordered]@{drive='E:';size_bytes=[int64]$disk.Size;free_bytes=[int64]$disk.FreeSpace;phase0_bytes=$phaseBytes;run_hard_limit_bytes=30GB;run_soft_warning_bytes=25GB}
    process_gate=$processGate
    proxy=[ordered]@{uri=$ProxyUri;type='Clash Verge/Mihomo mixed-port; HTTP primary';socks5h='diagnostic only; no automatic switch';tcp_succeeded=[bool]$proxyTcp.TcpTestSucceeded;tcp_error=[string]$proxyTcp.error;allowed_hosts=$AllowedDownloadHosts;host_policy=$hostPolicy;checks=$proxyEvidence;tls_revocation_checks_disabled=$false}
    model_metadata=[ordered]@{id=$ModelId;revision=$ModelRevision;expected_sha256=$ExpectedModelSha256;expected_size=$ExpectedModelSize;tree_sha256=$treeSha;tree_size=$treeSize;raw_pointer_sha256=$rawSha;raw_pointer_size=$rawSize;head_x_linked_etags=$headEtags;head_content_lengths=$headLengths;gate=$modelGate;superseded_sha256=$SupersededModelSha256}
    sample=[ordered]@{source_path=$SampleSourcePath;path=$samplePath;sha256=$sampleHash;bytes=$sampleItem.Length;duration_seconds_approx=$sampleDuration;declaration='synthetic; non-customer; non-internal'}
    gates=$gates;hard_gate_pass_before_bge_auth=$hardPass
  }
  $baselinePath=Join-Path $runFull 'evidence\a1-baseline.json';Write-JsonFile $baselinePath $baseline
  $approval=[ordered]@{
    schema_version='faster-whisper-r3a-retry5-approval/3';manual_code_and_plan_sha_approval=$false;file_copy_integrity='source/destination byte-length equality plus parser, BOM and SelfTest gates'
    execution_channel='Codex via verified SSH and Bitwarden SSH Agent';execution_authorization=[ordered]@{mode='single-use-no-preset-maintenance-window';identity=$runId;consumed_by='first production SSH call';retry_requires_new_approval=$true;timezone='Asia/Shanghai';recorded_at=($now.ToString('o'));recorded_at_utc=($now.UtcDateTime.ToString('o'))}
    scope=@('A0','A1','A2','A3','A4','A5','A6','A7','A8');excluded=@('R3-B','frozen-8-sample','long-audio','BGE-concurrency-stress','Phase-1','FunASR-replacement')
    pause_points=@('P1','P2','P3','P4');bge_auth=[ordered]@{approved=$true;method='local input -> 15-minute DPAPI file -> exact cleanup';status='pending P1'}
    sample=[ordered]@{path=$samplePath;sha256=$sampleHash;declaration='synthetic; non-customer; non-internal'};failure_artifacts='retain complete run'
    proxy=[ordered]@{type='Clash Verge/Mihomo mixed-port';primary_uri=$ProxyUri;primary_protocol='HTTP';socks5h='diagnostic only';exact_download_hosts=$AllowedDownloadHosts;tls_revocation_checks_disabled=$false}
    license_blocker=[ordered]@{approver_role='bim-admin';blanket_approval=$false;exact_blocker_approval_required_at='P3'}
    timeouts_minutes=[ordered]@{wheel_download=30;model_download=120;offline_install=30;model_load=15;inference=10}
    date_consistency=$dateConsistency
    dpapi_exact_cleanup_approved=$true;automatic_stop_and_rollback_approved=$true
  }
  $approvalPath=Join-Path $runFull 'config\approval.json';Write-JsonFile $approvalPath $approval
  $config=[ordered]@{
    schema_version='faster-whisper-r3a-retry5-config/2';run_id=$runId;run_root=$runFull
    packages=[ordered]@{'faster-whisper'='1.2.1';'ctranslate2'='4.8.1'}
    model=[ordered]@{id=$ModelId;revision=$ModelRevision;model_bin_size=$ExpectedModelSize;model_bin_sha256=$ExpectedModelSha256;supersedes_incorrect_record=$SupersededModelSha256}
    first_smoke=[ordered]@{device='cuda';compute_type='float16';sample_path=$samplePath;sample_sha256=$sampleHash;reference_text='这是自制合成语音，仅用于 faster whisper 冒烟测试。请检查建筑信息模型、碰撞检测和施工图审查。'}
    execution_time=[ordered]@{local=($now.ToString('o'));utc=($now.UtcDateTime.ToString('o'));timezone=$localTimeZone.Id;offset=($now.Offset.ToString());preapproved_calendar_window=$false}
    resource_gates=[ordered]@{single_asr_peak_vram_bytes=8GB;asr_plus_bge_vram_bytes=14GB;run_disk_soft_bytes=25GB;run_disk_hard_bytes=30GB}
    proxy=[ordered]@{http=$ProxyUri;https=$ProxyUri;type='mixed-port';allowed_hosts=$AllowedDownloadHosts;no_proxy_protocol_fallback=$true}
    timeouts_minutes=$approval.timeouts_minutes;bge=[ordered]@{base_url=$bgeBase;health_required=[ordered]@{status='ok';model_loaded=$true};authenticated_probe=$true}
    artifact_policy='retain complete run';pause_points=@('P1','P2','P3','P4')
  }
  $configPath=Join-Path $runFull 'config\r3a-config.json';Write-JsonFile $configPath $config
  $helperEntries=@(Get-ChildItem -LiteralPath (Join-Path $runFull 'helpers')-File|Sort-Object Name|ForEach-Object{[ordered]@{name=$_.Name;path=$_.FullName;bytes=[int64]$_.Length}})
  $manifestPath=Join-Path $runFull 'state\helper-manifest.json';Write-JsonFile $manifestPath ([ordered]@{schema_version='faster-whisper-r3a-retry5-helper-manifest/2';created_at=([DateTimeOffset]::Now.ToString('o'));manual_sha_approval=$false;helpers=$helperEntries})
  $identity=[ordered]@{
    schema_version='faster-whisper-r3a-retry5-run-identity/2';run_id=$runId;run_root=$runFull;created_at=([DateTimeOffset]::Now.ToString('o'))
    retry_plan_bytes=$retryPlanBytes;original_plan_bytes=$originalPlanBytes;static_precheck_bytes=$staticPrecheckBytes
    config_bytes=[int64](Get-Item -LiteralPath $configPath).Length;approval_bytes=[int64](Get-Item -LiteralPath $approvalPath).Length;helper_manifest_bytes=[int64](Get-Item -LiteralPath $manifestPath).Length;helpers=$helperEntries
    source_bytes=[ordered]@{retry_plan=$retryPlanSourceBytes;original_plan=$originalPlanSourceBytes;static_precheck=$staticPrecheckSourceBytes;helper=$helperSourceBytes;bge_helper=$bgeHelperSourceBytes}
    copied_bytes=[ordered]@{retry_plan=$retryPlanBytes;original_plan=$originalPlanBytes;static_precheck=$staticPrecheckBytes;helper=$helperBytes;bge_helper=$bgeHelperBytes}
    sample_sha256=$sampleHash;model_bin_sha256=$ExpectedModelSha256;model_bin_size=$ExpectedModelSize
  }
  $identityPath=Join-Path $runFull 'state\run-identity.json';Write-JsonFile $identityPath $identity
  $gateLines=$gates.GetEnumerator()|ForEach-Object{'- {0}: {1}'-f $_.Key,$_.Value}
  $proxyLines=$allProxy|ForEach-Object{Format-ProxyLine $_}
  $preflight=@(
    '# faster-whisper R3-A retry5 P1 preflight','',('- Run: `{0}`'-f $runFull),('- Collected: `{0}`'-f $now.ToString('o')),
    ('- Host: `{0}`'-f $env:COMPUTERNAME),('- Production HEAD: `{0}`'-f $head),
    ('- BGE: `{0}` / status=`{1}` / model_loaded=`{2}`'-f $bgeBase,$bgeHealth.status,$bgeHealth.model_loaded),
    ('- GPU: `{0}`'-f ($gpuSummary-join '; ')),('- WDDM compute-app rows: `{0}` (evidence only, not a hard gate)'-f $gpuAppsEvidence.Count),
    ('- E: free bytes: `{0}`'-f [int64]$disk.FreeSpace),('- Synthetic WAV SHA-256: `{0}`'-f $sampleHash),
    ('- Proxy: `{0}` / TCP=`{1}` / type=`mixed-port, HTTP primary`'-f $ProxyUri,$proxyTcp.TcpTestSucceeded),
    ('- Corrected model.bin SHA-256: `{0}` / size=`{1}`'-f $ExpectedModelSha256,$ExpectedModelSize),
    ('- Hard gate pass before authenticated BGE probe: `{0}`'-f $hardPass),'','## Gates','',$gateLines,'',
    '## Proxy checks (HEAD or bounded metadata GET; no wheel/model artifact download)','',$proxyLines,'',
    '## Mandatory pending item before leaving P1','',
    '- Run `helpers\run-bge-auth-probe.ps1` locally on the ${PRODUCTION_HOSTNAME} desktop and enter the token there.',
    '- Review observed hosts; only the four approved hosts may be used by later downloads.',
    '- Do not download wheels or model files before explicit P1 continuation.'
  )-join "`r`n"
  $preflightPath=Join-Path $runFull 'reports\preflight.md';Set-Content -LiteralPath $preflightPath -Value $preflight -Encoding UTF8
  $result=[ordered]@{run_root=$runFull;hard_gate_pass_before_bge_auth=$hardPass;baseline_path=$baselinePath;config_path=$configPath;approval_path=$approvalPath;identity_path=$identityPath;preflight_path=$preflightPath;sample_path=$samplePath;sample_sha256=$sampleHash;model_bin_sha256=$ExpectedModelSha256;auth_probe_status='pending-local-dpapi-helper'}
  Write-Output ('R3A_RETRY5_RESULT='+($result|ConvertTo-Json -Compress -Depth 8))
  if(-not $hardPass){exit 20};exit 0
}catch{
  $message=$_.Exception.Message
  if($createdRun-and (Test-Path -LiteralPath $RunRoot -PathType Container)){
    try{$stop=@('# faster-whisper R3-A retry5 automatic stop','',('- Time: `{0}`'-f [DateTimeOffset]::Now.ToString('o')),('- Run: `{0}`'-f $RunRoot),('- Error: `{0}`'-f $message),'- State: `STOPPED_BEFORE_P1_COMPLETE`','- Failure artifact policy: retain complete run; no automatic deletion.')-join "`r`n";Set-Content -LiteralPath (Join-Path $RunRoot 'reports\stop-event.md')-Value $stop -Encoding UTF8}catch{}
  }
  Write-Error $message;exit 70
}