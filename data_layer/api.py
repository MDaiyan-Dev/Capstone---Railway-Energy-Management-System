#!/usr/bin/env python3

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional

from flask import Flask, jsonify, make_response
import time

SIM_EPOCH = time.time()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]

BUS_IN_DIR = REPO_ROOT / "bus" / "in"
STATE_JSONL = BUS_IN_DIR / "telemetry.train.state.v1.jsonl"
ENERGY_JSONL = BUS_IN_DIR / "telemetry.energy.sample.v1.jsonl"
STOP_JSONL = BUS_IN_DIR / "telemetry.event.stop.v1.jsonl"

KPI_JSON = REPO_ROOT / "simulator" / "src" / "Demo" / "week6_outputs" / "kpi_w6.json"

# ---------------------------------------------------------------------------
# Simple types
# ---------------------------------------------------------------------------


@dataclass
class StateSample:
    run_id: str
    t_s: float
    segment: int
    x_m: float
    v_mps: float
    a_mps2: float
    limit_mps: float


@dataclass
class EnergySample:
    run_id: str
    t_s: float
    power_w: float
    energy_kwh: float
    regen_kwh: float


@dataclass
class StopEvent:
    run_id: str
    station_index: int
    scheduled_ts: float
    actual_ts: float
    dwell_s: float


# ---------------------------------------------------------------------------
# Loaders (naive in-memory)
# ---------------------------------------------------------------------------


def load_kpi() -> Dict[str, Any]:
    if not KPI_JSON.exists():
        raise FileNotFoundError(f"KPI file not found at {KPI_JSON}")
    with KPI_JSON.open("r", encoding="utf-8") as f:
        kpi = json.load(f)
    return kpi


def load_state_samples() -> List[StateSample]:
    if not STATE_JSONL.exists():
        raise FileNotFoundError(f"State JSONL not found at {STATE_JSONL}")
    samples: List[StateSample] = []
    with STATE_JSONL.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            samples.append(
                StateSample(
                    run_id=obj["run_id"],
                    t_s=float(obj["t_s"]),
                    segment=int(obj["segment"]),
                    x_m=float(obj["x_m"]),
                    v_mps=float(obj["v_mps"]),
                    a_mps2=float(obj["a_mps2"]),
                    limit_mps=float(obj["limit_mps"]),
                )
            )
    return samples


def load_energy_samples() -> List[EnergySample]:
    if not ENERGY_JSONL.exists():
        raise FileNotFoundError(f"Energy JSONL not found at {ENERGY_JSONL}")
    samples: List[EnergySample] = []
    with ENERGY_JSONL.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            samples.append(
                EnergySample(
                    run_id=obj["run_id"],
                    t_s=float(obj["t_s"]),
                    power_w=float(obj["power_w"]),
                    energy_kwh=float(obj["energy_kwh"]),
                    regen_kwh=float(obj["regen_kwh"]),
                )
            )
    return samples


def load_stop_events() -> List[StopEvent]:
    if not STOP_JSONL.exists():
        # ok if we have no stops yet
        return []
    events: List[StopEvent] = []
    with STOP_JSONL.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            events.append(
                StopEvent(
                    run_id=obj["run_id"],
                    station_index=int(obj["station_index"]),
                    scheduled_ts=float(obj["scheduled_ts"]),
                    actual_ts=float(obj["actual_ts"]),
                    dwell_s=float(obj["dwell_s"]),
                )
            )
    return events


def _nearest_index_for_time(samples: List[StateSample], target_t: float) -> int:
    """Find nearest index for a given time (bruteforce is fine at this scale)."""
    if not samples:
        return 0
    best_idx = 0
    best_err = float("inf")
    for idx, s in enumerate(samples):
        err = abs(s.t_s - target_t)
        if err < best_err:
            best_err = err
            best_idx = idx
    return best_idx


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.after_request
def add_cors_headers(resp):
    # Simple CORS so the dashboard can call this from localhost
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/kpi/current", methods=["GET"])
def api_kpi_current():
    """Raw KPI snapshot (debug/introspection, not used by dashboard directly)."""
    try:
        kpi = load_kpi()
    except FileNotFoundError as e:
        return make_response(jsonify({"error": str(e)}), 500)

    run_id: Optional[str] = None
    try:
        state_samples = load_state_samples()
        if state_samples:
            run_id = state_samples[0].run_id
    except FileNotFoundError:
        pass

    payload = {"kpi_raw": kpi}
    if run_id is not None:
        payload["run_id"] = run_id

    return jsonify(payload)


@app.route("/api/replay/meta", methods=["GET"])
def api_replay_meta():
    """
    Internal replay bundle (simple, not dashboard-shaped).
    """
    try:
        state_samples = load_state_samples()
        energy_samples = load_energy_samples()
        stop_events = load_stop_events()
    except FileNotFoundError as e:
        return make_response(jsonify({"error": str(e)}), 500)

    if not state_samples or not energy_samples:
        return make_response(jsonify({"error": "No samples loaded"}), 500)

    run_id = state_samples[0].run_id
    duration_s = state_samples[-1].t_s
    corridor_length_m = max(s.x_m for s in state_samples)
    num_samples = len(state_samples)
    num_stations = len(stop_events)

    energy_by_t = {round(e.t_s, 6): e for e in energy_samples}

    timeline: List[Dict[str, Any]] = []
    for s in state_samples:
        key = round(s.t_s, 6)
        e = energy_by_t.get(key)
        power_w = e.power_w if e is not None else 0.0
        energy_kwh = e.energy_kwh if e is not None else 0.0
        regen_kwh = e.regen_kwh if e is not None else 0.0

        timeline.append(
            {
                "t_s": s.t_s,
                "x_m": s.x_m,
                "segment": s.segment,
                "v_mps": s.v_mps,
                "limit_mps": s.limit_mps,
                "power_w": power_w,
                "energy_kwh": energy_kwh,
                "regen_kwh": regen_kwh,
            }
        )

    stops_payload = [
        {
            "station_index": ev.station_index,
            "scheduled_ts": ev.scheduled_ts,
            "actual_ts": ev.actual_ts,
            "dwell_s": ev.dwell_s,
        }
        for ev in stop_events
    ]

    payload = {
        "runId": run_id,
        "meta": {
            "duration_s": duration_s,
            "num_samples": num_samples,
            "num_stations": num_stations,
            "corridor_length_m": corridor_length_m,
        },
        "timeline": timeline,
        "stops": stops_payload,
    }

    return jsonify(payload)


# ---------------------------------------------------------------------------
# Dashboard-compatible payloads
# ---------------------------------------------------------------------------


def build_run_payload_for_week6(run_id: str) -> dict:
    """
    Build a run payload in the same shape as dashboard/data/run_demo.json
    using the week 6 telemetry.

    {
      "meta": { "runId": ..., "scenario": "week6-baseline", "simulated": true },
      "timeline": {
        "durationSec": ...,
        "stepSec": ...,
        "points": [
          { "t": ..., "kpi": {...}, "assets": [...], "events": [...], "extra": {...} },
          ...
        ]
      }
    }
    """
    state_samples = load_state_samples()
    energy_samples = load_energy_samples()
    stop_events = load_stop_events()

    if not state_samples or not energy_samples:
        raise RuntimeError("No telemetry samples available")

    duration_s = state_samples[-1].t_s
    if len(state_samples) > 1:
        step_sec = state_samples[1].t_s - state_samples[0].t_s
    else:
        step_sec = duration_s or 1.0

    corridor_length_m = max(s.x_m for s in state_samples)
    total_energy = max(e.energy_kwh for e in energy_samples) or 1.0
    total_regen = max(e.regen_kwh for e in energy_samples) or 1.0

    energy_by_t: Dict[float, EnergySample] = {round(e.t_s, 6): e for e in energy_samples}

    # Map stop events to timeline indices to show ticks/events
    stop_time_to_ev: Dict[int, List[StopEvent]] = {}
    for ev in stop_events or []:
        idx = _nearest_index_for_time(state_samples, ev.actual_ts)
        stop_time_to_ev.setdefault(idx, []).append(ev)

    points: List[Dict[str, Any]] = []

    for idx, s in enumerate(state_samples):
        key = round(s.t_s, 6)
        e = energy_by_t.get(key)

        if e is None:
            power_w = 0.0
            energy_kwh = 0.0
            regen_kwh = 0.0
        else:
            power_w = e.power_w
            energy_kwh = e.energy_kwh
            regen_kwh = e.regen_kwh

        energy_frac = min(1.0, max(0.0, energy_kwh / total_energy))
        regen_frac = min(1.0, max(0.0, regen_kwh / total_regen))

        # KPIs derived from train energy; [0,1] so UI is happy
        kpi = {
            "demandServed": 0.98,
            "gridDependence": 0.5 + 0.4 * energy_frac,
            "regenUtilization": regen_frac,
            "voltageDeviation": 0.02,
        }

        # Extra metrics mapped directly to tiles
        point_extra = {
            "tractionLoad": f"{max(0.0, power_w):.0f} W",
            "batterySOC": f"{round(60 + 30 * (1.0 - regen_frac))}%",
            "regenToday": f"{regen_kwh:.2f} kWh",
        }

        pos_norm = s.x_m / corridor_length_m if corridor_length_m > 0 else 0.0

        # Assets: substations + BESS + a 'train' marker
        assets = [
            {
                "id": "subA",
                "label": "Sub A",
                "type": "substation",
                "pos": 0.0,
                "state": "green",
                "vDev": "2.0%",
                "reason": "normal",
                "ts": f"t={s.t_s:.1f}",
            },
            {
                "id": "bess",
                "label": "BESS",
                "type": "bess",
                "pos": 0.5,
                "state": "green",
                "soc": point_extra["batterySOC"],
                "reason": "normal",
                "ts": f"t={s.t_s:.1f}",
            },
            {
                "id": "subC",
                "label": "Sub C",
                "type": "substation",
                "pos": 1.0,
                "state": "green",
                "vDev": "1.5%",
                "reason": "normal",
                "ts": f"t={s.t_s:.1f}",
            },
            {
                "id": "train",
                "label": "Train",
                "type": "station",
                "pos": pos_norm,
                "state": "green" if s.v_mps <= s.limit_mps + 0.1 else "amber",
                "reason": "in motion" if s.v_mps > 0.1 else "stopped",
                "ts": f"t={s.t_s:.1f}",
            },
        ]

        events_payload: List[Dict[str, Any]] = []
        if idx in stop_time_to_ev:
            for ev in stop_time_to_ev[idx]:
                events_payload.append(
                    {
                        "id": f"stop-{ev.station_index}",
                        "severity": "info",
                        "text": f"Station {ev.station_index} dwell {ev.dwell_s:.1f}s",
                        "ts": f"t={ev.actual_ts:.1f}",
                    }
                )

        points.append(
            {
                "t": s.t_s,
                "kpi": kpi,
                "assets": assets,
                "events": events_payload,
                "extra": point_extra,
            }
        )

    payload = {
        "meta": {
            "runId": run_id,
            "scenario": "week6-baseline",
            "simulated": True,
        },
        "timeline": {
            "durationSec": duration_s,
            "stepSec": step_sec,
            "points": points,
        },
    }
    return payload


@app.route("/api/runs/<run_id>", methods=["GET"])
def api_runs(run_id: str):
    """
    Endpoint expected by the existing dashboard for archived runs.

    Dashboard does:
      GET simBase + '/runs/' + runId
    """
    try:
        payload = build_run_payload_for_week6(run_id)
    except (FileNotFoundError, RuntimeError) as e:
        return make_response(jsonify({"error": str(e)}), 500)

    return jsonify(payload)


@app.route("/api/snapshot", methods=["GET"])
def api_snapshot():
    """
    Live-like snapshot used by the dashboard's Fetch Live button.

    We treat the week6 run as a loop:
      - Map wall-clock time -> position along the run
      - Use that sample to derive tiles, KPIs, assets, events
    """
    try:
        load_kpi()  # keep the hook; even if unused, validates presence
        state_samples = load_state_samples()
        energy_samples = load_energy_samples()
        stop_events = load_stop_events()
    except FileNotFoundError as e:
        return make_response(jsonify({"error": str(e)}), 500)

    if not state_samples or not energy_samples:
        return make_response(jsonify({"error": "No telemetry samples available"}), 500)

    # Loop over run duration based on real time
    duration_s = state_samples[-1].t_s
    now = time.time()
    elapsed = now - SIM_EPOCH
    phase_t = elapsed % duration_s

    idx = _nearest_index_for_time(state_samples, phase_t)
    s = state_samples[idx]
    e = energy_samples[min(idx, len(energy_samples) - 1)]

    corridor_length_m = max(ss.x_m for ss in state_samples)
    total_energy = max(ee.energy_kwh for ee in energy_samples) or 1.0
    total_regen = max(ee.regen_kwh for ee in energy_samples) or 1.0

    energy_frac = min(1.0, max(0.0, e.energy_kwh / total_energy))
    regen_frac = min(1.0, max(0.0, e.regen_kwh / total_regen))

    # Map trip KPIs into [0,1] live KPIs
    kpi_live = {
        "demandServed": 0.98,
        "gridDependence": 0.5 + 0.4 * energy_frac,
        "regenUtilization": regen_frac,
        "voltageDeviation": 0.03,
    }

    tiles = {
        "tractionLoad": f"{max(0.0, e.power_w):.0f} W",
        "batterySOC": f"{round(60 + 30 * (1.0 - regen_frac))}%",
        "regenToday": f"{e.regen_kwh:.2f} kWh",
        "activeAlarms": 0,
    }

    pos_norm = s.x_m / corridor_length_m if corridor_length_m > 0 else 0.0

    assets = [
        {
            "id": "subA",
            "label": "Sub A",
            "type": "substation",
            "pos": 0.15,
            "state": "green",
            "vDev": "2.0%",
            "reason": "normal",
            "ts": f"t={s.t_s:.1f}",
        },
        {
            "id": "staMid",
            "label": "Sta Mid",
            "type": "station",
            "pos": 0.5,
            "state": "green",
            "reason": "normal",
            "ts": f"t={s.t_s:.1f}",
        },
        {
            "id": "bess",
            "label": "BESS",
            "type": "bess",
            "pos": 0.65,
            "state": "green",
            "soc": tiles["batterySOC"],
            "reason": "normal",
            "ts": f"t={s.t_s:.1f}",
        },
        {
            "id": "subC",
            "label": "Sub C",
            "type": "substation",
            "pos": 0.9,
            "state": "green",
            "vDev": "1.5%",
            "reason": "normal",
            "ts": f"t={s.t_s:.1f}",
        },
        {
            "id": "train",
            "label": "Train",
            "type": "station",
            "pos": pos_norm,
            "state": "green" if s.v_mps <= s.limit_mps + 0.1 else "amber",
            "reason": "in motion" if s.v_mps > 0.1 else "stopped",
            "ts": f"t={s.t_s:.1f}",
        },
    ]

    # Recent dwell events as "live" events
    recent_events = []
    for ev in stop_events[-3:]:
        if ev.actual_ts <= phase_t:
            recent_events.append(
                {
                    "id": f"stop-{ev.station_index}",
                    "severity": "info",
                    "text": f"Station {ev.station_index} dwell {ev.dwell_s:.1f}s",
                    "action": None,
                    "ts": f"t={ev.actual_ts:.1f}",
                }
            )

    payload = {
        "kpi": kpi_live,
        "tiles": tiles,
        "status": {"assets": assets},
        "events": recent_events,
    }

    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
