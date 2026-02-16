# REPO_MAP.md

## Goal
This file describes the actual structure of the REMS repo using paths confirmed in code.

## Confirmed Core Entries

### Module 3 Simulator
- Simulator entry file: `simulator/src/run_sim.py`
- Config path used by default CLI flag: `config/mr90_default.json`
- Config fallback when no config path is provided: built in `default_config` in `simulator/src/run_sim.py`
- Output directory default: `simulator/outputs`
- Output naming written by simulator:
- `timeline_<run_id>.csv`
- `kpi_<run_id>.json`
- Confirmed examples in repo:
- `simulator/outputs/timeline_MR90_baseline.csv`
- `simulator/outputs/timeline_MR90_hess.csv`
- `simulator/outputs/kpi_MR90_baseline.json`
- `simulator/outputs/kpi_MR90_hess.json`

### API Layer
- API entry file: `data_layer/api.py`
- Framework: Flask
- API base route prefix in code: `/api`
- Confirmed endpoints:
- `GET /api/health`
- `GET /api/kpi/current`
- `GET /api/replay/meta`
- `GET /api/runs/<run_id>`
- `GET /api/snapshot`
- API source for run data:
- `simulator/outputs/timeline_<run_id>.csv`
- `simulator/outputs/kpi_<run_id>.json`

### Dashboard
- Dashboard entry file: `dashboard/index.html`
- Main dashboard script: `dashboard/js/app.js`
- Main stylesheet: `dashboard/css/styles.css`
- Demo payload files used by dashboard:
- `dashboard/data/live_demo.json`
- `dashboard/data/run_demo.json`

## Config Files
- Confirmed config file present in repo:
- `config/mr90_default.json`

## Output Contract
- Timeline CSV per run from simulator:
- `simulator/outputs/timeline_<run_id>.csv`
- KPI JSON per run from simulator:
- `simulator/outputs/kpi_<run_id>.json`
- Downstream API expects these exact patterns when loading a run id.

## Repo Root Short Tree
```text
.
|-- AGENTS.md
|-- QUICKSTART.md
|-- REPO_MAP.md
|-- README.md
|-- config/
|   `-- mr90_default.json
|-- simulator/
|   |-- requirements.txt
|   |-- outputs/
|   `-- src/
|       |-- run_sim.py
|       `-- emit_jsonl_from_timeline.py
|-- data_layer/
|   `-- api.py
|-- dashboard/
|   |-- index.html
|   |-- css/styles.css
|   |-- js/app.js
|   `-- data/
|       |-- live_demo.json
|       `-- run_demo.json
|-- ems/
|   `-- src/
|-- bus/
|-- common/
|-- docs/
|   `-- CONTEXT_MODULE3.md
`-- scripts/
```
