param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$javaCommand = Get-Command java -ErrorAction SilentlyContinue
if (-not $javaCommand -and $env:JAVA_HOME) {
    $javaPath = Join-Path $env:JAVA_HOME 'bin\java.exe'
    if (Test-Path -LiteralPath $javaPath) { $javaCommand = Get-Item -LiteralPath $javaPath }
}
if (-not $javaCommand) { throw 'Install JDK 11 or newer (or set JAVA_HOME) before installing Tika.' }
$tikaVersion = '3.3.2'
$runtimeDir = Join-Path $projectRoot '.runtime\tika'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$jarName = "tika-app-$tikaVersion.jar"
$jarPath = Join-Path $runtimeDir $jarName
if (-not (Test-Path -LiteralPath $jarPath)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $downloadPath = Join-Path $runtimeDir "$jarName.download"
    $releaseUrl = "https://downloads.apache.org/tika/$tikaVersion/$jarName"
    try {
        Write-Host "Downloading Apache Tika $tikaVersion..."
        try {
            Invoke-WebRequest -Uri $releaseUrl -OutFile $downloadPath -UseBasicParsing
        } catch {
            $releaseUrl = "https://archive.apache.org/dist/tika/$tikaVersion/$jarName"
            Invoke-WebRequest -Uri $releaseUrl -OutFile $downloadPath -UseBasicParsing
        }
        # Verify publisher integrity during installation, without executing Java or parsing files.
        $checksumText = (Invoke-WebRequest -Uri "$releaseUrl.sha512" -UseBasicParsing).Content
        $checksumMatch = [regex]::Match([string]$checksumText, '(?i)\b[0-9a-f]{128}\b')
        if (-not $checksumMatch.Success) { throw 'Publisher SHA-512 checksum was not readable.' }
        $downloadHash = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA512).Hash
        if ($downloadHash -ne $checksumMatch.Value) { throw 'Tika download checksum mismatch.' }
        Move-Item -LiteralPath $downloadPath -Destination $jarPath
    } finally {
        if (Test-Path -LiteralPath $downloadPath) { Remove-Item -LiteralPath $downloadPath -Force }
    }
}
Write-Host "Tika installed at $jarPath. It starts only when parsing DOC/PPT/RTF."
