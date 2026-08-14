[CmdletBinding()]
param(
  [switch]$SelfTest,
  [string]$RunRoot
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$AllowedRunParent='${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs'
$AllowedBgeUrls=@('http://127.0.0.1:8100','http://${GPU_SERVICE_IP}:8100')
function Get-Sha256([string]$Path){(Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()}
function Write-JsonFile([string]$Path,$Value){$Value|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $Path -Encoding UTF8}
function Resolve-Run([string]$Value){
  if([string]::IsNullOrWhiteSpace($Value)){throw 'RunRoot is required'}
  $full=[IO.Path]::GetFullPath($Value).TrimEnd('\');$parent=[IO.Path]::GetFullPath($AllowedRunParent).TrimEnd('\')
  if([IO.Path]::GetDirectoryName($full)-ne $parent){throw "RunRoot must be a direct child of $parent"}
  if((Split-Path -Leaf $full)-notmatch '^phase0-fw-r3a-retry-\d{8}-\d{6}$'){throw "invalid retry run identity: $full"}
  $full
}
if($SelfTest){
  $sample=Resolve-Run '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry-20260801-080000'
  $token=Join-Path $sample ('state\bge-token-'+(Split-Path -Leaf $sample)+'.dpapi')
  $result=[ordered]@{passed=($token-eq '${QUALIFICATION_SANDBOX_ROOT}\faster-whisper-runs\phase0-fw-r3a-retry-20260801-080000\state\bge-token-phase0-fw-r3a-retry-20260801-080000.dpapi');token_path=$token}
  Write-Output ('R3A_BGE_SELFTEST='+($result|ConvertTo-Json -Compress));if(-not $result.passed){exit 99};exit 0
}
$run=Resolve-Run $RunRoot
$runId=Split-Path -Leaf $run
$tokenPath=Join-Path $run ('state\bge-token-'+$runId+'.dpapi')
$resultPath=Join-Path $run 'evidence\bge-auth-probe.json'
$identityPath=Join-Path $run 'state\run-identity.json'
$configPath=Join-Path $run 'config\r3a-config.json'
$manifestPath=Join-Path $run 'state\helper-manifest.json'
foreach($path in @($identityPath,$configPath,$manifestPath)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "missing identity file: $path"}}
$identity=Get-Content -LiteralPath $identityPath -Raw|ConvertFrom-Json
if([string]$identity.run_root-ne $run){throw 'run identity path mismatch'}
if((Get-Sha256 $configPath)-ne [string]$identity.config_sha256){throw 'config identity drift'}
if((Get-Sha256 $manifestPath)-ne [string]$identity.helper_manifest_sha256){throw 'helper manifest identity drift'}
$selfEntry=@($identity.helpers|Where-Object{$_.name-eq 'run-bge-auth-probe.ps1'})|Select-Object -First 1
if($null-eq $selfEntry){throw 'BGE helper missing from run identity'}
if((Get-Sha256 $PSCommandPath)-ne [string]$selfEntry.sha256){throw 'BGE helper identity drift'}
$config=Get-Content -LiteralPath $configPath -Raw|ConvertFrom-Json
$start=[DateTimeOffset]::Parse([string]$config.maintenance_window.start)
$end=[DateTimeOffset]::Parse([string]$config.maintenance_window.end)
$now=[DateTimeOffset]::Now
if($start.Offset.TotalHours-ne 8-or $end.Offset.TotalHours-ne 8){throw 'maintenance window is not +08:00'}
if($now-lt $start-or $now-ge $end){throw "outside maintenance window: $($now.ToString('o'))"}
$bgeBase=[string]$config.bge.base_url
if($AllowedBgeUrls-notcontains $bgeBase){throw "unapproved BGE URL: $bgeBase"}
if(Test-Path -LiteralPath $tokenPath){throw "exact token path already exists: $tokenPath"}
$created=[DateTimeOffset]::Now;$expires=$created.AddMinutes(15)
$probe=[ordered]@{
  schema_version='faster-whisper-r3a-retry-bge-auth-probe/1';run_id=$runId;started_at=$created.ToString('o')
  token_file=[ordered]@{path=$tokenPath;created_at=$created.ToString('o');expires_at=$expires.ToString('o');content_recorded=$false;exists_before=$false;exists_after=$null}
  bge_base_url=$bgeBase;embedding=$null;rerank=$null;health_after=$null;success=$false;error=$null
}
$secureInput=$null;$secureRoundTrip=$null;$plainToken=$null;$bstr=[IntPtr]::Zero
try{
  Write-Host '请输入 GPU_SERVICE_TOKEN（输入不会回显，也不会写入日志）：'
  $secureInput=Read-Host -AsSecureString
  $cipher=ConvertFrom-SecureString -SecureString $secureInput
  if(-not $cipher){throw 'empty DPAPI ciphertext'}
  Set-Content -LiteralPath $tokenPath -Value $cipher -Encoding ASCII -NoNewline
  if(-not(Test-Path -LiteralPath $tokenPath)){throw 'DPAPI token file was not created'}
  if([DateTimeOffset]::Now-ge $expires){throw 'DPAPI token expired before probe'}
  $secureRoundTrip=Get-Content -LiteralPath $tokenPath -Raw|ConvertTo-SecureString
  $bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureRoundTrip)
  $plainToken=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  if([string]::IsNullOrWhiteSpace($plainToken)){throw 'decrypted token is empty'}
  $headers=@{Authorization='Bearer '+$plainToken;Accept='application/json'}
  $embedBody=@{texts=@('R3-A retry 本地鉴权探针');normalize=$true}|ConvertTo-Json -Compress
  $sw=[Diagnostics.Stopwatch]::StartNew()
  $embedResponse=Invoke-WebRequest -UseBasicParsing -Uri ($bgeBase+'/v1/embeddings')-Method Post -Headers $headers -ContentType 'application/json' -Body $embedBody -TimeoutSec 60
  $sw.Stop();$embedJson=$embedResponse.Content|ConvertFrom-Json;$first=@($embedJson.embeddings)[0]
  $probe.embedding=[ordered]@{http_status=[int]$embedResponse.StatusCode;latency_ms=[int]$sw.ElapsedMilliseconds;count=@($embedJson.embeddings).Count;dense_dimension=@($first.dense).Count;sparse_count=@($first.sparse_indices).Count;response_body_recorded=$false}
  $rerankBody=@{query='建筑信息模型';passages=@('建筑信息模型用于项目协同。');use_header=$false}|ConvertTo-Json -Compress
  $sw=[Diagnostics.Stopwatch]::StartNew()
  $rerankResponse=Invoke-WebRequest -UseBasicParsing -Uri ($bgeBase+'/v1/rerank')-Method Post -Headers $headers -ContentType 'application/json' -Body $rerankBody -TimeoutSec 60
  $sw.Stop();$rerankJson=$rerankResponse.Content|ConvertFrom-Json
  $probe.rerank=[ordered]@{http_status=[int]$rerankResponse.StatusCode;latency_ms=[int]$sw.ElapsedMilliseconds;score_count=@($rerankJson.scores).Count;response_body_recorded=$false}
  $health=Invoke-RestMethod -Uri ($bgeBase+'/health')-Method Get -TimeoutSec 15
  $probe.health_after=[ordered]@{status=$health.status;model_loaded=[bool]$health.model_loaded}
  $probe.success=($probe.embedding.http_status-eq 200-and $probe.embedding.count-eq 1-and $probe.embedding.dense_dimension-eq 1024-and $probe.rerank.http_status-eq 200-and $probe.rerank.score_count-eq 1-and $health.status-eq 'ok'-and [bool]$health.model_loaded)
}catch{$probe.error=[ordered]@{type=$_.Exception.GetType().FullName;message=$_.Exception.Message}}
finally{
  $plainToken=$null
  if($bstr-ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr);$bstr=[IntPtr]::Zero}
  $secureRoundTrip=$null;$secureInput=$null
  if(Test-Path -LiteralPath $tokenPath){Remove-Item -LiteralPath $tokenPath -Force}
  $probe.token_file.exists_after=Test-Path -LiteralPath $tokenPath
  $probe.finished_at=[DateTimeOffset]::Now.ToString('o')
  Write-JsonFile $resultPath $probe
}
Write-Host ('鉴权探针结果：'+$resultPath)
Write-Host ('DPAPI 临时令牌已不存在：'+(-not(Test-Path -LiteralPath $tokenPath)))
if(-not $probe.success-or $probe.token_file.exists_after){exit 21};exit 0