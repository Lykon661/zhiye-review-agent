$ErrorActionPreference = 'Stop'
$bundleRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $bundleRoot
$userRoot = Join-Path $env:LOCALAPPDATA 'Zhiye'
$env:DATA_DIR = Join-Path $userRoot 'data'
$env:ZHIYE_LOG_DIR = Join-Path $userRoot 'logs'
$env:EMBEDDING_LOCAL_FILES_ONLY = 'true'
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:TIKA_JAVA = Join-Path $bundleRoot '.runtime\java\bin\java.exe'
$env:TIKA_APP_JAR = Join-Path $bundleRoot '.runtime\tika\tika-app-3.3.2.jar'
$env:PATH = (Join-Path $bundleRoot '.runtime\node') + ';' + $env:PATH
New-Item -ItemType Directory -Path $env:DATA_DIR,$env:ZHIYE_LOG_DIR -Force | Out-Null

# Models come in a separate ZIP (MODELS_ID in build-portable.py). The first launch copies
# them into the user folder, so later releases with the same models need no second download.
$modelsId = '1'
$modelsName = "Zhiye-models-$modelsId"
$modelsRoot = Join-Path $userRoot "models\$modelsId"
function Test-Models([string]$directory) {
    $manifest = Join-Path $directory 'models.json'
    if (-not (Test-Path -LiteralPath $manifest)) { return $false }
    $files = (Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json).files
    foreach ($file in $files.PSObject.Properties) {
        $path = Join-Path $directory $file.Name
        if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -ne $file.Value) { return $false }
    }
    return $true
}
if (-not (Test-Models $modelsRoot)) {
    $parent = Split-Path -Parent $bundleRoot
    # Explorer's "Extract All" adds an outer folder named after the ZIP, so look one level deeper too.
    $candidates = @(
        (Join-Path $bundleRoot $modelsName), (Join-Path $bundleRoot "$modelsName\$modelsName"),
        (Join-Path $parent $modelsName), (Join-Path $parent "$modelsName\$modelsName")
    )
    $package = $candidates | Where-Object { Test-Models $_ } | Select-Object -First 1
    if (-not $package) {
        Write-Host "Model package $modelsName was not found or is incomplete."
        Write-Host "Download $modelsName.zip from the same GitHub release and extract it into:"
        Write-Host "  $parent"
        Write-Host "Then start Zhiye again. The package is only needed on the first launch."
        exit 1
    }
    Write-Host "Installing models from $package (first launch only)..."
    $partial = "$modelsRoot.partial"
    Remove-Item -LiteralPath $partial,$modelsRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Split-Path -Parent $modelsRoot) -Force | Out-Null
    Copy-Item -LiteralPath $package -Destination $partial -Recurse
    Rename-Item -LiteralPath $partial -NewName (Split-Path -Leaf $modelsRoot)
}
$env:HF_HOME = Join-Path $modelsRoot 'huggingface'
$formulaTarget = Join-Path $env:DATA_DIR 'ocr-models\formula'
New-Item -ItemType Directory -Path $formulaTarget -Force | Out-Null
foreach ($name in @('image_resizer.onnx','encoder.onnx','decoder.onnx','tokenizer.json')) {
    $target = Join-Path $formulaTarget $name
    if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -eq 0) {
        Copy-Item -LiteralPath (Join-Path $modelsRoot "formula\$name") -Destination $target
    }
}
& (Join-Path $bundleRoot '.runtime\python\python.exe') -s -u (Join-Path $PSScriptRoot 'portable_server.py')
exit $LASTEXITCODE
