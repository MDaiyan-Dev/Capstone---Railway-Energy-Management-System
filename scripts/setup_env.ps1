param(
    [string]$PythonExe = "",
    [string]$VenvName = ".venv",
    [switch]$ForceRecreate,
    [switch]$SkipPipUpgrade
)

$ErrorActionPreference = "Stop"

function Resolve-PythonLauncher {
    param([string]$PreferredPython)

    $candidates = @()

    if ($PreferredPython) {
        $candidates += [pscustomobject]@{
            Label = "explicit PythonExe"
            Command = $PreferredPython
            PrefixArgs = @()
        }
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        $candidates += [pscustomobject]@{
            Label = "py -3"
            Command = $pyCommand.Source
            PrefixArgs = @("-3")
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += [pscustomobject]@{
            Label = "python"
            Command = $pythonCommand.Source
            PrefixArgs = @()
        }
    }

    $python3Command = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3Command) {
        $candidates += [pscustomobject]@{
            Label = "python3"
            Command = $python3Command.Source
            PrefixArgs = @()
        }
    }

    foreach ($candidate in $candidates) {
        try {
            & $candidate.Command @($candidate.PrefixArgs + @("--version")) *> $null
            return $candidate
        }
        catch {
        }
    }

    throw "No usable Python interpreter was found. Install Python 3 and rerun this script."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvDir = Join-Path $repoRoot $VenvName
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirementsPath = Join-Path $repoRoot "simulator\requirements.txt"

if (-not (Test-Path $requirementsPath)) {
    throw "Requirements file not found at $requirementsPath"
}

if ($ForceRecreate -and (Test-Path $venvDir)) {
    Write-Host "[setup_env] Removing existing virtual environment at $venvDir"
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

$launcher = Resolve-PythonLauncher -PreferredPython $PythonExe
Write-Host "[setup_env] Repo root: $repoRoot"
Write-Host "[setup_env] Using Python launcher: $($launcher.Label) -> $($launcher.Command)"

if (-not (Test-Path $venvPython)) {
    Write-Host "[setup_env] Creating virtual environment at $venvDir"
    & $launcher.Command @($launcher.PrefixArgs + @("-m", "venv", $venvDir))
}
else {
    Write-Host "[setup_env] Reusing existing virtual environment at $venvDir"
}

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment creation did not produce $venvPython"
}

Write-Host "[setup_env] Virtual environment Python: $venvPython"

if (-not $SkipPipUpgrade) {
    Write-Host "[setup_env] Upgrading pip, setuptools, and wheel"
    & $venvPython -m pip install --upgrade pip setuptools wheel
}

Write-Host "[setup_env] Installing repo requirements from $requirementsPath"
& $venvPython -m pip install -r $requirementsPath

Write-Host "[setup_env] Verifying core imports"
& $venvPython -c "import flask, numpy, pandas, matplotlib; print('Environment verification passed.')"

Write-Host ""
Write-Host "[setup_env] Environment is ready."
Write-Host "[setup_env] Activate with: $venvDir\Scripts\Activate.ps1"
Write-Host "[setup_env] Run expo preparation with: $venvPython scripts/create_expo_runs.py"
Write-Host "[setup_env] Run the demo UI with: .\scripts\run_demo.ps1"
