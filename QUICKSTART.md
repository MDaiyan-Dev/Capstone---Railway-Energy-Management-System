# QUICKSTART.md

## Purpose
These commands must work on a fresh machine after cloning the repo.
Update this file whenever setup steps or entry points change.

## Setup
1. Clone the repo
2. Create a Python environment
3. Install dependencies

### Python environment
Use whichever is standard for this repo.

Example using venv:
1. Create venv
2. Activate venv
3. Install requirements

TODO confirm whether this repo uses requirements.txt or pyproject.toml.

## Run the Simulator
The simulator must be runnable from the command line and must generate:
- timeline CSV
- KPI JSON
- resolved config JSON

Example with overrides:
`python simulator/src/run_sim.py --mode hess --run-id MR90_hess_tuned --config config/mr90_default.json --overrides docs/overrides_examples/mr90_hess_tuned.json`
Resolved run config is written to `simulator/outputs/config_<run_id>.json`.

### Baseline run
TODO insert exact command once simulator entry point is confirmed.

Expected result:
- A new timeline CSV is written to the outputs folder
- A new KPI JSON is written to the outputs folder

### HESS run
TODO insert exact command once config and flags are confirmed.

Expected result:
- A new timeline CSV is written to the outputs folder
- A new KPI JSON is written to the outputs folder

## Verification Checklist
After any simulator change, run:
- baseline
- HESS

Then confirm:
- CSV headers match previous run
- KPI keys match previous run
- totals are not obviously broken

## Run the API if present
TODO insert exact command to start the API after confirming the entry file.

## Run the Dashboard
TODO insert exact command or instructions to open the dashboard after confirming where it lives.

## Semester 2 Validation
### Physical CSV logging requirements
Minimum columns:
- t_s
- v_mps or rpm
- V_motor_v
- I_motor_a

Derived:
- P_w equals V times I
- E_kwh cumulative by integrating power over time

### Validation script
TODO insert exact command once validation script exists.

Expected result:
- prints error metrics
- saves at least one plot
