# Capstone---Railway-Energy-Management-System

This is our Capstone Project Repository.
It is used for collaboration and storage of relevant project resources.

# Railway Energy Management System - Capstone 2025-26

**SOFE + ELEE Integrated Project - Ontario Tech University**  
**Modules:** Simulator - EMS - Data Layer - Dashboard

---

## Overview

This repository contains the current Semester 2 implementation of the Railway Energy Management System.
The main delivered workflow is centered on a deterministic railway simulator, a Flask API layer, a browser-based control panel, and a dashboard replay interface.

The current project supports:

* configuration-driven baseline and HESS simulator runs
* per-run timeline, KPI, and resolved-config artifacts
* API-triggered runs from the control panel
* replay and snapshot payloads for the dashboard
* optional export scripts for report figures and load-profile CSVs

The physical prototype is separate from this software workflow.
This repository does not implement live hardware control of the simulator or dashboard.

---

## Current Architecture

```text
+---------------------+         +----------------------+
|   Simulator         |         |   Config + Overrides |
|   run_sim.py        |<------->|   JSON input files   |
+----------+----------+         +----------------------+
           |
           | timeline_<run_id>.csv
           | kpi_<run_id>.json
           | config_<run_id>.json
           v
+---------------------+         +----------------------+
|   Flask API         |<------->|   Control Panel      |
|   data_layer/api.py |         |   /control           |
+----------+----------+         +----------------------+
           |
           | /api/runs/<run_id>
           | /api/snapshot
           v
+---------------------+
|   Dashboard         |
|   /dashboard/       |
+---------------------+
```

---

## Simulator Module

**Directory:** `simulator/`

The simulator entry point is:

* `simulator/src/run_sim.py`

The default configuration file is:

* `config/mr90_default.json`

The simulator currently supports:

* baseline mode
* HESS mode
* partial JSON overrides merged onto the base configuration
* resolved per-run configuration export
* deterministic timeline and KPI generation
* motor power-cap enforcement through `train.P_max_w`

For each run, the simulator writes:

* `simulator/outputs/timeline_<run_id>.csv`
* `simulator/outputs/kpi_<run_id>.json`
* `simulator/outputs/config_<run_id>.json`

The KPI output includes:

* trip time and distance metrics
* traction and grid energy metrics
* HESS regeneration metrics
* alarm and voltage-related metrics
* grid-cost and baseline-savings metrics when applicable

---

## Data Layer And Control Panel

**Directory:** `data_layer/`

The current API entry point is:

* `data_layer/api.py`

The API is implemented with Flask and serves both the data endpoints and the UI entry points used in the demo flow.

Confirmed API endpoints:

* `GET /api/health`
* `GET /api/config/default`
* `GET /api/runs/list`
* `POST /api/sim/run`
* `GET /api/runs/<run_id>`
* `GET /api/snapshot`
* `GET /api/kpi/current`

The control panel is served at:

* `/control`

The control panel file is:

* `data_layer/static/control.html`

The control panel supports:

* loading the default config from the API
* editing train, corridor, HESS, and pricing fields
* client-side validation for run IDs and numeric inputs
* submitting simulator runs without terminal interaction
* opening replay JSON and dashboard routes for the created run

---

## Dashboard

**Directory:** `dashboard/`

The dashboard is served through Flask at:

* `/dashboard/`

In the current flow, the dashboard is a downstream consumer of Module 3 API payloads.
It uses:

* `GET /api/snapshot` for live-style data
* `GET /api/runs/<run_id>` for replay data

The dashboard can also be opened directly from the control panel with the selected `run_id` embedded in the URL.

---

## EMS Module

**Directory:** `ems/`

The EMS code remains in the repository and can still be run in batch mode using the file-bus workflow.
That path is currently optional and is not the main Semester 2 demo flow.

Related utilities still present in the repo include:

* `simulator/src/emit_jsonl_from_timeline.py`
* `ems/src/ems_main.py`

---

## Running The Current System

### 1. Create And Activate A Python Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r simulator\requirements.txt
```

### 2. Run Baseline And HESS From The Command Line

```powershell
python simulator\src\run_sim.py --mode baseline --run-id MR90_baseline --config config\mr90_default.json --out-dir simulator\outputs
python simulator\src\run_sim.py --mode hess --run-id MR90_hess --config config\mr90_default.json --out-dir simulator\outputs
```

Expected outputs:

* `simulator\outputs\timeline_MR90_baseline.csv`
* `simulator\outputs\kpi_MR90_baseline.json`
* `simulator\outputs\config_MR90_baseline.json`
* `simulator\outputs\timeline_MR90_hess.csv`
* `simulator\outputs\kpi_MR90_hess.json`
* `simulator\outputs\config_MR90_hess.json`

### 3. Start The API

```powershell
python data_layer\api.py
```

The API runs at:

* `http://127.0.0.1:5000`

### 4. Open The Control Panel

Open:

* `http://127.0.0.1:5000/control`

This is the main no-terminal demo workflow.

### 5. Open The Dashboard

Open:

* `http://127.0.0.1:5000/dashboard/`

Use API base values:

* `Data API base = http://127.0.0.1:5000/api`
* `Simulator API base = http://127.0.0.1:5000/api`

### 6. One-Command Demo Start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

This launches Flask and opens the control page in a browser.

---

## Export And Report Support Scripts

**Directory:** `scripts/`

Current helper scripts include:

* `scripts/export_traction_load_light.py`
* `scripts/export_load_profile.py`
* `scripts/r4_run_acceptance_tests.py`

Example commands:

```powershell
python scripts\export_traction_load_light.py --run-ids MR90_baseline MR90_hess
python scripts\export_load_profile.py --run-id MR90_baseline
python scripts\r4_run_acceptance_tests.py
```

The Report 4 acceptance runner writes evidence to:

* `test_outputs/R4/`

---

## Repo Structure

```text
.
|-- AGENTS.md
|-- QUICKSTART.md
|-- README.md
|-- REPO_MAP.md
|-- RUN_PROJECT.md
|-- config/
|   `-- mr90_default.json
|-- simulator/
|   |-- outputs/
|   |-- requirements.txt
|   `-- src/
|       |-- emit_jsonl_from_timeline.py
|       `-- run_sim.py
|-- data_layer/
|   |-- api.py
|   `-- static/
|       `-- control.html
|-- dashboard/
|   |-- index.html
|   `-- js/
|-- ems/
|   `-- src/
|-- scripts/
|   |-- export_load_profile.py
|   |-- export_traction_load_light.py
|   |-- r4_run_acceptance_tests.py
|   `-- run_demo.ps1
`-- test_outputs/
    `-- R4/
```

---

## Current Status

The repository now contains the integrated Semester 2 Module 3 workflow:

* simulator runs can be launched directly or through the API
* a resolved config artifact is saved for each run
* replay payloads include `meta.kpiSummary`
* snapshot payloads include `kpiSummary`
* the control panel can submit edited runs and open the dashboard for the selected run
* Report 4 acceptance evidence can be regenerated from the repo

---

## License

Academic use only - Capstone 2025-26.
