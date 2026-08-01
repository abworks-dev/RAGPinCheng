[CmdletBinding()]
param(
  [switch]$SelfTest,
  [string]$RunRoot,
  [string]$RetryPlanSourcePath,
  [string]$ExpectedRetryPlanSha256,
  [string]$OriginalPlanSourcePath,
  [string]$StaticPrecheckSourcePath,
  [string]$BgeHelperSourcePath,
  [string]$ExpectedBgeHelperSha256,
  [string]$SampleSourcePath,
  [string]$ExpectedHelperSha256,
  [string]$WindowStart,
  [string]$WindowEnd,
  [string]$ProxyUri = 'http://10.205.165.230:7897'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedHost = 'FJPCSEVER'
$ExpectedHead = 'e2374e37e1357be3d8df93d6d3429bb0947fb9ba'
$ExpectedFingerprint = 'SHA256:nRSpKS3UAsE2IecHqyxSryD4Q9Af1piSF4siM+LTS9M'
$ExpectedOriginalPlanSha256 = 'e2508a827441d8e7fea61441be9e6551e4a94ee6fd1f903048b5017c8baf08d1'
$ExpectedStaticPrecheckSha256 = '2edb7c53fc9aec9818eec6be70fd1fa3873d3ce4b0900d7c53e819a9fee9717e'
$ExpectedSampleSha256 = 'af9ad1728ab8acc6b06a2ead8d520df88e8368d5301f18cde8be01ae175a12c9'
$ExpectedModelSha256 = 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da'
$SupersededModelSha256 = 'e76620f83d5f5769e6a5f66c8013e1292a797de79b3581b44b6c7f9e36d77f31'
$ExpectedModelSize = 1617884929L
$ModelId = 'dropbox-dash/faster-whisper-large-v3-turbo'
$ModelRevision = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
$AllowedRunParent = 'E:\FunASR-Phase0\faster-whisper-runs'
$ProductionRepo = 'D:\RAGPinCheng'
$PhaseRoot = 'E:\FunASR-Phase0'
$AllowedDownloadHosts = @('pypi.org','files.pythonhosted.org','huggingface.co','us.aws.cdn.hf.co')
$BgeCandidates = @('http://127.0.0.1:8100','http://192.168.11.11:8100')

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
function Assert-FileHash([string]$Path,[string]$Expected,[string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
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
    $p.WaitForExit()
    return [ordered]@{exit_code=124;stdout=[string]$outTask.Result;stderr=(([string]$errTask.Result)+"`r`ntimeout").Trim();timed_out=$true}
  }
  $p.WaitForExit()
  [ordered]@{exit_code=[int]$p.ExitCode;stdout=[string]$outTask.Result;stderr=[string]$errTask.Result;timed_out=$false}
}
function Read-HeaderEvidence([string]$Path) {
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
function Get-AsrProcessGate([object[]]$ActiveRunFiles,[object[]]$Processes,[string]$CurrentRunRoot,[int]$CurrentProcessId) {
  $named=@();$bound=@()
  foreach($process in @($Processes)) {
    $id=if($null-ne $process.ProcessId){[int]$process.ProcessId}else{0}
    if($id-eq $CurrentProcessId){continue}
    $name=[string]$process.Name;$exe=[string]$process.ExecutablePath;$cmd=[string]$process.CommandLine
    if(($name+' '+$exe)-match '(?i)(faster[-_]?whisper|ctranslate2)'){$named+=$process}
    $mentions=(-not [string]::IsNullOrWhiteSpace($CurrentRunRoot)-and $cmd.IndexOf($CurrentRunRoot,[StringComparison]::OrdinalIgnoreCase)-ge 0)
    $asrLike=($cmd-match '(?i)(faster[-_]?whisper|ctranslate2|WhisperModel|transcrib(e|ing)|\bpython(w)?\.exe\b)')
    if($mentions-and $asrLike){$bound+=$process}
  }
  [ordered]@{blocked=(@($ActiveRunFiles).Count-gt 0-or $named.Count-gt 0-or $bound.Count-gt 0);active_run_files=@($ActiveRunFiles);named_processes=$named;run_bound_processes=$bound}
}
function Test-AllowedHosts([object[]]$Results,[string[]]$Allowed) {
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
  $failures=New-Object Collections.Generic.List[string]
  function Check([bool]$Condition,[string]$Message){if(-not $Condition){$failures.Add($Message)}}
  $empty=[ordered]@{status_lines=@();locations=@();x_linked_etags=@();content_lengths=@()}
  $okCap=[ordered]@{exit_code=0;stdout='200|https://pypi.org/simple/||1.1.1.1|0';stderr='';timed_out=$false}
  $ok=Normalize-CurlResult 'ok' 'https://pypi.org/simple/' 1 $okCap $empty 'mock'
  Check ($ok.success-and $ok.http_code-eq 200) 'proxy success normalization failed'
  $badCap=[ordered]@{exit_code=35;stdout='';stderr='schannel failed';timed_out=$false}
  $bad=Normalize-CurlResult 'tls' 'https://huggingface.co/' 1 $badCap $null 'mock'
  $badLine=Format-ProxyLine ([ordered]@{name='tls';success=$false;final=[ordered]@{attempt=$bad}})
  Check ((-not $bad.success)-and $bad.http_code-eq 0-and $badLine-match 'exit=35') 'curl exit 35/null handling failed'
  $redCap=[ordered]@{exit_code=0;stdout='307|https://huggingface.co/b|https://huggingface.co/b|1.1.1.1|0';stderr='';timed_out=$false}
  $red=Normalize-CurlResult 'redirect' 'https://huggingface.co/a' 1 $redCap $empty 'mock'
  Check ($red.success-and $red.http_code-eq 307) 'HTTP 307 handling failed'
  $gui=[pscustomobject]@{ProcessId=100;Name='explorer.exe';ExecutablePath='C:\Windows\explorer.exe';CommandLine='C:\Windows\explorer.exe'}
  $gateGui=Get-AsrProcessGate @() @($gui) 'E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-test' 999
  Check (-not $gateGui.blocked) 'WDDM GUI evidence incorrectly blocked A1'
  $gateActive=Get-AsrProcessGate @('E:\FunASR-Phase0\active-run.json') @($gui) 'E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-test' 999
  Check $gateActive.blocked 'active-run did not block A1'
  $asr=[pscustomobject]@{ProcessId=101;Name='python.exe';ExecutablePath='C:\Python310\python.exe';CommandLine='python.exe E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-test\smoke.py --model faster-whisper'}
  Check ((Get-AsrProcessGate @() @($asr) 'E:\FunASR-Phase0\faster-whisper-runs\phase0-fw-r3a-retry-test' 999).blocked) 'run-bound ASR process did not block A1'
  Check ((Test-ModelMetadata $ExpectedModelSha256 $ExpectedModelSize $ExpectedModelSha256 $ExpectedModelSize @($ExpectedModelSha256) @(1061L,$ExpectedModelSize)).ok) 'correct model metadata rejected'
  Check (-not (Test-ModelMetadata $SupersededModelSha256 $ExpectedModelSize $SupersededModelSha256 $ExpectedModelSize @($SupersededModelSha256) @($ExpectedModelSize)).ok) 'superseded model SHA accepted'
  $selfTestPath=Join-Path ([IO.Path]::GetTempPath()) ('faster-whisper-r3a-selftest-{0}.json' -f [Guid]::NewGuid().ToString('N'))
  try {
    [IO.File]::WriteAllText($selfTestPath,'{"path":"model.bin","lfs":{"oid":"e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da","size":1617884929}}',[Text.Encoding]::UTF8)
    $decorated=Get-Content -LiteralPath $selfTestPath -Raw
    $plain=Read-PlainUtf8Text $selfTestPath
    Check (@($decorated.PSObject.Properties|Where-Object{$_.Name -eq 'PSPath'}).Count -eq 1) 'selftest fixture did not reproduce Get-Content ETS properties'
    Check (@($plain.PSObject.Properties|Where-Object{$_.Name -eq 'PSPath'}).Count -eq 0) 'plain UTF-8 reader retained PSPath ETS property'
    Check ([string]$decorated -eq [string]$plain) 'plain UTF-8 reader changed body text'
    $mockRecord=[ordered]@{attempt=$ok;body_path=$selfTestPath;body_bytes=(Get-Item -LiteralPath $selfTestPath).Length}
    $mockResult=[ordered]@{name='selftest';url='https://huggingface.co/';head_only=$false;attempts=@($mockRecord);final=$mockRecord;success=$true}
    $dto=Convert-ProxyEvidenceDto $mockResult
    $dtoJson=$dto|ConvertTo-Json -Depth 16 -Compress
    Check ($dtoJson.Length -lt 10000) 'proxy evidence DTO serialization was not bounded'
    Check ($dtoJson -notmatch 'PSProvider|PSPath|PSDrive') 'proxy evidence DTO retained ETS properties'
    Check (-not $dto.Contains('final')) 'proxy evidence DTO retained duplicate final object'
    Check ($dto.final_attempt_number -eq 1) 'proxy evidence DTO final attempt number incorrect'
  } finally {
    Remove-Item -LiteralPath $selfTestPath -Force -ErrorAction SilentlyContinue
  }
  $result=[ordered]@{tests=16;failures=@($failures);passed=($failures.Count-eq 0)}
  Write-Output ('R3A_RETRY_SELFTEST='+($result|ConvertTo-Json -Compress -Depth 6))
  if($failures.Count-gt 0){exit 99};exit 0
}
if($SelfTest){Invoke-SelfTest}

$createdRun=$false
try {
  foreach($pair in @(
    @('RunRoot',$RunRoot),@('RetryPlanSourcePath',$RetryPlanSourcePath),@('ExpectedRetryPlanSha256',$ExpectedRetryPlanSha256),
    @('OriginalPlanSourcePath',$OriginalPlanSourcePath),@('StaticPrecheckSourcePath',$StaticPrecheckSourcePath),
    @('BgeHelperSourcePath',$BgeHelperSourcePath),@('ExpectedBgeHelperSha256',$ExpectedBgeHelperSha256),@('SampleSourcePath',$SampleSourcePath),
    @('ExpectedHelperSha256',$ExpectedHelperSha256),@('WindowStart',$WindowStart),@('WindowEnd',$WindowEnd)
  )){Require-Value $pair[0] $pair[1]}
  $start=[DateTimeOffset]::Parse($WindowStart);$end=[DateTimeOffset]::Parse($WindowEnd);$now=[DateTimeOffset]::Now
  if($start.Offset.TotalHours-ne 8-or $end.Offset.TotalHours-ne 8){throw 'maintenance window must use +08:00'}
  if($end-le $start){throw 'maintenance window end must be after start'}
  if($now-lt $start-or $now-ge $end){throw "outside maintenance window: $($now.ToString('o'))"}
  if($ProxyUri-ne 'http://10.205.165.230:7897'){throw "unapproved proxy URI: $ProxyUri"}
  $runFull=[IO.Path]::GetFullPath($RunRoot).TrimEnd('\');$parent=[IO.Path]::GetFullPath($AllowedRunParent).TrimEnd('\')
  if([IO.Path]::GetDirectoryName($runFull)-ne $parent){throw "RunRoot must be a direct child of $parent"}
  $runId=Split-Path -Leaf $runFull
  if($runId-notmatch '^phase0-fw-r3a-retry-\d{8}-\d{6}$'){throw "invalid retry run id: $runId"}
  if(Test-Path -LiteralPath $runFull){throw "RunRoot already exists; use a new identity: $runFull"}
  if($env:COMPUTERNAME-ne $ExpectedHost){throw "hostname mismatch: $($env:COMPUTERNAME)"}
  Assert-FileHash $PSCommandPath $ExpectedHelperSha256 'retry A0/A1 helper'|Out-Null
  Assert-FileHash $RetryPlanSourcePath $ExpectedRetryPlanSha256 'retry execution plan'|Out-Null
  Assert-FileHash $OriginalPlanSourcePath $ExpectedOriginalPlanSha256 'original approved execution plan'|Out-Null
  Assert-FileHash $StaticPrecheckSourcePath $ExpectedStaticPrecheckSha256 'static precheck'|Out-Null
  $bgeHelperHash=Assert-FileHash $BgeHelperSourcePath $ExpectedBgeHelperSha256 'retry BGE helper'
  Assert-FileHash $SampleSourcePath $ExpectedSampleSha256 'approved synthetic sample'|Out-Null
  foreach($name in @('config','helpers','venv','wheels','hf-cache','model','evidence','logs','reports','state','testdata')){New-Item -ItemType Directory -Path (Join-Path $runFull $name)-Force|Out-Null}
  $createdRun=$true
  $retrySnap=Join-Path $runFull 'evidence\faster-whisper-r3a-retry-execution-plan.md'
  $originalSnap=Join-Path $runFull 'evidence\faster-whisper-r3a-execution-plan.md'
  $staticSnap=Join-Path $runFull 'evidence\faster-whisper-phase0-precheck.md'
  $helperSnap=Join-Path $runFull 'helpers\faster-whisper-r3a-retry-a0-a1.ps1'
  $bgeSnap=Join-Path $runFull 'helpers\run-bge-auth-probe.ps1'
  $samplePath=Join-Path $runFull 'testdata\r3a-synthetic-zh.wav'
  Copy-Item -LiteralPath $RetryPlanSourcePath -Destination $retrySnap
  Copy-Item -LiteralPath $OriginalPlanSourcePath -Destination $originalSnap
  Copy-Item -LiteralPath $StaticPrecheckSourcePath -Destination $staticSnap
  Copy-Item -LiteralPath $PSCommandPath -Destination $helperSnap
  Copy-Item -LiteralPath $BgeHelperSourcePath -Destination $bgeSnap
  Copy-Item -LiteralPath $SampleSourcePath -Destination $samplePath
  Assert-FileHash $retrySnap $ExpectedRetryPlanSha256 'retry plan snapshot'|Out-Null
  Assert-FileHash $originalSnap $ExpectedOriginalPlanSha256 'original plan snapshot'|Out-Null
  Assert-FileHash $staticSnap $ExpectedStaticPrecheckSha256 'static precheck snapshot'|Out-Null
  Assert-FileHash $helperSnap $ExpectedHelperSha256 'retry helper snapshot'|Out-Null
  Assert-FileHash $bgeSnap $bgeHelperHash 'BGE helper snapshot'|Out-Null
  $sampleHash=Assert-FileHash $samplePath $ExpectedSampleSha256 'sample snapshot'
  $sampleItem=Get-Item -LiteralPath $samplePath
  $sampleDuration=[math]::Round([math]::Max(0,($sampleItem.Length-44))/32000.0,3)
  $os=Get-CimInstance Win32_OperatingSystem
  $git=(Get-Command git.exe -ErrorAction Stop).Source
  $head=(& $git -C $ProductionRepo rev-parse HEAD).Trim()
  $branch=(& $git -C $ProductionRepo branch --show-current).Trim()
  $worktree=@(& $git -C $ProductionRepo status --porcelain=v1)
  $pyRaw=& py.exe -3.10 -c "import json,platform,struct,sys; print(json.dumps({'executable':sys.executable,'version':sys.version,'bits':struct.calcsize('P')*8,'platform':platform.platform()}))"
  if($LASTEXITCODE-ne 0){throw 'Python 3.10 lookup failed'}
  $py=$pyRaw|ConvertFrom-Json;$pyHash=Get-Sha256 $py.executable
  $gpuSummary=@(& nvidia-smi.exe --query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits)
  if($LASTEXITCODE-ne 0){throw 'nvidia-smi GPU query failed'}
  $gpuAppsEvidence=@(& nvidia-smi.exe --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader,nounits)
  if($LASTEXITCODE-ne 0){$gpuAppsEvidence=@()}
  $listener=@(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue|Select-Object -First 1 LocalAddress,LocalPort,OwningProcess)
  $bgePid=if($listener.Count-gt 0){[int]$listener[0].OwningProcess}else{$null}
  $bgeBase=$null;$bgeHealth=$null
  foreach($candidate in $BgeCandidates){try{$response=Invoke-RestMethod -Uri ($candidate+'/health')-Method Get -TimeoutSec 10;$bgeBase=$candidate;$bgeHealth=$response;break}catch{}}
  $disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'"
  $phaseMeasure=Get-ChildItem -LiteralPath $PhaseRoot -Recurse -Force -File -ErrorAction SilentlyContinue|Measure-Object Length -Sum
  $phaseBytes=if($null-eq $phaseMeasure.Sum){0L}else{[int64]$phaseMeasure.Sum}
  $activeRuns=@(Get-ChildItem -LiteralPath $PhaseRoot -Recurse -Force -File -ErrorAction SilentlyContinue|Where-Object{$_.Name-match '^active-run.*\.json$'}|Select-Object -ExpandProperty FullName)
  $processes=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue|Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine)
  $processGate=Get-AsrProcessGate $activeRuns $processes $runFull $PID
  $proxy=[Uri]$ProxyUri
  $proxyTcp=Test-NetConnection -ComputerName $proxy.Host -Port $proxy.Port -InformationLevel Detailed -WarningAction SilentlyContinue
  $logRoot=Join-Path $runFull 'logs'
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
    maintenance_window_active=($now-ge $start-and $now-lt $end);hostname_matches=($env:COMPUTERNAME-eq $ExpectedHost)
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
    schema_version='faster-whisper-r3a-retry-preflight/1';run_id=$runId;run_root=$runFull;collected_at=$now.ToString('o');hostname=$env:COMPUTERNAME
    os=[ordered]@{caption=$os.Caption;version=$os.Version;build=$os.BuildNumber;architecture=$os.OSArchitecture}
    timezone=[ordered]@{id=(Get-TimeZone).Id;display=(Get-TimeZone).DisplayName;reporting='Asia/Shanghai +08:00'}
    ssh=[ordered]@{target='Administrator@10.205.165.105';expected_ed25519_fingerprint=$ExpectedFingerprint;strict_host_key_checking=$true;kex='curve25519-sha256';verified_by_caller=$true}
    repo=[ordered]@{path=$ProductionRepo;head=$head;branch=$branch;worktree_entries=$worktree}
    python=[ordered]@{executable=$py.executable;version=$py.version;bits=$py.bits;platform=$py.platform;sha256=$pyHash}
    gpu=[ordered]@{summary=$gpuSummary;compute_apps_evidence_only=$gpuAppsEvidence;bge_pid=$bgePid;wddm_compute_apps_are_not_a_hard_gate=$true}
    bge=[ordered]@{listener=$listener;base_url=$bgeBase;health=$bgeHealth;authenticated_probe='pending-local-dpapi-helper-at-P1'}
    disk=[ordered]@{drive='E:';size_bytes=[int64]$disk.Size;free_bytes=[int64]$disk.FreeSpace;phase0_bytes=$phaseBytes;run_hard_limit_bytes=30GB;run_soft_warning_bytes=25GB}
    process_gate=$processGate
    proxy=[ordered]@{uri=$ProxyUri;type='Clash Verge/Mihomo mixed-port; HTTP primary';socks5h='diagnostic only; no automatic switch';tcp_succeeded=[bool]$proxyTcp.TcpTestSucceeded;allowed_hosts=$AllowedDownloadHosts;host_policy=$hostPolicy;checks=$proxyEvidence;tls_revocation_checks_disabled=$false}
    model_metadata=[ordered]@{id=$ModelId;revision=$ModelRevision;expected_sha256=$ExpectedModelSha256;expected_size=$ExpectedModelSize;tree_sha256=$treeSha;tree_size=$treeSize;raw_pointer_sha256=$rawSha;raw_pointer_size=$rawSize;head_x_linked_etags=$headEtags;head_content_lengths=$headLengths;gate=$modelGate;superseded_sha256=$SupersededModelSha256}
    sample=[ordered]@{source_path=$SampleSourcePath;path=$samplePath;sha256=$sampleHash;bytes=$sampleItem.Length;duration_seconds_approx=$sampleDuration;declaration='synthetic; non-customer; non-internal'}
    gates=$gates;hard_gate_pass_before_bge_auth=$hardPass
  }
  $baselinePath=Join-Path $runFull 'evidence\a1-baseline.json';Write-JsonFile $baselinePath $baseline
  $approval=[ordered]@{
    schema_version='faster-whisper-r3a-retry-approval/1';approved_retry_plan_sha256=$ExpectedRetryPlanSha256.ToLowerInvariant();original_plan_sha256=$ExpectedOriginalPlanSha256
    execution_channel='Codex via verified SSH and Bitwarden SSH Agent';maintenance_window=[ordered]@{start=$start.ToString('o');end=$end.ToString('o');timezone='Asia/Shanghai'}
    scope=@('A0','A1','A2','A3','A4','A5','A6','A7','A8');excluded=@('R3-B','frozen-8-sample','long-audio','BGE-concurrency-stress','Phase-1','FunASR-replacement')
    pause_points=@('P1','P2','P3','P4');bge_auth=[ordered]@{approved=$true;method='local input -> 15-minute DPAPI file -> exact cleanup';status='pending P1'}
    sample=[ordered]@{path=$samplePath;sha256=$sampleHash;declaration='synthetic; non-customer; non-internal'};failure_artifacts='retain complete run'
    proxy=[ordered]@{type='Clash Verge/Mihomo mixed-port';primary_uri=$ProxyUri;primary_protocol='HTTP';socks5h='diagnostic only';exact_download_hosts=$AllowedDownloadHosts;tls_revocation_checks_disabled=$false}
    license_blocker=[ordered]@{approver_role='bim-admin';blanket_approval=$false;exact_blocker_approval_required_at='P3'}
    timeouts_minutes=[ordered]@{wheel_download=30;model_download=120;offline_install=30;model_load=15;inference=10}
    date_consistency='UTC calendar date 2026-07-31 corresponds to Asia/Shanghai calendar date 2026-08-01 during this window; reports use Asia/Shanghai +08:00.'
    dpapi_exact_cleanup_approved=$true;automatic_stop_and_rollback_approved=$true
  }
  $approvalPath=Join-Path $runFull 'config\approval.json';Write-JsonFile $approvalPath $approval
  $config=[ordered]@{
    schema_version='faster-whisper-r3a-retry-config/1';run_id=$runId;run_root=$runFull
    packages=[ordered]@{'faster-whisper'='1.2.1';'ctranslate2'='4.8.1'}
    model=[ordered]@{id=$ModelId;revision=$ModelRevision;model_bin_size=$ExpectedModelSize;model_bin_sha256=$ExpectedModelSha256;supersedes_incorrect_record=$SupersededModelSha256}
    first_smoke=[ordered]@{device='cuda';compute_type='float16';sample_path=$samplePath;sample_sha256=$sampleHash;reference_text='这是自制合成语音，仅用于 faster whisper 冒烟测试。请检查建筑信息模型、碰撞检测和施工图审查。'}
    maintenance_window=[ordered]@{start=$start.ToString('o');end=$end.ToString('o');timezone='Asia/Shanghai'}
    resource_gates=[ordered]@{single_asr_peak_vram_bytes=8GB;asr_plus_bge_vram_bytes=14GB;run_disk_soft_bytes=25GB;run_disk_hard_bytes=30GB}
    proxy=[ordered]@{http=$ProxyUri;https=$ProxyUri;type='mixed-port';allowed_hosts=$AllowedDownloadHosts;no_proxy_protocol_fallback=$true}
    timeouts_minutes=$approval.timeouts_minutes;bge=[ordered]@{base_url=$bgeBase;health_required=[ordered]@{status='ok';model_loaded=$true};authenticated_probe=$true}
    artifact_policy='retain complete run';pause_points=@('P1','P2','P3','P4')
  }
  $configPath=Join-Path $runFull 'config\r3a-config.json';Write-JsonFile $configPath $config
  $helperEntries=@(Get-ChildItem -LiteralPath (Join-Path $runFull 'helpers')-File|Sort-Object Name|ForEach-Object{[ordered]@{name=$_.Name;path=$_.FullName;sha256=Get-Sha256 $_.FullName;bytes=$_.Length}})
  $manifestPath=Join-Path $runFull 'state\helper-manifest.json';Write-JsonFile $manifestPath ([ordered]@{schema_version='faster-whisper-r3a-retry-helper-manifest/1';created_at=[DateTimeOffset]::Now.ToString('o');helpers=$helperEntries})
  $identity=[ordered]@{
    schema_version='faster-whisper-r3a-retry-run-identity/1';run_id=$runId;run_root=$runFull;created_at=[DateTimeOffset]::Now.ToString('o')
    approved_retry_plan_sha256=Get-Sha256 $retrySnap;original_plan_sha256=Get-Sha256 $originalSnap;static_precheck_sha256=Get-Sha256 $staticSnap
    config_sha256=Get-Sha256 $configPath;approval_sha256=Get-Sha256 $approvalPath;helper_manifest_sha256=Get-Sha256 $manifestPath;helpers=$helperEntries
    sample_sha256=$sampleHash;model_bin_sha256=$ExpectedModelSha256;model_bin_size=$ExpectedModelSize
  }
  $identityPath=Join-Path $runFull 'state\run-identity.json';Write-JsonFile $identityPath $identity
  $gateLines=$gates.GetEnumerator()|ForEach-Object{'- {0}: {1}'-f $_.Key,$_.Value}
  $proxyLines=$allProxy|ForEach-Object{Format-ProxyLine $_}
  $preflight=@(
    '# faster-whisper R3-A retry P1 preflight','',('- Run: `{0}`'-f $runFull),('- Collected: `{0}`'-f $now.ToString('o')),
    ('- Host: `{0}`'-f $env:COMPUTERNAME),('- Production HEAD: `{0}`'-f $head),
    ('- BGE: `{0}` / status=`{1}` / model_loaded=`{2}`'-f $bgeBase,$bgeHealth.status,$bgeHealth.model_loaded),
    ('- GPU: `{0}`'-f ($gpuSummary-join '; ')),('- WDDM compute-app rows: `{0}` (evidence only, not a hard gate)'-f $gpuAppsEvidence.Count),
    ('- E: free bytes: `{0}`'-f [int64]$disk.FreeSpace),('- Synthetic WAV SHA-256: `{0}`'-f $sampleHash),
    ('- Proxy: `{0}` / TCP=`{1}` / type=`mixed-port, HTTP primary`'-f $ProxyUri,$proxyTcp.TcpTestSucceeded),
    ('- Corrected model.bin SHA-256: `{0}` / size=`{1}`'-f $ExpectedModelSha256,$ExpectedModelSize),
    ('- Hard gate pass before authenticated BGE probe: `{0}`'-f $hardPass),'','## Gates','',$gateLines,'',
    '## Proxy checks (HEAD or bounded metadata GET; no wheel/model artifact download)','',$proxyLines,'',
    '## Mandatory pending item before leaving P1','',
    '- Run `helpers\run-bge-auth-probe.ps1` locally on the FJPCSEVER desktop and enter the token there.',
    '- Review observed hosts; only the four approved hosts may be used by later downloads.',
    '- Do not download wheels or model files before explicit P1 continuation.'
  )-join "`r`n"
  $preflightPath=Join-Path $runFull 'reports\preflight.md';Set-Content -LiteralPath $preflightPath -Value $preflight -Encoding UTF8
  $result=[ordered]@{run_root=$runFull;hard_gate_pass_before_bge_auth=$hardPass;baseline_path=$baselinePath;config_path=$configPath;approval_path=$approvalPath;identity_path=$identityPath;preflight_path=$preflightPath;sample_path=$samplePath;sample_sha256=$sampleHash;model_bin_sha256=$ExpectedModelSha256;auth_probe_status='pending-local-dpapi-helper'}
  Write-Output ('R3A_RETRY_RESULT='+($result|ConvertTo-Json -Compress -Depth 8))
  if(-not $hardPass){exit 20};exit 0
}catch{
  $message=$_.Exception.Message
  if($createdRun-and (Test-Path -LiteralPath $RunRoot -PathType Container)){
    try{$stop=@('# faster-whisper R3-A retry automatic stop','',('- Time: `{0}`'-f [DateTimeOffset]::Now.ToString('o')),('- Run: `{0}`'-f $RunRoot),('- Error: `{0}`'-f $message),'- State: `STOPPED_BEFORE_P1_COMPLETE`','- Failure artifact policy: retain complete run; no automatic deletion.')-join "`r`n";Set-Content -LiteralPath (Join-Path $RunRoot 'reports\stop-event.md')-Value $stop -Encoding UTF8}catch{}
  }
  Write-Error $message;exit 70
}