# Expo Laptop Setup

## Purpose
Use these steps on a Windows laptop that has not run this project before.

This gets you to:
- a working Python environment
- the canonical EXPO runs generated
- the REMS control panel running at `/control`
- the dashboard running at `/dashboard/`

## What You Need
- Windows with PowerShell
- Python 3 installed and available as `py` or `python`
- Internet access for the first package install
- This repository copied or cloned onto the laptop

## 1. Open PowerShell In The Repo Root
Open PowerShell and go to the project folder.

Example:

```powershell
cd C:\Users\amiru\OneDrive\Desktop\Capstone\Capstone---Railway-Energy-Management-System
```

## 2. Set Up The Python Environment
Run the setup script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1
```

What this does:
- creates `.venv`
- installs the Python packages from `simulator\requirements.txt`
- verifies the core imports

If the script finishes with `Environment is ready.`, continue.

## 3. Generate The Canonical Expo Runs
Run:

```powershell
.\.venv\Scripts\python.exe .\scripts\create_expo_runs.py
```

This refreshes these canonical runs:
- `EXPO_baseline_reference`
- `EXPO_hess_reference`
- `EXPO_high_price`
- `EXPO_constrained_grid`

Expected artifacts for each run:
- `simulator\outputs\timeline_<run_id>.csv`
- `simulator\outputs\kpi_<run_id>.json`
- `simulator\outputs\config_<run_id>.json`
- `simulator\outputs\tem_<run_id>.json`

## 4. Start The App
Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1
```

This starts Flask on:

- `http://127.0.0.1:5000/control`

Leave this PowerShell window open while presenting.

To stop the app later:

```powershell
Ctrl+C
```

## 5. Open The Product UI
In the browser, use:

- Control Panel: `http://127.0.0.1:5000/control`
- Dashboard: `http://127.0.0.1:5000/dashboard/`

## 6. Recommended Expo Workflow
### Option A: Use The Canonical Runs
This is the safest flow for the expo.

1. Open `/control`
2. In `Recent Runs`, choose one of the `EXPO_...` runs
3. Confirm the metadata, EMS summary, and TEM summary appear
4. Click `Open Dashboard`

Recommended order:
1. `EXPO_baseline_reference`
2. `EXPO_hess_reference`
3. `EXPO_high_price`
4. `EXPO_constrained_grid`

### Option B: Create A Fresh Run Live
1. Open `/control`
2. Pick a `Scenario Preset`
3. Click `Run Simulation`
4. Wait for the success banner
5. If needed, click `Run TEM Analysis`
6. Click `Open Dashboard`

## 7. Quick Verification Checklist
Before the expo, confirm:

- `/control` loads
- `/dashboard/` loads
- `Recent Runs` shows the `EXPO_...` runs near the top
- selecting `EXPO_hess_reference` shows EMS summary and TEM summary
- selecting `EXPO_high_price` shows price per kWh as `0.5`
- `Open Dashboard` opens the selected run

## 8. If Something Looks Stale
If the control page or styles look old, hard refresh the browser:

```text
Ctrl+F5
```

## 9. If Python Is Not Found
Install Python 3, then reopen PowerShell and rerun:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1
```

## 10. If You Want The Short Version
Run these three commands in order:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1
.\.venv\Scripts\python.exe .\scripts\create_expo_runs.py
powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1
```

Then open:

- `http://127.0.0.1:5000/control`

## Notes
- No Node or frontend build step is required.
- The default price is `0.25` per kWh.
- The `High Price Period` scenario uses `0.50` per kWh.
- Cost calculations are based on energy drawn from the grid.
