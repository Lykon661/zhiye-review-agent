param([switch]$OpenBrowser, [switch]$RestartBackend)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) { throw 'Run npm ci in frontend first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'model-runtime\node_modules\@earendil-works\pi-ai\package.json'))) { throw 'Run npm.cmd install --prefix model-runtime first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
}
$env:PYTHONUTF8 = '1'
$logDir = Join-Path $projectRoot '.cache\run'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
if ($RestartBackend) {
    # Query all listeners so an unused port is an empty result. Permission
    # failures must stop startup instead of silently leaving old code running.
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8000)
    foreach ($listener in $listeners) {
        $backendTaskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction Stop
        $backendTaskParent = Get-CimInstance Win32_Process -Filter "ProcessId = $($backendTaskProcess.ParentProcessId)" -ErrorAction Stop
        $projectPython = ($backendTaskProcess.ExecutablePath -eq $pythonPath) -or ($backendTaskParent.ExecutablePath -eq $pythonPath)
        $isReviewServer = $backendTaskProcess.CommandLine -match '-m\s+uvicorn\s+app\.main:app\b'
        if (-not ($projectPython -and $isReviewServer)) {
            throw 'Port 8000 is owned by another application; stop it yourself before restarting this project.'
        }
        Stop-Process -Id $backendTaskProcess.ProcessId -ErrorAction Stop
        Write-Host "Stopped project backend: $($backendTaskProcess.ProcessId)"
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remainingListeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8000)
        if (-not $remainingListeners) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($remainingListeners) { throw 'Port 8000 was not released; backend restart failed.' }
}
$backendOnline = $false
try { $response = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2; $backendOnline = $response.status -eq 'ok' } catch {}
if (-not $backendOnline) {
    $startupStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $backendOut = Join-Path $logDir "backend-$startupStamp.out.log"
    $backendErr = Join-Path $logDir "backend-$startupStamp.err.log"
    $backend = Start-Process -FilePath $pythonPath -ArgumentList @('-u', '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
    Write-Host "Backend process: $($backend.Id)"
    $backendReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $backend.Refresh()
        if ($backend.HasExited) { throw "Backend exited during startup. Read $backendErr" }
        try {
            $response = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            $backendReady = $response.status -eq 'ok'
        } catch {}
        if ($backendReady) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $backendReady) { throw "Backend did not become ready. Read $backendErr" }
}
$frontendOnline = $false
try { $response = Invoke-WebRequest 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 2; $frontendOnline = $response.StatusCode -eq 200 } catch {}
if (-not $frontendOnline) {
    $frontend = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d /c npm.cmd run dev -- --port 5173 --strictPort' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'frontend.out.log') -RedirectStandardError (Join-Path $logDir 'frontend.err.log')
    Write-Host "Frontend process: $($frontend.Id)"
}
Write-Host 'Review UI: http://127.0.0.1:5173/'
Write-Host 'API docs:  http://127.0.0.1:8000/docs'
Write-Host "Logs: $logDir"
if ($OpenBrowser) { Start-Process 'http://127.0.0.1:5173/' }
