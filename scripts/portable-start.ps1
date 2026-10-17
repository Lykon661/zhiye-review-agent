$ErrorActionPreference = 'Stop'
$bundleRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $bundleRoot
$userRoot = Join-Path $env:LOCALAPPDATA 'Zhiye'
$env:DATA_DIR = Join-Path $userRoot 'data'
$env:ZHIYE_LOG_DIR = Join-Path $userRoot 'logs'
$env:HF_HOME = Join-Path $bundleRoot '.runtime\huggingface'
$env:EMBEDDING_LOCAL_FILES_ONLY = 'true'
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:TIKA_JAVA = Join-Path $bundleRoot '.runtime\java\bin\java.exe'
$env:TIKA_APP_JAR = Join-Path $bundleRoot '.runtime\tika\tika-app-3.3.2.jar'
$env:PATH = (Join-Path $bundleRoot '.runtime\node') + ';' + $env:PATH
New-Item -ItemType Directory -Path $env:DATA_DIR,$env:ZHIYE_LOG_DIR -Force | Out-Null
$formulaTarget = Join-Path $env:DATA_DIR 'ocr-models\formula'
New-Item -ItemType Directory -Path $formulaTarget -Force | Out-Null
foreach ($name in @('image_resizer.onnx','encoder.onnx','decoder.onnx','tokenizer.json')) {
    $target = Join-Path $formulaTarget $name
    if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -eq 0) {
        Copy-Item -LiteralPath (Join-Path $bundleRoot ".runtime\formula-models\$name") -Destination $target
    }
}
& (Join-Path $bundleRoot '.runtime\python\python.exe') -s -u (Join-Path $PSScriptRoot 'portable_server.py')
exit $LASTEXITCODE
