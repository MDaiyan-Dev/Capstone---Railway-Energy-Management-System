# Capstone---Railway-Energy-Management-System

This is our Capstone Project Repository
It will be used for collaboration and storage of all relevant resources

# 🚇 Railway Energy Management System — Capstone 2025–26

**SOFE + ELEE Integrated Project — Ontario Tech University**
**Modules:** Simulator • EMS • Data Layer • Dashboard

---

## 📌 Overview

This project implements a **small but realistic digital twin** of a metro corridor that supports energy-aware train operation, real-time telemetry, and an Energy Management System (EMS).
The system is intentionally modular and mirrors real rail software architecture:

* **Simulator** — deterministic 1D train dynamics with traction, braking, resistance, regeneration, and segment speed limits.
* **EMS (Energy Management System)** — rule-based controller that reduces energy while maintaining schedule.
* **Data Layer** — versioned JSON schemas + REST API wrapping telemetry.
* **Dashboard** — web UI for live monitoring, KPI display, and replay.

This repo contains all four modules and the file-bus interface used for integration.

---

## 🧱 Architecture

```
+-----------------+      JSONL Telemetry      +------------------+
|   Simulator     |  ->  bus/in/*.jsonl  ->   |   Data Layer     |
|  (Python)       |                           |   REST API       |
+-----------------+                           +----------+-------+
                ^                                        |
                | EMS Commands (JSONL)                   |
                | bus/out/ems.command.v1.jsonl           |
+-----------------+                                      |
|       EMS       |  <-----------------------------------+
|  (Python)       |
+-----------------+

               +--------------------------------------------+
               |                 Dashboard                   |
               |     (HTML / CSS / JS — standalone)          |
               +--------------------------------------------+
```

---

## 🚆 Simulator Module

**Directory:** `simulator/`

Implements a deterministic point-mass 1D metro train with:

* Segment speed limits
* Trapezoidal/triangular phase planning
* Davis resistance
* Traction and braking caps
* Regeneration efficiency
* Real-time (0.2–0.5s tick) and accelerated batch modes
* Telemetry outputs:

  * `telemetry.train.state.v1.jsonl`
  * `telemetry.energy.sample.v1.jsonl`
  * `telemetry.event.stop.v1.jsonl`

### Baseline Week 6 KPIs

* Corridor length: **4.2 km**
* Trip time: **328.44 s**
* Traction energy: **16.567 kWh**
* Regen energy: **5.294 kWh**
* Energy intensity: **3.945 kWh/km**

---

## ⚡ EMS Module

**Directory:** `ems/`

Implements a **V1 EMS** that operates in batch/advisory mode over simulator telemetry:

### Control Policies

1. **eco_coast_1**
   Reduce target speed to ~90% of limit between 30–50% of corridor.

2. **catchup_cancel_coast**
   If projected arrival time exceeds schedule by >5s, cancel coasting and return to limit.

3. **peak_shave_1**
   If 5s moving average traction power >2.5 MW, trim speed to reduce peak load.

4. **baseline_follow**
   Default case: match segment speed limit.

### Outputs

JSONL command timeline:
`bus/out/ems.command.v1.jsonl`

### EMS Visualization

`ems/src/plot_ems_vs_baseline.py` generates:

* Baseline speed profile
* EMS target speed profile
* Rationale shading regions

Produces: `ems_speed_profile.png`

---

## 🗂 Data Layer

**Directory:** `data_layer/`

Functions as the integration hub:

* Loads versioned schemas (`*.v1.json`)
* Reads telemetry from `bus/in/`
* Serves REST endpoints:

```
GET /api/health
GET /api/snapshot          # live view
GET /api/runs/{runId}      # replay bundle
GET /api/kpi/current       # raw KPIs
```

The dashboard consumes these APIs directly.

---

## 📊 Dashboard

**Directory:** `dashboard/`

Standalone HTML/CSS/JS frontend with:

* **Live view** (polls `/api/snapshot`)

  * Real traction load
  * Battery SOC
  * Regen energy
  * Train progress map
  * Events + alarms

* **Replay view** (fetch `/api/runs/{id}`)

  * Time scrubber
  * KPIs per timestep
  * Asset state over time
  * Train movement animation
  * Event tick markers

All tiles and KPI displays now pull real values from simulator + EMS.

---

## ▶️ Running the System

### 1. Generate Simulator Telemetry

```bash
cd simulator
python src/emit_jsonl_from_timeline.py
```

### 2. Run EMS (batch mode)

```bash
cd ems
python src/ems_main.py
```

### 3. Start Data Layer API

```bash
cd data_layer
python api.py
```

### 4. Open Dashboard

Open `dashboard/index.html` in your browser.

Set simulator API base:

```
http://127.0.0.1:5000/api
```

Use **Fetch Live** or **Fetch Run** to drive the interface.

---

## 📦 Repo Structure

```
.
├── simulator/
│   ├── src/
│   ├── outputs/
│   └── requirements.txt
│
├── ems/
│   └── src/
│
├── data_layer/
│   ├── api.py
│   └── schemas/
│
├── dashboard/
│   ├── index.html
│   └── js/
│
├── bus/
│   ├── in/
│   └── out/
│
├── README.md
└── .gitignore
```

---

## 🧪 Acceptance Tests (AT)

* **AT S1:** Kinematics sanity (limits, stopping precision, trip time ±5s)
* **AT S2:** Energy continuity (traction monotonic, regen behavior)
* **AT S3:** Telemetry frequency & skew
* **AT S4:** Determinism across runs
* **AT S5:** Replay export + scrub alignment
* **AT S6:** EMS round-trip (coming in Week 9)

---

## 🗺 Roadmap (Next 2 Weeks)

### Week 8–9

* Integrate EMS target speeds into simulator tick loop
* Add SOC dynamics + BESS limits
* Implement schedule adherence KPI
* Compare baseline vs EMS run in dashboard

### Week 10 (Final)

* Demo full closed-loop EMS-in-the-loop
* Show KPI improvements
* Present dashboard + EMS plots + integrated architecture

---

## 👥 Team Roles

* **SOFE** — Simulator, Data Layer, Dashboard
* **ELEE** — EMS
* **SOFE** — Telemetry, Replay, Visualization

---

## 📜 License

Academic use only — Capstone 2025–26.


