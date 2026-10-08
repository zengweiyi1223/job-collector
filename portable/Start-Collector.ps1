param([ValidateRange(1024,65535)][int]$Port=8688,[ValidateRange(1024,65535)][int]$BridgePort=3458,[ValidateRange(1024,65535)][int]$ChromePort=9422,[switch]$NoOpen,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$collectorRoot=$PSScriptRoot
$collectorState=Join-Path $collectorRoot 'user-data'
$collectorPython=Join-Path $collectorRoot 'runtime/python/python.exe'
$collectorNode=Join-Path $collectorRoot 'runtime/node/node.exe'
$collectorApp=Join-Path $collectorRoot 'app/run_companion.py'
$collectorBridge=Join-Path $collectorRoot 'app/bridge.mjs'
$collectorProfile=Join-Path $collectorState 'chrome'
if(@($Port,$BridgePort,$ChromePort | Select-Object -Unique).Count -ne 3){throw 'Ports must be distinct'}
function Collector-Owner([int]$Number){
 $links=@(Get-NetTCPConnection -LocalPort $Number -State Listen -ErrorAction SilentlyContinue)
 if(@($links | Where-Object LocalAddress -notin @('127.0.0.1','::1')).Count){throw "Port $Number is not loopback-only"}
 $ids=@($links | Select-Object -ExpandProperty OwningProcess -Unique)
 if($ids.Count -gt 1){throw "Ambiguous port $Number"}
 if($ids.Count){return Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])"}
 return $null
}
foreach($asset in @($collectorPython,$collectorNode,$collectorApp,$collectorBridge)){if(-not(Test-Path -LiteralPath $asset)){throw "Missing package file: $asset"}}
$collectorWeb=Collector-Owner $Port;$collectorWorker=Collector-Owner $BridgePort;$collectorBrowser=Collector-Owner $ChromePort
if($collectorWeb -and ($collectorWeb.ExecutablePath -ne $collectorPython -or -not $collectorWeb.CommandLine.Contains($collectorApp))){throw "Port $Port belongs to another application. Nothing stopped."}
if($collectorWorker -and ($collectorWorker.ExecutablePath -ne $collectorNode -or -not $collectorWorker.CommandLine.Contains($collectorBridge))){throw "Port $BridgePort belongs to another application. Nothing stopped."}
if([bool]$collectorWeb -ne [bool]$collectorWorker){throw 'Incomplete existing companion. Inspect user-data/logs; no process terminated.'}
if($collectorBrowser -and (-not $collectorBrowser.CommandLine.Contains($collectorProfile) -or -not $collectorBrowser.CommandLine.Contains("--remote-debugging-port=$ChromePort"))){throw "Port $ChromePort is not this package browser. Nothing stopped."}
$collectorPublicOrigin=$null
$collectorSettings=Join-Path $collectorRoot 'settings.json'
if(Test-Path -LiteralPath $collectorSettings){$collectorPublicOrigin=(Get-Content -LiteralPath $collectorSettings -Raw | ConvertFrom-Json).public_origin}
New-Item -ItemType Directory -Path (Join-Path $collectorState 'logs') -Force | Out-Null
$collectorChrome=$null
if(-not $NoBrowser -or -not $NoOpen){
 $collectorChrome=@((Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'),(Join-Path ${env:ProgramFiles(x86)} 'Google/Chrome/Application/chrome.exe'),(Join-Path $env:LOCALAPPDATA 'Google/Chrome/Application/chrome.exe')) | Where-Object {Test-Path -LiteralPath $_} | Select-Object -First 1
 if(-not $collectorChrome){throw 'Google Chrome is required. No automatic installation.'}
}
if(-not $collectorBrowser -and -not $NoBrowser){
 # Visible browser is necessary for the user's normal manual login/verification.
 Start-Process -FilePath $collectorChrome -ArgumentList @("--remote-debugging-port=$ChromePort",'--remote-debugging-address=127.0.0.1',"--user-data-dir=`"$collectorProfile`"",'--no-first-run','https://www.zhipin.com/web/geek/jobs') | Out-Null
}
if(-not $collectorWeb){
 $collectorStamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
 $collectorArgs=@('-I','-B',"`"$collectorApp`"",'--db',"`"$(Join-Path $collectorState 'jobs.db')`"",'--node',"`"$collectorNode`"",'--port',"$Port",'--bridge-port',"$BridgePort",'--chrome-port',"$ChromePort")
 if($collectorPublicOrigin){$collectorArgs+=@('--public-origin',"`"$collectorPublicOrigin`"")}
 Start-Process -FilePath $collectorPython -ArgumentList $collectorArgs -WorkingDirectory $collectorRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $collectorState "logs/service-$collectorStamp.out.log") -RedirectStandardError (Join-Path $collectorState "logs/service-$collectorStamp.err.log") | Out-Null
}
$collectorHealth=$null
for($i=0;$i -lt 20;$i++){
 try{$collectorHealth=Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -Headers @{Origin="http://127.0.0.1:$Port"} -TimeoutSec 5}catch{}
 if($collectorHealth.service -eq 'jdia-collector' -and $collectorHealth.collector_ready){break}
 Start-Sleep -Milliseconds 300
}
if(-not $collectorHealth -or $collectorHealth.service -ne 'jdia-collector'){throw 'Startup incomplete. Inspect user-data/logs. No collection started.'}
$collectorWeb=Collector-Owner $Port;$collectorWorker=Collector-Owner $BridgePort
@{web_pid=$collectorWeb.ProcessId;bridge_pid=$collectorWorker.ProcessId;port=$Port;bridge_port=$BridgePort;chrome_port=$ChromePort;collection_started=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $collectorState 'services.json') -Encoding UTF8
if(-not $NoOpen){Start-Process -FilePath $collectorChrome -ArgumentList @("--user-data-dir=`"$collectorProfile`"","http://127.0.0.1:$Port/pair","http://127.0.0.1:$Port/") | Out-Null}
$collectorHealth | ConvertTo-Json
