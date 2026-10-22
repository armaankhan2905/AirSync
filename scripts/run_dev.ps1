param([switch]$Simulation, [switch]$Background)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonExe)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
$runArgs = @('main.py')
if ($Simulation) { $runArgs += '--simulation' }
if ($Background) { $runArgs += '--background' }
& $pythonExe @runArgs
exit $LASTEXITCODE
