# QUICKSTART.md

## Purpose
These commands should work on a fresh clone and reflect the current Semester 2 workflow.

## Setup
Create and activate a Python environment, then install the repo requirements.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r simulator\requirements.txt
```

## Run The Simulator
The simulator writes a timeline CSV, KPI JSON, and resolved config JSON for each run.

### Baseline run
```powershell
python simulator\src\run_sim.py --mode baseline --run-id MR90_baseline --config config\mr90_default.json --out-dir simulator\outputs
```

### HESS run
```powershell
python simulator\src\run_sim.py --mode hess --run-id MR90_hess --config config\mr90_default.json --out-dir simulator\outputs
```

### HESS run with overrides
```powershell
python simulator\src\run_sim.py --mode hess --run-id MR90_hess_tuned --config config\mr90_default.json --overrides docs\overrides_examples\mr90_hess_tuned.json --out-dir simulator\outputs
```

Expected artifacts:
- `simulator\outputs\timeline_<run_id>.csv`
- `simulator\outputs\kpi_<run_id>.json`
- `simulator\outputs\config_<run_id>.json`

## Verification Checklist
After any simulator change, run both:
- baseline
- hess

Then confirm:
- CSV headers are unchanged unless the change was intentional
- KPI keys are unchanged unless the change was intentional
- totals look reasonable and not obviously broken

## Run The API
```powershell
python data_layer\api.py
```

Expected URL:
- `http://127.0.0.1:5000`

## Open The Control Panel
Open:
- `http://127.0.0.1:5000/control`

## Open The Dashboard
Open:
- `http://127.0.0.1:5000/dashboard/`

Use:
- `Data API base = http://127.0.0.1:5000/api`
- `Simulator API base = http://127.0.0.1:5000/api`

## One Command Demo Start
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

## Optional Exports
```powershell
python scripts\export_traction_load_light.py --run-ids MR90_baseline MR90_hess
python scripts\export_load_profile.py --run-id MR90_baseline
```

## Semester 2 Validation
Physical CSV logs should include:
- `t_s`
- `v_mps` or `rpm`
- `V_motor_v`
- `I_motor_a`

Derived checks:
- `P_w = V * I`
- `E_kwh` should be cumulative from integrated power

Acceptance and evidence runner:
```powershell
python scripts\r4_run_acceptance_tests.py
```

Expected result:
- regenerates evidence in `test_outputs\R4`
- writes `section3_test_results.csv`
- writes `section3_test_results.md`
