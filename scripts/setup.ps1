param([switch]$SkipFrontend, [switch]$SkipTika)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:PYTHONUTF8 = '1'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw 'Install Node.js 22.19 or newer before running setup.'
}
if (-not $SkipTika) {
    $setupJava = Get-Command java -ErrorAction SilentlyContinue
    if (-not $setupJava -and $env:JAVA_HOME) {
        $setupJavaPath = Join-Path $env:JAVA_HOME 'bin\java.exe'
        if (Test-Path -LiteralPath $setupJavaPath) { $setupJava = Get-Item -LiteralPath $setupJavaPath }
    }
    if (-not $setupJava) {
        throw 'Install JDK 11 or newer (or set JAVA_HOME), or use -SkipTika if DOC/PPT/RTF support is not needed.'
    }
}
uv sync --python 3.11 --frozen --group dev
if ($LASTEXITCODE -ne 0) { throw 'Python dependency setup failed.' }
uv sync --project formula-worker --python 3.11 --frozen
if ($LASTEXITCODE -ne 0) { throw 'Formula worker dependency setup failed.' }
if (-not $SkipTika) {
    & (Join-Path $PSScriptRoot 'setup-tika.ps1')
} else {
    Write-Host 'Skipped Tika setup. DOC/PPT/RTF parsing requires JDK 11+ and scripts\setup-tika.ps1.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
}
Push-Location -LiteralPath (Join-Path $projectRoot 'model-runtime')
try {
    if (Test-Path -LiteralPath 'package-lock.json') { npm.cmd ci --ignore-scripts } else { npm.cmd install --ignore-scripts }
    if ($LASTEXITCODE -ne 0) { throw 'pi-ai model runtime dependency setup failed.' }
} finally { Pop-Location }
if (-not $SkipFrontend) {
    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency setup failed.' }
    } finally { Pop-Location }
}
Write-Host 'Setup complete. Run .\run.cmd, then configure your model in the web UI.'
