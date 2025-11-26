#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional

from flask import Flask, jsonify, make_response

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]

# Simulator outputs: simulator/outputs/timeline_<runId>.csv, kpi_<runId>.json
SIM_OUTPUT_DIR = REPO_ROOT / "simulator" / "outputs"

# Default run for "Fetch Live" looping
DEFAULT_LIVE_RUN_ID = "MR90_hess"

# Epoch for looping live playback
SIM_EPOCH = time.time()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class TimelineRow:
    run_id: str
    t_s: float
    x_m: float
    segment: int
    v_mps: float
    a_mps2: float
    limit_mps: float
    power_w: float
    energy_kwh: float
    regen_kwh: float
    P_grid_w: float
    P_batt_w: float
    P_sc_w: float
    soc_batt: float
    e_sc_kwh: float
    regen_kwh_captured: float
    regen_kwh_lost: float
    event: str


@dataclass
class StopEvent:
    run_id: str
    station_index: int
    actual_ts: float
    dwell_s: float


# ---------------------------------------------------------------------------
# Helpers: load timeline + KPI + stops
# ---------------------------------------------------------------------------

def _timeline_path(run_id: str) -> Path:
    return SIM_OUTPUT_DIR / f"timeline_{run_id}.csv"


def _kpi_path(run_id: str) -> Path:
    return SIM_OUTPUT_DIR / f"kpi_{run_id}.json"


def load_timeline(run_id: str) -> List[TimelineRow]:
    path = _timeline_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"Timeline CSV not found for run_id={run_id} at {path}")

    rows: List[TimelineRow] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            def get_f(name: str, default: float = 0.0) -> float:
                v = raw.get(name)
                if v is None or v == "":
                    return default
                return float(v)

            rows.append(
                TimelineRow(
                    run_id=raw.get("run_id", run_id),
                    t_s=float(raw["t"]),
                    x_m=float(raw["x_m"]),
                    segment=int(raw["segment"]),
                    v_mps=float(raw["v_mps"]),
                    a_mps2=float(raw["a_mps2"]),
                    limit_mps=float(raw["limit_mps"]),
                    power_w=float(raw["power_w"]),
                    energy_kwh=float(raw["energy_kwh"]),
                    regen_kwh=float(raw["regen_kwh"]),
                    P_grid_w=get_f("P_grid_w", float(raw["power_w"])),
                    P_batt_w=get_f("P_batt_w", 0.0),
                    P_sc_w=get_f("P_sc_w", 0.0),
                    soc_batt=get_f("soc_batt", 0.0),
                    e_sc_kwh=get_f("e_sc_kwh", 0.0),
                    regen_kwh_captured=get_f("regen_kwh_captured", 0.0),
                    regen_kwh_lost=get_f("regen_kwh_lost", 0.0),
                    event=(raw.get("event") or "").strip().upper(),
                )
            )
    if not rows:
        raise RuntimeError(f"No rows in {path}")
    return rows


def load_kpi(run_id: str) -> Dict[str, Any]:
    path = _kpi_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"KPI JSON not found for run_id={run_id} at {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def derive_stop_events(rows: List[TimelineRow]) -> List[StopEvent]:
    """Convert DWELL blocks in the timeline into station stop events."""
    events: List[StopEvent] = []
    station_index = 0
    in_dwell = False
    dwell_start: Optional[float] = None
    last_t: Optional[float] = None
    run_id = rows[0].run_id if rows else "unknown"

    for r in rows:
        is_dwell = r.event == "DWELL"
        if is_dwell and not in_dwell:
            in_dwell = True
            dwell_start = r.t_s
            last_t = r.t_s
        elif is_dwell and in_dwell:
            last_t = r.t_s
        elif (not is_dwell) and in_dwell:
            in_dwell = False
            if dwell_start is not None and last_t is not None and last_t > dwell_start:
                station_index += 1
                dwell_s = max(0.0, last_t - dwell_start)
                events.append(
                    StopEvent(
                        run_id=run_id,
                        station_index=station_index,
                        actual_ts=dwell_start,
                        dwell_s=dwell_s,
                    )
                )
            dwell_start = None
            last_t = None

    if in_dwell and dwell_start is not None and last_t is not None:
        station_index += 1
        dwell_s = max(0.0, last_t - dwell_start)
        events.append(
            StopEvent(
                run_id=run_id,
                station_index=station_index,
                actual_ts=dwell_start,
                dwell_s=dwell_s,
            )
        )

    return events


def _nearest_index_for_time(rows: List[TimelineRow], target_t: float) -> int:
    if not rows:
        return 0
    best_idx = 0
    best_err = float("inf")
    for i, r in enumerate(rows):
        err = abs(r.t_s - target_t)
        if err < best_err:
            best_err = err
            best_idx = i
    return best_idx


def compute_station_sites(
    rows: List[TimelineRow],
    stops: List[StopEvent],
    corridor_length_m: float,
) -> List[Dict[str, float]]:
    """Derive unique station sites (id, label, pos in [0,1]) from dwell events."""
    sites: List[Dict[str, float]] = []
    seen = set()

    for ev in stops:
        if ev.station_index in seen:
            continue
        seen.add(ev.station_index)
        idx = _nearest_index_for_time(rows, ev.actual_ts)
        r = rows[idx]
        pos_norm = r.x_m / corridor_length_m if corridor_length_m > 0 else 0.0
        sites.append(
            {
                "id": f"sta{ev.station_index}",
                "label": f"Sta {ev.station_index}",
                "pos": pos_norm,
            }
        )

    sites.sort(key=lambda s: s["pos"])
    return sites


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def make_assets_for_point(
    r: TimelineRow,
    station_sites: List[Dict[str, float]],
    soc_text: str,
    corridor_length_m: float,
) -> List[Dict[str, Any]]:
    """
    Build the full asset list for one time sample:
      - Sub A just before first station
      - Sub C just after last station
      - BESS between middle pair of stations
      - Stations at their true positions
      - Train at its true position
    """
    assets: List[Dict[str, Any]] = []
    n = len(station_sites)

    if n >= 1:
        first_pos = station_sites[0]["pos"]
        last_pos = station_sites[-1]["pos"]

        subA_pos = _clamp01(first_pos - 0.07)
        subC_pos = _clamp01(last_pos + 0.07)

        assets.append(
            {
                "id": "subA",
                "label": "Sub A",
                "type": "substation",
                "pos": subA_pos,
                "state": "green",
                "vDev": "2.0%",
                "reason": "normal",
                "ts": f"t={r.t_s:.1f}",
            }
        )
        if n >= 2:
            assets.append(
                {
                    "id": "subC",
                    "label": "Sub C",
                    "type": "substation",
                    "pos": subC_pos,
                    "state": "green",
                    "vDev": "1.5%",
                    "reason": "normal",
                    "ts": f"t={r.t_s:.1f}",
                }
            )

        if n >= 3:
            mid = n // 2
            left = station_sites[mid - 1]["pos"]
            right = station_sites[mid]["pos"]
            bess_pos = 0.5 * (left + right)
        elif n == 2:
            bess_pos = 0.5 * (first_pos + last_pos)
        else:
            bess_pos = first_pos

        assets.append(
            {
                "id": "bess",
                "label": "BESS",
                "type": "bess",
                "pos": _clamp01(bess_pos),
                "state": "green",
                "soc": soc_text,
                "reason": "normal",
                "ts": f"t={r.t_s:.1f}",
            }
        )

    for site in station_sites:
        assets.append(
            {
                "id": site["id"],
                "label": site["label"],
                "type": "station",
                "pos": site["pos"],
                "state": "green",
                "reason": "normal",
                "ts": f"t={r.t_s:.1f}",
            }
        )

    pos_norm = r.x_m / corridor_length_m if corridor_length_m > 0 else 0.0

    assets.append(
        {
            "id": "train",
            "label": "Train",
            "type": "station",
            "pos": _clamp01(pos_norm),
            "state": "green" if r.v_mps <= r.limit_mps + 0.1 else "amber",
            "reason": "in motion" if r.v_mps > 0.1 else "stopped",
            "ts": f"t={r.t_s:.1f}",
        }
    )

    return assets


# ---------------------------------------------------------------------------
# Flask app + CORS
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# KPI / replay helpers (debug-ish)
# ---------------------------------------------------------------------------

@app.route("/api/kpi/current", methods=["GET"])
def api_kpi_current():
    run_id = DEFAULT_LIVE_RUN_ID
    try:
        kpi = load_kpi(run_id)
    except FileNotFoundError as e:
        return make_response(jsonify({"error": str(e)}), 500)
    return jsonify({"run_id": run_id, "kpi": kpi})


@app.route("/api/replay/meta", methods=["GET"])
def api_replay_meta():
    """Simple replay payload (not used by dashboard, kept for completeness)."""
    run_id = DEFAULT_LIVE_RUN_ID
    try:
        rows = load_timeline(run_id)
        stops = derive_stop_events(rows)
    except (FileNotFoundError, RuntimeError) as e:
        return make_response(jsonify({"error": str(e)}), 500)

    duration_s = rows[-1].t_s
    corridor_length_m = max(r.x_m for r in rows)
    timeline = [
        {
            "t_s": r.t_s,
            "x_m": r.x_m,
            "segment": r.segment,
            "v_mps": r.v_mps,
            "limit_mps": r.limit_mps,
            "power_w": r.power_w,
            "energy_kwh": r.energy_kwh,
            "regen_kwh": r.regen_kwh,
        }
        for r in rows
    ]
    stops_payload = [
        {
            "station_index": ev.station_index,
            "actual_ts": ev.actual_ts,
            "dwell_s": ev.dwell_s,
        }
        for ev in stops
    ]

    payload = {
        "runId": run_id,
        "meta": {
            "duration_s": duration_s,
            "num_samples": len(rows),
            "num_stations": len(stops),
            "corridor_length_m": corridor_length_m,
        },
        "timeline": timeline,
        "stops": stops_payload,
    }
    return jsonify(payload)


# ---------------------------------------------------------------------------
# Core: build run payload for dashboard /runs/<run_id>
# ---------------------------------------------------------------------------

def build_run_payload(run_id: str) -> dict:
    rows = load_timeline(run_id)
    stops = derive_stop_events(rows)

    duration_s = rows[-1].t_s
    if len(rows) > 1:
        step_sec = rows[1].t_s - rows[0].t_s
    else:
        step_sec = duration_s or 1.0

    corridor_length_m = max(r.x_m for r in rows)
    station_sites = compute_station_sites(rows, stops, corridor_length_m)

    # Per-step regen delta based on mechanical regen_kwh, for dynamic regenUtilization
    regen_step_kwh: List[float] = []
    prev_regen = rows[0].regen_kwh
    for r in rows:
        delta = max(0.0, r.regen_kwh - prev_regen)
        regen_step_kwh.append(delta)
        prev_regen = r.regen_kwh
    max_regen_step = max(regen_step_kwh) if regen_step_kwh else 0.0
    if max_regen_step <= 0.0:
        max_regen_step = 1e-9

    # For voltage deviation: normalize grid power against its max
    grid_power_samples = [max(r.P_grid_w, 0.0) for r in rows]
    max_grid_power = max(grid_power_samples) if grid_power_samples else 0.0
    if max_grid_power <= 0.0:
        max_grid_power = 1e-6

    stop_idx_map: Dict[int, List[StopEvent]] = {}
    for ev in stops:
        idx = _nearest_index_for_time(rows, ev.actual_ts)
        stop_idx_map.setdefault(idx, []).append(ev)

    points: List[Dict[str, Any]] = []

    last_grid_frac = 0.0

    for idx, r in enumerate(rows):
        # Instantaneous power mix
        P_grid_pos = max(r.P_grid_w, 0.0)
        P_batt_pos = max(r.P_batt_w, 0.0)
        P_sc_pos = max(r.P_sc_w, 0.0)
        total_pos = P_grid_pos + P_batt_pos + P_sc_pos

        if total_pos > 1e-6:
            grid_frac = P_grid_pos / total_pos
            storage_share = (P_batt_pos + P_sc_pos) / total_pos
        else:
            grid_frac = last_grid_frac
            storage_share = 0.0

        storage_share = _clamp01(storage_share)

        # Regen utilization from *step* regen energy, normalized over max step
        step_kwh = regen_step_kwh[idx] if idx < len(regen_step_kwh) else 0.0
        regen_frac = _clamp01(step_kwh / max_regen_step)

        # Demand served: fraction of corridor distance completed
        demand_served = _clamp01(r.x_m / corridor_length_m) if corridor_length_m > 0 else 0.0

        # Voltage deviation: higher when grid power is closer to its max
        grid_stress = _clamp01(P_grid_pos / max_grid_power)
        voltage_dev = 0.01 + 0.04 * grid_stress

        last_grid_frac = grid_frac

        # Alarm logic
        active_alarms = 0
        alarm_events: List[Dict[str, Any]] = []

        # Battery SoC alarm
        if r.soc_batt > 0 and r.soc_batt < 0.2:
            active_alarms += 1
            alarm_events.append(
                {
                    "id": f"low-soc-{idx}",
                    "severity": "alarm",
                    "text": f"Battery SoC low ({r.soc_batt*100:.0f}%)",
                    "ts": f"t={r.t_s:.1f}",
                }
            )

        # Grid peak alarm: only when near top of grid range *and* moving
        if (
            max_grid_power > 0.0
            and P_grid_pos > 0.5 * max_grid_power
            and (P_grid_pos / max_grid_power) >= 0.97
            and r.v_mps > 0.5 * r.limit_mps
        ):
            active_alarms += 1
            alarm_events.append(
                {
                    "id": f"grid-peak-{idx}",
                    "severity": "alarm",
                    "text": "Grid power near limit",
                    "ts": f"t={r.t_s:.1f}",
                }
            )

        kpi = {
            "demandServed": demand_served,
            "gridDependence": grid_frac,
            "regenUtilization": regen_frac,
            "voltageDeviation": voltage_dev,
        }

        traction_load_w = P_grid_pos
        soc_text = f"{r.soc_batt * 100:.0f}%" if r.soc_batt > 0 else "n/a"
        regen_today_kwh = (
            r.regen_kwh_captured if r.regen_kwh_captured > 0 else r.regen_kwh
        )

        extra = {
            "tractionLoad": f"{traction_load_w:.0f} W",
            "batterySOC": soc_text,
            "regenToday": f"{regen_today_kwh:.2f} kWh",
            "activeAlarms": active_alarms,
            "storageShare": f"{storage_share * 100:.1f}%",
        }

        assets = make_assets_for_point(
            r=r,
            station_sites=station_sites,
            soc_text=soc_text,
            corridor_length_m=corridor_length_m,
        )

        events_payload: List[Dict[str, Any]] = []
        if idx in stop_idx_map:
            for ev in stop_idx_map[idx]:
                events_payload.append(
                    {
                        "id": f"stop-{ev.station_index}",
                        "severity": "info",
                        "text": f"Station {ev.station_index} dwell {ev.dwell_s:.1f}s",
                        "ts": f"t={ev.actual_ts:.1f}",
                    }
                )

        # append any alarm events
        events_payload.extend(alarm_events)

        points.append(
            {
                "t": r.t_s,
                "kpi": kpi,
                "assets": assets,
                "events": events_payload,
                "extra": extra,
            }
        )

    return {
        "meta": {
            "runId": run_id,
            "scenario": f"{run_id}",
            "simulated": True,
        },
        "timeline": {
            "durationSec": duration_s,
            "stepSec": step_sec,
            "points": points,
        },
    }


@app.route("/api/runs/<run_id>", methods=["GET"])
def api_runs(run_id: str):
    try:
        payload = build_run_payload(run_id)
    except (FileNotFoundError, RuntimeError) as e:
        return make_response(jsonify({"error": str(e)}), 500)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# Live snapshot: /api/snapshot
# ---------------------------------------------------------------------------

@app.route("/api/snapshot", methods=["GET"])
def api_snapshot():
    run_id = DEFAULT_LIVE_RUN_ID
    try:
        rows = load_timeline(run_id)
        stops = derive_stop_events(rows)
    except (FileNotFoundError, RuntimeError) as e:
        return make_response(jsonify({"error": str(e)}), 500)

    duration_s = rows[-1].t_s

    # Per-step regen deltas for live view, same semantics as replay
    regen_step_kwh: List[float] = []
    prev_regen = rows[0].regen_kwh
    for r in rows:
        delta = max(0.0, r.regen_kwh - prev_regen)
        regen_step_kwh.append(delta)
        prev_regen = r.regen_kwh
    max_regen_step = max(regen_step_kwh) if regen_step_kwh else 0.0
    if max_regen_step <= 0.0:
        max_regen_step = 1e-9

    # For voltage deviation: normalize grid power against its max
    grid_power_samples = [max(r.P_grid_w, 0.0) for r in rows]
    max_grid_power = max(grid_power_samples) if grid_power_samples else 0.0
    if max_grid_power <= 0.0:
        max_grid_power = 1e-6

    now = time.time()
    phase_t = ((now - SIM_EPOCH) % duration_s) if duration_s > 0 else 0.0
    idx = _nearest_index_for_time(rows, phase_t)
    r = rows[idx]

    corridor_length_m = max(rr.x_m for rr in rows)
    station_sites = compute_station_sites(rows, stops, corridor_length_m)

    # Instantaneous power mix
    P_grid_pos = max(r.P_grid_w, 0.0)
    P_batt_pos = max(r.P_batt_w, 0.0)
    P_sc_pos = max(r.P_sc_w, 0.0)
    total_pos = P_grid_pos + P_batt_pos + P_sc_w if False else P_grid_pos + P_batt_pos + P_sc_pos  # keep structure simple
    if total_pos > 1e-6:
        grid_frac = P_grid_pos / total_pos
        storage_share = (P_batt_pos + P_sc_pos) / total_pos
    else:
        grid_frac = 0.0
        storage_share = 0.0

    storage_share = _clamp01(storage_share)

    # Regen utilization from per-step mechanical regen
    step_kwh = regen_step_kwh[idx] if idx < len(regen_step_kwh) else 0.0
    regen_frac = _clamp01(step_kwh / max_regen_step)

    # Demand served: fraction of corridor completed at this instant
    demand_served = _clamp01(r.x_m / corridor_length_m) if corridor_length_m > 0 else 0.0

    # Voltage deviation: higher when grid power near its max
    grid_stress = _clamp01(P_grid_pos / max_grid_power)
    voltage_dev = 0.01 + 0.04 * grid_stress

    # Alarm logic
    active_alarms = 0
    recent_events: List[Dict[str, Any]] = []

    if r.soc_batt > 0 and r.soc_batt < 0.2:
        active_alarms += 1
        recent_events.append(
            {
                "id": f"low-soc-live-{idx}",
                "severity": "alarm",
                "text": f"Battery SoC low ({r.soc_batt*100:.0f}%)",
                "action": None,
                "ts": f"t={r.t_s:.1f}",
            }
        )

    if (
        max_grid_power > 0.0
        and P_grid_pos > 0.5 * max_grid_power
        and (P_grid_pos / max_grid_power) >= 0.97
        and r.v_mps > 0.5 * r.limit_mps
    ):
        active_alarms += 1
        recent_events.append(
            {
                "id": f"grid-peak-live-{idx}",
                "severity": "alarm",
                "text": "Grid power near limit",
                "action": None,
                "ts": f"t={r.t_s:.1f}",
            }
        )

    # Recent dwell events as "live" info
    for ev in stops[-3:]:
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

    kpi_live = {
        "demandServed": demand_served,
        "gridDependence": grid_frac,
        "regenUtilization": regen_frac,
        "voltageDeviation": voltage_dev,
    }

    traction_load_w = P_grid_pos
    soc_text = f"{r.soc_batt * 100:.0f}%" if r.soc_batt > 0 else "n/a"
    regen_today_kwh = (
        r.regen_kwh_captured if r.regen_kwh_captured > 0 else r.regen_kwh
    )

    tiles = {
        "tractionLoad": f"{traction_load_w:.0f} W",
        "batterySOC": soc_text,
        "regenToday": f"{regen_today_kwh:.2f} kWh",
        "activeAlarms": active_alarms,
        "storageShare": f"{storage_share * 100:.1f}%",
    }

    assets = make_assets_for_point(
        r=r,
        station_sites=station_sites,
        soc_text=soc_text,
        corridor_length_m=corridor_length_m,
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
