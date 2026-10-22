param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonExe scripts/fetch_models.py --verify-only
if ($LASTEXITCODE -ne 0) { throw 'Run scripts/fetch_models.py before building.' }
if (!$SkipTests) {
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
}
& $pythonExe -m PyInstaller --noconfirm VisionGuard.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
Write-Host 'Build ready: dist\VisionGuardAI\VisionGuardAI.exe'
