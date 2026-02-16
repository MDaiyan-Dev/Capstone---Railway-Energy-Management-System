# RUN_PROJECT.md

## Scope
This guide runs the current repo end to end from a clean clone on Windows PowerShell.
It covers simulator outputs, optional EMS artifacts, API, control panel, and dashboard.

## 1. Open Terminal In Repo Root
```powershell
cd "c:\Users\amiru\OneDrive\Desktop\Capstone\Capstone---Railway-Energy-Management-System"
```

## 2. Create And Activate Python Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 3. Install Python Dependencies
```powershell
pip install -r simulator\requirements.txt
```

## 4. Run Simulator Baseline And HESS
From repo root:

```powershell
python simulator\src\run_sim.py --mode baseline --run-id MR90_baseline --config config\mr90_default.json --out-dir simulator\outputs
python simulator\src\run_sim.py --mode hess --run-id MR90_hess --config config\mr90_default.json --out-dir simulator\outputs
```

Expected outputs:
- `simulator\outputs\timeline_MR90_baseline.csv`
- `simulator\outputs\kpi_MR90_baseline.json`
- `simulator\outputs\timeline_MR90_hess.csv`
- `simulator\outputs\kpi_MR90_hess.json`

## 5. Optional Plot Generation
```powershell
python make_plots.py
```

Expected outputs in `simulator\outputs`:
- `fig1_grid_energy.png`
- `fig2_regen_stacked.png`
- `fig3_grid_power_vs_time.png`
- `fig4_storage_behaviour.png`

## 6. Optional JSONL Telemetry Export For EMS
This converts a timeline CSV to `bus\in` JSONL files.

```powershell
python simulator\src\emit_jsonl_from_timeline.py --timeline simulator\outputs\timeline_MR90_hess.csv
```

Expected outputs:
- `bus\in\telemetry.train.state.v1.jsonl`
- `bus\in\telemetry.energy.sample.v1.jsonl`
- `bus\in\telemetry.event.stop.v1.jsonl`

## 7. Optional EMS Batch Run
```powershell
python ems\src\ems_main.py
```

Expected output:
- `bus\out\ems.command.v1.jsonl`

## 8. One Command Demo Start
With venv active, run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

This starts Flask and opens:
- `http://127.0.0.1:5000/control`

From the control page:
- Run baseline or hess without terminal commands
- Open the dashboard at `/dashboard/`

## 9. Manual API Start (Alternative)
If you do not use the launcher, run:

```powershell
python data_layer\api.py
```

API runs at `http://127.0.0.1:5000`.
Quick health check in another terminal:

```powershell
curl http://127.0.0.1:5000/api/health
```

## 10. Open Dashboard
Open this URL in your browser:
- `http://127.0.0.1:5000/dashboard/`

In dashboard inputs:
- `Data API base` = `http://127.0.0.1:5000/api`
- `Simulator API base` = `http://127.0.0.1:5000/api`
- `Run ID` = `MR90_hess` or `MR90_baseline`

Then use:
- `Fetch Live` for `/api/snapshot`
- `Fetch Run` for `/api/runs/<run_id>`

## 11. Minimal End To End Verification
1. Confirm simulator files exist in `simulator\outputs`.
2. Confirm `GET /api/health` returns ok.
3. Confirm `http://127.0.0.1:5000/control` loads and can run a simulation.
4. Confirm `http://127.0.0.1:5000/dashboard/` loads.
5. In dashboard, `Fetch Live` updates KPI values from `empty`.
6. In dashboard, `Fetch Run` loads timeline slider and map playback.
