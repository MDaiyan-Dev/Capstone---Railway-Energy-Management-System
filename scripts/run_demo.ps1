param(
    [int]$Port = 5000
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$url = "http://127.0.0.1:$Port/control"
Write-Host "[run_demo] Starting Flask API and UI at $url"
Write-Host "[run_demo] Press Ctrl+C in this terminal to stop."

Start-Process $url | Out-Null
python data_layer/api.py
