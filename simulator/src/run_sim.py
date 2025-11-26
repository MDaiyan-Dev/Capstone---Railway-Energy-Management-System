#!/usr/bin/env python3
"""
run_sim.py

Batch train simulator with integrated EMS / HESS.

Modes:
  baseline  - classic grid + simple regen, no onboard storage logic
  hess      - hybrid energy storage (battery + supercap) sharing traction,
              soaking regen, and supporting ride-through.

Numeric values come from a JSON config (train + corridor + hess).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd


# ---------------- Dataclasses ----------------


@dataclass
class TrainParams:
    mass_kg: float
    a_acc_mps2: float
    a_brk_mps2: float
    P_max_w: float
    F_max_n: float
    F_brk_max_n: float
    regen_eff: float
    davis_A: float
    davis_B: float
    davis_C: float


@dataclass
class CorridorParams:
    segments_m: List[float]
    speed_limits_mps: List[float]
    dwell_s: float


@dataclass
class HESSParams:
    batt_capacity_kwh: float
    batt_initial_soc: float
    batt_max_power_kw: float
    sc_capacity_kwh: float
    sc_initial_kwh: float
    sc_max_power_kw: float
    sc_startup_assist_s: float
    grid_sag_max_kw: float


@dataclass
class SimParams:
    run_id: str
    dt_target: float
    mode: str  # "baseline" or "hess"


# ---------------- Defaults / config ----------------


def default_config() -> Dict[str, Any]:
    """Built-in fallback config roughly matching config/mr90_default.json."""
    return {
        "train": {
            "name": "MR90_3car",
            "mass_kg": 146099.0,
            "a_acc_mps2": 0.60,
            "a_brk_mps2": 0.89,
            "P_max_w": 1300000.0,
            "F_max_n": 150000.0,
            "F_brk_max_n": 180000.0,
            "regen_eff": 0.85,
            "davis": {"A": 4500.0, "B": 75.0, "C": 15.0},
        },
        "corridor": {
            "segments_m": [1200.0, 1600.0, 1400.0],
            "speed_limits_mps": [25.0, 22.0, 20.0],
            "dwell_s": 20.0,
        },
        "hess": {
            "battery": {
                "capacity_kwh": 25.0,
                "initial_soc": 0.6,
                "max_power_kw": 1000.0,
            },
            "supercap": {
                "capacity_kwh": 5.0,
                "initial_energy_kwh": 2.5,
                "max_power_kw": 2000.0,
                "startup_assist_secs": 5.0,
            },
            "grid": {"sag_max_power_kw": 1000.0},
        },
    }


def load_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return default_config()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_params(cfg: Dict[str, Any], sim_mode: str, run_id: str, dt: float):
    tcfg = cfg["train"]
    ccf = cfg["corridor"]
    hcfg = cfg.get("hess", {})

    train = TrainParams(
        mass_kg=float(tcfg["mass_kg"]),
        a_acc_mps2=float(tcfg["a_acc_mps2"]),
        a_brk_mps2=float(tcfg["a_brk_mps2"]),
        P_max_w=float(tcfg["P_max_w"]),
        F_max_n=float(tcfg["F_max_n"]),
        F_brk_max_n=float(tcfg["F_brk_max_n"]),
        regen_eff=float(tcfg["regen_eff"]),
        davis_A=float(tcfg["davis"]["A"]),
        davis_B=float(tcfg["davis"]["B"]),
        davis_C=float(tcfg["davis"]["C"]),
    )

    corridor = CorridorParams(
        segments_m=list(map(float, ccf["segments_m"])),
        speed_limits_mps=list(map(float, ccf["speed_limits_mps"])),
        dwell_s=float(ccf["dwell_s"]),
    )

    hess = None
    if sim_mode == "hess":
        batt = hcfg["battery"]
        sc = hcfg["supercap"]
        grid = hcfg["grid"]
        hess = HESSParams(
            batt_capacity_kwh=float(batt["capacity_kwh"]),
            batt_initial_soc=float(batt["initial_soc"]),
            batt_max_power_kw=float(batt["max_power_kw"]),
            sc_capacity_kwh=float(sc["capacity_kwh"]),
            sc_initial_kwh=float(sc["initial_energy_kwh"]),
            sc_max_power_kw=float(sc["max_power_kw"]),
            sc_startup_assist_s=float(sc["startup_assist_secs"]),
            grid_sag_max_kw=float(grid["sag_max_power_kw"]),
        )

    sim = SimParams(run_id=run_id, dt_target=dt, mode=sim_mode)
    return train, corridor, hess, sim


# ---------------- Physics helpers ----------------


def davis_force(v: np.ndarray, A: float, B: float, C: float) -> np.ndarray:
    return A + B * v + C * v * v


def segment_profile(L, vmax, a_acc, a_brk, dt_target):
    """Return piecewise time, pos, vel arrays for one segment (start v0=0, end v=0)."""
    d_acc = (vmax * vmax) / (2 * a_acc)
    d_brk = (vmax * vmax) / (2 * a_brk)

    if d_acc + d_brk <= L:
        d_cruise = L - (d_acc + d_brk)
        t_acc = vmax / a_acc
        t_brk = vmax / a_brk
        t_cruise = d_cruise / vmax

        n_acc = max(5, int(round(t_acc / dt_target)))
        n_cruise = max(5, int(round(t_cruise / dt_target))) if t_cruise > 0 else 0
        n_brk = max(5, int(round(t_brk / dt_target)))

        t_acc_v = np.linspace(0, t_acc, n_acc, endpoint=False)
        t_cruise_v = (
            np.linspace(0, t_cruise, n_cruise, endpoint=False)
            if n_cruise > 0
            else np.array([])
        )
        t_brk_v = np.linspace(0, t_brk, n_brk)

        v_acc = a_acc * t_acc_v
        x_acc = 0.5 * a_acc * t_acc_v ** 2

        v_cruise = vmax * np.ones_like(t_cruise_v)
        x_cruise = d_acc + vmax * t_cruise_v

        v_brk = vmax - a_brk * t_brk_v
        v_brk = np.clip(v_brk, 0.0, None)
        x_brk = d_acc + d_cruise + (vmax * t_brk_v - 0.5 * a_brk * t_brk_v ** 2)

        t_seg = np.concatenate(
            [t_acc_v, t_acc + t_cruise_v, t_acc + t_cruise + t_brk_v]
        )
        v_seg = np.concatenate([v_acc, v_cruise, v_brk])
        x_seg = np.concatenate([x_acc, x_cruise, x_brk])
        phase = (
            ["ACCEL"] * len(t_acc_v)
            + ["CRUISE"] * len(t_cruise_v)
            + ["BRAKE"] * len(t_brk_v)
        )
        return t_seg, x_seg, v_seg, phase

    else:
        v_peak = math.sqrt(L * 2.0 / (1.0 / a_acc + 1.0 / a_brk))
        t_acc = v_peak / a_acc
        t_brk = v_peak / a_brk
        d_acc = (v_peak * v_peak) / (2 * a_acc)

        n_acc = max(5, int(round(t_acc / dt_target)))
        n_brk = max(5, int(round(t_brk / dt_target)))

        t_acc_v = np.linspace(0, t_acc, n_acc, endpoint=False)
        t_brk_v = np.linspace(0, t_brk, n_brk)

        v_acc = a_acc * t_acc_v
        x_acc = 0.5 * a_acc * t_acc_v ** 2

        v_brk = v_peak - a_brk * t_brk_v
        v_brk = np.clip(v_brk, 0.0, None)
        x_brk = d_acc + (v_peak * t_brk_v - 0.5 * a_brk * t_brk_v ** 2)

        t_seg = np.concatenate([t_acc_v, t_acc + t_brk_v])
        v_seg = np.concatenate([v_acc, v_brk])
        x_seg = np.concatenate([x_acc, x_brk])
        phase = ["ACCEL"] * len(t_acc_v) + ["BRAKE"] * len(t_brk_v)
        return t_seg, x_seg, v_seg, phase


# ---------------- HESS logic (in-loop) ----------------


def apply_hess_step(
    mode: str,
    hess: Optional[HESSParams],
    train: TrainParams,
    t_i: float,
    dt_i: float,
    P_trac_kw: float,
    P_brake_kw: float,
    event_prev: str,
    event_curr: str,
    time_since_departure: float,
    e_batt: float,
    e_sc: float,
):
    """
    Decide power split and update HESS state for this timestep.
    Returns:
      P_grid_w, P_batt_w, P_sc_w, e_batt_new, e_sc_new,
      regen_captured_kwh_step, regen_lost_kwh_step, new_time_since_departure
    """
    if mode != "hess" or hess is None or dt_i <= 0:
        # Baseline: everything from grid, no storage effects
        P_total_kw = P_trac_kw - P_brake_kw
        return (
            P_total_kw * 1000.0,
            0.0,
            0.0,
            e_batt,
            e_sc,
            0.0,
            0.0,
            time_since_departure + dt_i,
        )

    # Update departure timer
    if event_prev == "DWELL" and event_curr != "DWELL":
        time_since_departure = 0.0
    else:
        time_since_departure += dt_i

    # Initialise outputs
    P_grid_kw = 0.0
    P_batt_kw = 0.0
    P_sc_kw = 0.0
    regen_captured = 0.0
    regen_lost = 0.0

    # Traction case
    if P_trac_kw > 0.0:
        remaining_kw = P_trac_kw

        # startup supercap assist
        if time_since_departure <= hess.sc_startup_assist_s:
            sc_e_avail = max(e_sc, 0.0)
            sc_p_cap = min(
                hess.sc_max_power_kw,
                sc_e_avail / (dt_i / 3600.0) if sc_e_avail > 0 else 0.0,
            )
            P_sc_kw = min(remaining_kw, sc_p_cap)
            remaining_kw -= P_sc_kw

        # grid contribution (no grid events model yet, just apply sag cap)
        P_grid_cap = min(hess.grid_sag_max_kw, remaining_kw)
        P_grid_kw = P_grid_cap
        remaining_kw -= P_grid_cap

        # battery covers residual
        if remaining_kw > 1e-6:
            batt_e_avail = max(e_batt, 0.0)
            batt_p_cap = min(
                hess.batt_max_power_kw,
                batt_e_avail / (dt_i / 3600.0) if batt_e_avail > 0 else 0.0,
            )
            P_batt_kw = min(remaining_kw, batt_p_cap)
            remaining_kw -= P_batt_kw

        # if still remaining, let grid take it (we keep kinematics fixed)
        if remaining_kw > 1e-6:
            P_grid_kw += remaining_kw

        # update energies
        e_batt -= max(P_batt_kw, 0.0) * dt_i / 3600.0
        e_sc -= max(P_sc_kw, 0.0) * dt_i / 3600.0

    # Braking / regen case
    elif P_brake_kw > 0.0:
        e_reg_step = P_brake_kw * train.regen_eff * dt_i / 3600.0

        # first SC
        sc_cap = hess.sc_capacity_kwh - e_sc
        e_to_sc = min(max(sc_cap, 0.0), e_reg_step)
        e_sc += e_to_sc
        e_reg_step -= e_to_sc

        # then battery
        batt_cap = hess.batt_capacity_kwh - e_batt
        e_to_batt = min(max(batt_cap, 0.0), e_reg_step)
        e_batt += e_to_batt
        e_reg_step -= e_to_batt

        regen_captured = e_to_sc + e_to_batt
        regen_lost = e_reg_step

        # treat captured energy as negative grid power (export)
        if regen_captured > 0 and dt_i > 0:
            P_grid_kw = -regen_captured * 3600.0 / dt_i

    # Clamp energies
    e_batt = min(max(e_batt, 0.0), hess.batt_capacity_kwh)
    e_sc = min(max(e_sc, 0.0), hess.sc_capacity_kwh)

    return (
        P_grid_kw * 1000.0,
        P_batt_kw * 1000.0,
        P_sc_kw * 1000.0,
        e_batt,
        e_sc,
        regen_captured,
        regen_lost,
        time_since_departure,
    )


# ---------------- Timeline builder ----------------


def build_timeline(
    train: TrainParams, corridor: CorridorParams, hess: Optional[HESSParams], sim: SimParams
) -> pd.DataFrame:
    segments_m = corridor.segments_m
    speed_limits = corridor.speed_limits_mps
    dwell_s = corridor.dwell_s
    dt_target = sim.dt_target
    run_id = sim.run_id
    mode = sim.mode

    rows: List[Dict[str, Any]] = []
    t0 = 0.0
    x0 = 0.0
    total_d = sum(segments_m)

    # HESS state
    if mode == "hess" and hess is not None:
        e_batt = hess.batt_capacity_kwh * hess.batt_initial_soc
        e_sc = hess.sc_initial_kwh
        regen_captured_total = 0.0
        regen_lost_total = 0.0
    else:
        e_batt = e_sc = regen_captured_total = regen_lost_total = 0.0

    time_since_departure = 0.0
    prev_event = "DWELL"

    for seg_i, (L, vmax) in enumerate(zip(segments_m, speed_limits)):
        t_seg, x_seg, v_seg, phase = segment_profile(
            L, vmax, train.a_acc_mps2, train.a_brk_mps2, dt_target
        )

        t_glob = t0 + t_seg
        x_glob = x0 + x_seg

        a_seg = np.diff(v_seg, prepend=v_seg[0]) / np.diff(
            t_seg, prepend=dt_target
        )

        F_res = davis_force(
            v_seg, train.davis_A, train.davis_B, train.davis_C
        ) * np.sign(v_seg)
        F_cmd = train.mass_kg * a_seg + F_res
        P = F_cmd * v_seg  # W

        dt_local = np.diff(t_glob, prepend=t_glob[0])
        P_pos = np.clip(P, 0, None)
        P_neg = np.clip(P, None, 0)
        E_trac = np.cumsum(P_pos * dt_local)
        E_reg = np.cumsum((-P_neg) * train.regen_eff * dt_local)

        for k in range(len(t_glob)):
            t_i = float(t_glob[k])
            dt_i = float(dt_local[k])
            v_i = float(v_seg[k])
            a_i = float(a_seg[k])
            P_i = float(P[k])
            event_i = phase[k]

            P_trac_kw = max(P_i, 0.0) / 1000.0
            P_brake_kw = max(-P_i, 0.0) / 1000.0

            P_grid_w = P_i
            P_batt_w = 0.0
            P_sc_w = 0.0
            regen_captured_step = 0.0
            regen_lost_step = 0.0

            if mode == "hess":
                (
                    P_grid_w,
                    P_batt_w,
                    P_sc_w,
                    e_batt,
                    e_sc,
                    regen_captured_step,
                    regen_lost_step,
                    time_since_departure,
                ) = apply_hess_step(
                    mode,
                    hess,
                    train,
                    t_i,
                    dt_i,
                    P_trac_kw,
                    P_brake_kw,
                    prev_event,
                    event_i,
                    time_since_departure,
                    e_batt,
                    e_sc,
                )
                regen_captured_total += regen_captured_step
                regen_lost_total += regen_lost_step

            prev_event = event_i

            row = {
                "run_id": run_id,
                "t": t_i,
                "x_m": float(x_glob[k]),
                "segment": seg_i + 1,
                "v_mps": v_i,
                "a_mps2": a_i,
                "limit_mps": float(vmax),
                "power_w": P_i,
                "energy_kwh": float(E_trac[k] / 3.6e6),
                "regen_kwh": float(E_reg[k] / 3.6e6),
                "event": event_i,
                "P_grid_w": P_grid_w,
                "P_batt_w": P_batt_w,
                "P_sc_w": P_sc_w,
                "soc_batt": (e_batt / hess.batt_capacity_kwh) if (mode == "hess" and hess) else 0.0,
                "e_sc_kwh": e_sc if mode == "hess" else 0.0,
                "regen_kwh_captured": regen_captured_total,
                "regen_kwh_lost": regen_lost_total,
            }
            rows.append(row)

        t0 = float(t_glob[-1])
        x0 = float(x_glob[-1])

        if seg_i < len(segments_m) - 1:
            n_dwell = max(1, int(round(dwell_s / dt_target)))
            t_dw = t0 + np.arange(1, n_dwell + 1) * (dwell_s / n_dwell)
            for td in t_dw:
                event_i = "DWELL"
                dt_i = (dwell_s / n_dwell)

                P_grid_w = 0.0
                P_batt_w = 0.0
                P_sc_w = 0.0

                if mode == "hess":
                    (
                        P_grid_w,
                        P_batt_w,
                        P_sc_w,
                        e_batt,
                        e_sc,
                        regen_captured_step,
                        regen_lost_step,
                        time_since_departure,
                    ) = apply_hess_step(
                        mode,
                        hess,
                        train,
                        float(td),
                        dt_i,
                        0.0,
                        0.0,
                        prev_event,
                        event_i,
                        time_since_departure,
                        e_batt,
                        e_sc,
                    )
                    regen_captured_total += regen_captured_step
                    regen_lost_total += regen_lost_step

                prev_event = event_i

                rows.append(
                    {
                        "run_id": run_id,
                        "t": float(td),
                        "x_m": float(x0),
                        "segment": seg_i + 1,
                        "v_mps": 0.0,
                        "a_mps2": 0.0,
                        "limit_mps": float(vmax),
                        "power_w": 0.0,
                        "energy_kwh": float(E_trac[-1] / 3.6e6),
                        "regen_kwh": float(E_reg[-1] / 3.6e6),
                        "event": event_i,
                        "P_grid_w": P_grid_w,
                        "P_batt_w": P_batt_w,
                        "P_sc_w": P_sc_w,
                        "soc_batt": (e_batt / hess.batt_capacity_kwh) if (mode == "hess" and hess) else 0.0,
                        "e_sc_kwh": e_sc if mode == "hess" else 0.0,
                        "regen_kwh_captured": regen_captured_total,
                        "regen_kwh_lost": regen_lost_total,
                    }
                )
            t0 = float(t_dw[-1])

    df = pd.DataFrame(rows)
    df.attrs["total_distance_m"] = total_d
    return df


def compute_kpi(df: pd.DataFrame, run_id: str, total_distance_m: float) -> dict:
    distance_km = total_distance_m / 1000.0

    # mechanical traction and regen (same in both modes)
    kwh_trac = float(df["energy_kwh"].max()) if len(df) else 0.0
    regen_mech_kwh = float(df["regen_kwh"].max()) if len(df) else 0.0

    # electrical energy from grid (can differ with HESS)
    if "P_grid_w" in df.columns:
        dt = df["t"].diff().fillna(0.0)  # seconds
        P_grid_pos = df["P_grid_w"].clip(lower=0.0)
        grid_energy_kwh = float((P_grid_pos * dt).sum() / 3.6e6)
    else:
        grid_energy_kwh = kwh_trac  # fallback: assume all from grid

    # regen captured / lost by HESS (baseline will be zero)
    regen_captured_kwh = float(df.get("regen_kwh_captured", pd.Series([0.0])).iloc[-1])
    regen_lost_kwh = float(df.get("regen_kwh_lost", pd.Series([0.0])).iloc[-1])

    return {
        "run_id": run_id,
        "distance_km": round(distance_km, 3),
        "trip_time_s": round(float(df["t"].max() if len(df) else 0.0), 2),
        # mechanical
        "traction_energy_kwh": round(kwh_trac, 3),
        "traction_kwh_per_km": round(kwh_trac / distance_km, 3) if distance_km > 0 else 0.0,
        "regen_mech_kwh": round(regen_mech_kwh, 3),
        # electrical from grid
        "grid_energy_kwh": round(grid_energy_kwh, 3),
        "grid_kwh_per_km": round(grid_energy_kwh / distance_km, 3) if distance_km > 0 else 0.0,
        # HESS metrics
        "regen_captured_kwh": round(regen_captured_kwh, 3),
        "regen_lost_kwh": round(regen_lost_kwh, 3),
    }



# ---------------- CLI ----------------


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]

    p = argparse.ArgumentParser(description="Batch train simulator with EMS/HESS.")
    p.add_argument(
        "--mode",
        type=str,
        choices=["baseline", "hess"],
        default="baseline",
        help="Simulation mode.",
    )
    p.add_argument(
        "--run-id",
        type=str,
        default="MR90_baseline",
        help="Run identifier to embed in outputs.",
    )
    p.add_argument(
        "--dt",
        type=float,
        default=0.2,
        help="Target time step in seconds.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=repo_root / "config" / "mr90_default.json",
        help="Path to JSON config file.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=repo_root / "simulator" / "outputs",
        help="Output directory.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    train, corridor, hess, sim = build_params(cfg, args.mode, args.run_id, args.dt)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = build_timeline(train, corridor, hess, sim)
    total_d = df.attrs.get("total_distance_m", sum(corridor.segments_m))
    kpi = compute_kpi(df, sim.run_id, total_d)

    timeline_path = args.out_dir / f"timeline_{sim.run_id}.csv"
    kpi_path = args.out_dir / f"kpi_{sim.run_id}.json"

    df.to_csv(timeline_path, index=False)
    with kpi_path.open("w", encoding="utf-8") as f:
        json.dump(kpi, f, indent=2)

    print(f"[run_sim] wrote timeline: {timeline_path}")
    print(f"[run_sim] wrote kpi:      {kpi_path}")
    print(f"[run_sim] kpi: {kpi}")


if __name__ == "__main__":
    main()
