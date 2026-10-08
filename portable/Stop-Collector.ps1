$ErrorActionPreference='Stop'
$collectorRoot=$PSScriptRoot;$collectorState=Join-Path $collectorRoot 'user-data'
$collectorIdentity=Join-Path $collectorState 'services.json'
if(-not(Test-Path -LiteralPath $collectorIdentity)){Write-Output 'No saved service identity.';exit 0}
$collectorInfo=Get-Content -LiteralPath $collectorIdentity -Raw | ConvertFrom-Json
$collectorApp=Join-Path $collectorRoot 'app/run_companion.py';$collectorBridge=Join-Path $collectorRoot 'app/bridge.mjs'
$collectorWeb=Get-CimInstance Win32_Process -Filter "ProcessId=$($collectorInfo.web_pid)"
$collectorWorker=Get-CimInstance Win32_Process -Filter "ProcessId=$($collectorInfo.bridge_pid)"
if(-not $collectorWeb -and -not $collectorWorker){Write-Output 'Already stopped.';exit 0}
if(-not $collectorWeb -or -not $collectorWorker -or -not $collectorWeb.CommandLine.Contains($collectorApp) -or -not $collectorWorker.CommandLine.Contains($collectorBridge) -or $collectorWorker.ParentProcessId -ne $collectorWeb.ProcessId){throw 'Saved process identity mismatch. Nothing stopped.'}
$collectorBase="http://127.0.0.1:$($collectorInfo.port)"
$collectorToken=[regex]::Match((Invoke-WebRequest "$collectorBase/pair" -UseBasicParsing -TimeoutSec 5).Content,'<pre>([^<]+)</pre>').Groups[1].Value
$collectorHeaders=@{Origin=$collectorBase;'X-JDIA-Session'=$collectorToken}
$collectorRuns=(Invoke-RestMethod "$collectorBase/api/runs" -Headers $collectorHeaders -TimeoutSec 5).runs
if(@($collectorRuns | Where-Object status -in @('running','paused')).Count){
 Invoke-RestMethod "$collectorBase/api/collect/stop" -Method Post -Headers $collectorHeaders -TimeoutSec 5 | Out-Null
 for($i=0;$i -lt 20;$i++){Start-Sleep -Milliseconds 500;$collectorRuns=(Invoke-RestMethod "$collectorBase/api/runs" -Headers $collectorHeaders -TimeoutSec 5).runs;if(-not @($collectorRuns | Where-Object status -in @('running','paused')).Count){break}}
 if(@($collectorRuns | Where-Object status -in @('running','paused')).Count){throw 'Collection still stopping. Use Web Stop and retry. Nothing terminated.'}
}
Stop-Process -Id $collectorWorker.ProcessId
Stop-Process -Id $collectorWeb.ProcessId
Write-Output 'Companion stopped. Database and browser remain.'
