#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from flask import Flask, jsonify, make_response, request, send_from_directory

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]

# Simulator outputs: simulator/outputs/timeline_<runId>.csv, kpi_<runId>.json
SIM_OUTPUT_DIR = REPO_ROOT / "simulator" / "outputs"
SIM_RUNNER_PATH = REPO_ROOT / "simulator" / "src" / "run_sim.py"
DEFAULT_BASE_CONFIG = REPO_ROOT / "config" / "mr90_default.json"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DATA_LAYER_STATIC_DIR = REPO_ROOT / "data_layer" / "static"
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# Default run for "Fetch Live" looping
DEFAULT_LIVE_RUN_ID = "MR90_hess"
EXPO_RUN_DISPLAY_ORDER = {
    "EXPO_baseline_reference": 0,
    "EXPO_hess_reference": 1,
    "EXPO_high_price": 2,
    "EXPO_constrained_grid": 3,
}

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


def _config_path(run_id: str) -> Path:
    return SIM_OUTPUT_DIR / f"config_{run_id}.json"


def _tem_path(run_id: str) -> Path:
    return SIM_OUTPUT_DIR / f"tem_{run_id}.json"


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


def load_resolved_config_optional(run_id: str) -> Optional[Dict[str, Any]]:
    path = _config_path(run_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_tem_optional(run_id: str) -> Optional[Dict[str, Any]]:
    path = _tem_path(run_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _optional_clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def is_canonical_run_id(run_id: str) -> bool:
    return isinstance(run_id, str) and run_id.startswith("EXPO_")


def extract_run_metadata(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    meta = cfg.get("meta", {}) if isinstance(cfg, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    pricing = cfg.get("pricing", {}) if isinstance(cfg, dict) else {}
    if not isinstance(pricing, dict):
        pricing = {}

    mode = _optional_clean_text(meta.get("mode"))
    if mode not in {"baseline", "hess"}:
        mode = None

    raw_price = pricing.get("grid_price_per_kwh")
    try:
        price_per_kwh = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError):
        price_per_kwh = None

    return {
        "scenario_name": _optional_clean_text(meta.get("scenario_name")),
        "mode": mode,
        "created_at_utc": _optional_clean_text(meta.get("created_at_utc")),
        "price_per_kwh": price_per_kwh,
    }


def _round_or_none(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _positive_number_enabled(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False


def select_ems_reference_row(rows: List[TimelineRow]) -> Optional[TimelineRow]:
    if not rows:
        return None

    for row in reversed(rows):
        positive_traction_power = max(row.P_grid_w, 0.0) + max(row.P_batt_w, 0.0) + max(row.P_sc_w, 0.0)
        charging_power = max(-row.P_batt_w, 0.0) + max(-row.P_sc_w, 0.0)
        if positive_traction_power > 1e-6 or charging_power > 1e-6:
            return row

    return rows[-1]


def summarize_power_source(row: Optional[TimelineRow]) -> str:
    if row is None:
        return "Power source summary unavailable"

    charging_power = max(-row.P_batt_w, 0.0) + max(-row.P_sc_w, 0.0)
    if charging_power > 1e-6:
        return "Regenerative capture active"

    positive_grid = max(row.P_grid_w, 0.0)
    positive_storage = max(row.P_batt_w, 0.0) + max(row.P_sc_w, 0.0)
    total_positive_power = positive_grid + positive_storage

    if total_positive_power <= 1e-6:
        return "No active traction demand"

    storage_share = positive_storage / total_positive_power
    if storage_share >= 0.65:
        return "Storage-assisted traction"
    if storage_share >= 0.2:
        return "Mixed grid and storage support"
    return "Grid dominant"


def build_ems_summary(
    cfg: Optional[Dict[str, Any]],
    kpi: Optional[Dict[str, Any]],
    row: Optional[TimelineRow],
) -> Dict[str, Any]:
    run_meta = extract_run_metadata(cfg)
    mode = run_meta["mode"]
    if mode not in {"baseline", "hess"}:
        mode = "hess" if row and (abs(row.P_batt_w) > 1e-6 or abs(row.P_sc_w) > 1e-6) else "baseline"

    hess_cfg = cfg.get("hess", {}) if isinstance(cfg, dict) and isinstance(cfg.get("hess"), dict) else {}
    supercap_cfg = hess_cfg.get("supercap", {}) if isinstance(hess_cfg.get("supercap"), dict) else {}
    grid_cfg = hess_cfg.get("grid", {}) if isinstance(hess_cfg.get("grid"), dict) else {}
    kpi = kpi if isinstance(kpi, dict) else {}

    if mode == "baseline":
        mode_description = (
            "Traditional grid-dependent operating mode with no onboard storage support."
        )
        storage_enabled = False
        startup_assist_enabled = False
        regen_capture_enabled = False
        grid_sag_mitigation_enabled = False
    else:
        mode_description = (
            "Battery and supercapacitor support reduce grid dependence during traction and help capture regenerative energy."
        )
        storage_enabled = True
        startup_assist_enabled = _positive_number_enabled(supercap_cfg.get("startup_assist_secs"))
        regen_capture_enabled = True
        grid_sag_mitigation_enabled = _positive_number_enabled(grid_cfg.get("sag_max_power_kw"))

    positive_grid = max(row.P_grid_w, 0.0) if row is not None else 0.0
    positive_storage = (
        max(row.P_batt_w, 0.0) + max(row.P_sc_w, 0.0)
        if row is not None
        else 0.0
    )
    total_positive_power = positive_grid + positive_storage
    storage_share_pct = (
        _round_or_none((positive_storage / total_positive_power) * 100.0, 1)
        if total_positive_power > 1e-6
        else None
    )

    regen_captured_kwh = kpi.get("regen_captured_kwh")
    if regen_captured_kwh is None and row is not None:
        regen_captured_kwh = row.regen_kwh_captured

    regen_lost_kwh = kpi.get("regen_lost_kwh")
    if regen_lost_kwh is None and row is not None:
        regen_lost_kwh = row.regen_kwh_lost

    battery_soc = row.soc_batt if row is not None else None
    supercap_energy_kwh = row.e_sc_kwh if row is not None else None

    return {
        "mode": mode,
        "modeDescription": mode_description,
        "storageEnabled": storage_enabled,
        "startupAssistEnabled": startup_assist_enabled,
        "regenCaptureEnabled": regen_capture_enabled,
        "gridSagMitigationEnabled": grid_sag_mitigation_enabled,
        "batterySOC": _round_or_none(battery_soc, 6),
        "supercapEnergy_kwh": _round_or_none(supercap_energy_kwh, 6),
        "storageShare_pct": storage_share_pct,
        "regenCaptured_kwh": _round_or_none(regen_captured_kwh, 6),
        "regenLost_kwh": _round_or_none(regen_lost_kwh, 6),
        "currentPowerSourceSummary": summarize_power_source(row),
    }


def build_tem_summary_preview(artifact: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(artifact, dict):
        return None
    return {
        "status": artifact.get("status"),
        "totalCost": artifact.get("total_cost"),
        "savingsVsBaseline": artifact.get("savings_total_vs_baseline"),
        "recommendation": artifact.get("recommendation_summary"),
    }


def build_tem_recommendations(
    mode: Optional[str],
    price_per_kwh: float,
    baseline_available: bool,
) -> Dict[str, str]:
    if mode == "baseline":
        recommendation = (
            "TEM analysis identifies this run as a reference for later HESS cost comparisons."
        )
        strategy = (
            "Baseline mode does not dispatch onboard storage. Use this run as the grid-cost reference for later comparison."
        )
    elif mode == "hess" and price_per_kwh >= 0.2:
        recommendation = (
            "Prioritize storage discharge during high-price periods to reduce grid draw."
        )
        strategy = (
            "Use battery and supercapacitor support during higher-cost traction demand and recover energy during braking where available."
        )
    elif mode == "hess" and price_per_kwh <= 0.05:
        recommendation = (
            "Charge storage during low-price periods where operationally feasible."
        )
        strategy = (
            "Favor charging storage during lower-cost windows, then use stored energy to offset later traction demand."
        )
    else:
        recommendation = (
            "Use storage selectively to trim grid demand while maintaining regenerative capture."
        )
        strategy = (
            "Blend moderate storage support with regenerative capture to reduce total grid cost without changing the simulator timeline."
        )

    if not baseline_available:
        strategy = (
            f"{strategy} Baseline comparison not available. Savings fields are informational only after a baseline run is provided."
        )

    return {
        "recommendation_summary": recommendation,
        "charge_discharge_strategy_summary": strategy,
    }


def build_tem_artifact(run_id: str, baseline_run_id: Optional[str] = None) -> Dict[str, Any]:
    kpi = load_kpi(run_id)
    cfg = load_resolved_config_optional(run_id)
    if cfg is None:
        raise FileNotFoundError(
            f"Resolved config JSON not found for run_id={run_id} at {_config_path(run_id)}"
        )

    run_meta = extract_run_metadata(cfg)
    price_per_kwh = float(run_meta["price_per_kwh"] or 0.0)
    grid_energy_kwh = float(kpi.get("grid_energy_kwh", 0.0) or 0.0)
    distance_km = float(kpi.get("distance_km", 0.0) or 0.0)
    total_cost = grid_energy_kwh * price_per_kwh

    savings_total = None
    savings_per_train_km = None
    baseline_id_clean = _optional_clean_text(baseline_run_id)
    if baseline_id_clean is not None:
        baseline_kpi = load_kpi(baseline_id_clean)
        baseline_grid_energy_kwh = float(baseline_kpi.get("grid_energy_kwh", 0.0) or 0.0)
        baseline_total_cost = baseline_grid_energy_kwh * price_per_kwh
        savings_total = baseline_total_cost - total_cost
        savings_per_train_km = (
            savings_total / distance_km if distance_km > 0.0 else None
        )

    recommendations = build_tem_recommendations(
        mode=run_meta["mode"],
        price_per_kwh=price_per_kwh,
        baseline_available=baseline_id_clean is not None,
    )

    return {
        "run_id": run_id,
        "scenario_name": run_meta["scenario_name"],
        "mode": run_meta["mode"],
        "analyzed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pricing_used_per_kwh": _round_or_none(price_per_kwh, 6),
        "baseline_run_id": baseline_id_clean,
        "grid_energy_kwh": _round_or_none(grid_energy_kwh, 6),
        "distance_km": _round_or_none(distance_km, 6),
        "total_cost": _round_or_none(total_cost, 6),
        "savings_total_vs_baseline": _round_or_none(savings_total, 6),
        "savings_per_train_km_vs_baseline": _round_or_none(savings_per_train_km, 6),
        "recommendation_summary": recommendations["recommendation_summary"],
        "charge_discharge_strategy_summary": recommendations["charge_discharge_strategy_summary"],
        "status": "complete",
    }


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
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/config/default", methods=["GET"])
def api_config_default():
    if not DEFAULT_BASE_CONFIG.exists():
        return make_response(
            jsonify({"error": f"Default config not found at {DEFAULT_BASE_CONFIG}"}),
            500,
        )
    try:
        with DEFAULT_BASE_CONFIG.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return make_response(jsonify({"error": f"Failed to load default config: {e}"}), 500)

    if not isinstance(cfg, dict):
        return make_response(jsonify({"error": "Default config must be a JSON object"}), 500)

    # Ensure expected top-level sections exist for control UI.
    if "train" not in cfg:
        cfg["train"] = {}
    if "corridor" not in cfg:
        cfg["corridor"] = {}
    if "hess" not in cfg:
        cfg["hess"] = {}
    if "pricing" not in cfg:
        cfg["pricing"] = {"grid_price_per_kwh": 0.25}

    return jsonify(cfg)


@app.route("/api/runs/list", methods=["GET"])
def api_runs_list():
    runs: List[Dict[str, Any]] = []
    baseline_run_ids = set()
    for p in SIM_OUTPUT_DIR.glob("timeline_*.csv"):
        run_id = p.stem.removeprefix("timeline_")
        mtime_ts = p.stat().st_mtime
        mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat()
        run_meta = extract_run_metadata(load_resolved_config_optional(run_id))
        tem_artifact = load_tem_optional(run_id)
        if run_meta["mode"] == "baseline":
            baseline_run_ids.add(run_id)
        runs.append(
            {
                "run_id": run_id,
                "mtime_iso": mtime,
                "scenario_name": run_meta["scenario_name"],
                "mode": run_meta["mode"],
                "tem_available": tem_artifact is not None,
                "tem_status": tem_artifact.get("status") if isinstance(tem_artifact, dict) else None,
                "is_canonical": is_canonical_run_id(run_id),
                "_sort_mtime": mtime_ts,
            }
        )

    has_any_baseline = bool(baseline_run_ids)
    for run in runs:
        baseline_available = run["mode"] != "baseline" and has_any_baseline
        labels: List[str] = []
        if run["is_canonical"]:
            labels.append("Canonical")
        if run["tem_available"]:
            labels.append("TEM ready")
        if baseline_available:
            labels.append("Baseline available")
        run["baseline_available"] = baseline_available
        run["labels"] = labels

    runs.sort(
        key=lambda r: (
            0 if r["is_canonical"] else 1,
            EXPO_RUN_DISPLAY_ORDER.get(r["run_id"], 999 if r["is_canonical"] else 0),
            -float(r["_sort_mtime"]),
        )
    )
    for run in runs:
        run.pop("_sort_mtime", None)
    return jsonify(runs)


@app.route("/control", methods=["GET"])
def control_page():
    return send_from_directory(DATA_LAYER_STATIC_DIR, "control.html")


@app.route("/dashboard/", methods=["GET"])
def dashboard_index():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.route("/dashboard/<path:filename>", methods=["GET"])
def dashboard_static(filename: str):
    return send_from_directory(DASHBOARD_DIR, filename)


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
    kpi_summary = load_kpi(run_id)
    stops = derive_stop_events(rows)
    resolved_cfg = load_resolved_config_optional(run_id)
    run_meta = extract_run_metadata(resolved_cfg)
    tem_artifact = load_tem_optional(run_id)
    ems_summary = build_ems_summary(
        cfg=resolved_cfg,
        kpi=kpi_summary,
        row=select_ems_reference_row(rows),
    )

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
            "kpiSummary": kpi_summary,
            "scenarioName": run_meta["scenario_name"],
            "mode": run_meta["mode"],
            "createdAtUtc": run_meta["created_at_utc"],
            "emsSummary": ems_summary,
            "temAvailable": tem_artifact is not None,
            "temStatus": tem_artifact.get("status") if isinstance(tem_artifact, dict) else None,
            "temSummaryPreview": build_tem_summary_preview(tem_artifact),
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
# TEM analysis: post-run artifact generation
# ---------------------------------------------------------------------------

@app.route("/api/tem/run", methods=["POST"])
def api_tem_run():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return make_response(jsonify({"error": "Invalid JSON payload"}), 400)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return make_response(jsonify({"error": "run_id is required"}), 400)
    run_id = run_id.strip()
    if not RUN_ID_RE.fullmatch(run_id):
        return make_response(
            jsonify({"error": "run_id must use only letters, digits, underscore, dash"}),
            400,
        )

    baseline_run_id_raw = payload.get("baseline_run_id")
    if baseline_run_id_raw is None:
        baseline_run_id = None
    elif isinstance(baseline_run_id_raw, str):
        baseline_run_id = baseline_run_id_raw.strip() or None
        if baseline_run_id is not None and not RUN_ID_RE.fullmatch(baseline_run_id):
            return make_response(
                jsonify(
                    {
                        "error": "baseline_run_id must use only letters, digits, underscore, dash"
                    }
                ),
                400,
            )
    else:
        return make_response(
            jsonify({"error": "baseline_run_id must be a string when provided"}),
            400,
        )

    try:
        artifact = build_tem_artifact(run_id=run_id, baseline_run_id=baseline_run_id)
    except FileNotFoundError as e:
        return make_response(jsonify({"error": str(e)}), 404)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
        return make_response(jsonify({"error": f"Failed to build TEM analysis: {e}"}), 500)

    tem_path = _tem_path(run_id)
    tem_path.parent.mkdir(parents=True, exist_ok=True)
    with tem_path.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)

    return jsonify(
        {
            "status": "ok",
            "run_id": run_id,
            "tem_artifact": str(tem_path),
            "tem_summary": build_tem_summary_preview(artifact),
        }
    )


@app.route("/api/tem/<run_id>", methods=["GET"])
def api_tem_get(run_id: str):
    artifact = load_tem_optional(run_id)
    if artifact is None:
        return make_response(
            jsonify({"error": f"TEM artifact not found for run_id={run_id} at {_tem_path(run_id)}"}),
            404,
        )
    return jsonify(artifact)


# ---------------------------------------------------------------------------
# Trigger simulator run: /api/sim/run
# ---------------------------------------------------------------------------

@app.route("/api/sim/run", methods=["POST"])
def api_sim_run():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return make_response(jsonify({"error": "Invalid JSON payload"}), 400)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return make_response(jsonify({"error": "run_id is required"}), 400)
    if not RUN_ID_RE.fullmatch(run_id):
        return make_response(
            jsonify({"error": "run_id must use only letters, digits, underscore, dash"}),
            400,
        )

    mode = payload.get("mode", "hess")
    if mode not in {"baseline", "hess"}:
        return make_response(jsonify({"error": "mode must be baseline or hess"}), 400)

    scenario_name_raw = payload.get("scenario_name")
    if scenario_name_raw is None:
        scenario_name = None
    elif isinstance(scenario_name_raw, str):
        scenario_name = scenario_name_raw.strip() or None
    else:
        return make_response(jsonify({"error": "scenario_name must be a string when provided"}), 400)

    base_config_raw = payload.get("base_config")
    if base_config_raw is None:
        base_config_path = DEFAULT_BASE_CONFIG
    elif isinstance(base_config_raw, str) and base_config_raw.strip():
        p = Path(base_config_raw)
        base_config_path = p if p.is_absolute() else (REPO_ROOT / p)
    else:
        return make_response(jsonify({"error": "base_config must be a string path"}), 400)

    overrides = payload.get("overrides", None)
    overrides_path: Optional[Path] = None
    if overrides is not None:
        if not isinstance(overrides, dict):
            return make_response(jsonify({"error": "overrides must be a JSON object"}), 400)
        SIM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        overrides_path = SIM_OUTPUT_DIR / f"overrides_{run_id}.json"
        with overrides_path.open("w", encoding="utf-8") as f:
            json.dump(overrides, f, indent=2)

    cmd = [
        sys.executable,
        str(SIM_RUNNER_PATH),
        "--mode",
        mode,
        "--run-id",
        run_id,
        "--config",
        str(base_config_path),
    ]
    if scenario_name is not None:
        cmd.extend(["--scenario-name", scenario_name])
    if overrides_path is not None:
        cmd.extend(["--overrides", str(overrides_path)])

    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return make_response(
            jsonify(
                {
                    "error": "Simulator run timed out after 60 seconds",
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                }
            ),
            500,
        )
    except Exception as e:
        return make_response(jsonify({"error": f"Failed to start simulator: {e}"}), 500)

    timeline_path = SIM_OUTPUT_DIR / f"timeline_{run_id}.csv"
    kpi_path = SIM_OUTPUT_DIR / f"kpi_{run_id}.json"
    config_path = SIM_OUTPUT_DIR / f"config_{run_id}.json"

    if proc.returncode != 0:
        return make_response(
            jsonify(
                {
                    "error": f"Simulator failed with exit code {proc.returncode}",
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            ),
            500,
        )

    run_meta = extract_run_metadata(load_resolved_config_optional(run_id))
    return jsonify(
        {
            "status": "ok",
            "run_id": run_id,
            "mode": mode,
            "scenario_name": run_meta["scenario_name"],
            "created_at_utc": run_meta["created_at_utc"],
            "price_per_kwh": run_meta["price_per_kwh"],
            "artifacts": {
                "timeline": str(timeline_path),
                "kpi": str(kpi_path),
                "config": str(config_path),
                "overrides": str(overrides_path) if overrides_path else None,
            },
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    )


# ---------------------------------------------------------------------------
# Live snapshot: /api/snapshot
# ---------------------------------------------------------------------------

@app.route("/api/snapshot", methods=["GET"])
def api_snapshot():
    run_id = DEFAULT_LIVE_RUN_ID
    try:
        rows = load_timeline(run_id)
        kpi_summary = load_kpi(run_id)
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
    resolved_cfg = load_resolved_config_optional(run_id)

    corridor_length_m = max(rr.x_m for rr in rows)
    station_sites = compute_station_sites(rows, stops, corridor_length_m)

    # Instantaneous power mix
    P_grid_pos = max(r.P_grid_w, 0.0)
    P_batt_pos = max(r.P_batt_w, 0.0)
    P_sc_pos = max(r.P_sc_w, 0.0)
    total_pos = P_grid_pos + P_batt_pos + P_sc_pos
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
        "kpiSummary": kpi_summary,
        "emsSummary": build_ems_summary(cfg=resolved_cfg, kpi=kpi_summary, row=r),
        "tiles": tiles,
        "status": {"assets": assets},
        "events": recent_events,
    }
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
