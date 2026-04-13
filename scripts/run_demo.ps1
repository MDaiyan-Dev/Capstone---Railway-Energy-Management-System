param(
    [int]$Port = 5000
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
}
else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Run .\scripts\setup_env.ps1 first."
    }
    $pythonExe = $pythonCommand.Source
}

$url = "http://127.0.0.1:$Port/control"
Write-Host "[run_demo] Starting Flask API and UI at $url"
Write-Host "[run_demo] Using Python interpreter: $pythonExe"
Write-Host "[run_demo] Press Ctrl+C in this terminal to stop."

Start-Process $url | Out-Null
& $pythonExe data_layer/api.py
