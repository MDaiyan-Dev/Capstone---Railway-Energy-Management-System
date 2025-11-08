# week6_sim_demo.py — Week 6 V1 (vectorized, no loops that can hang)
# Single train • 4 stations (3 segments) • 20 s dwells • Analytic phases
# Outputs: week6_outputs/timeline_w6.csv, kpi_w6.json, and plots if matplotlib is present.

import json, math
from pathlib import Path

import numpy as np
import pandas as pd

# Optional plotting (skipped if not installed)
try:
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False

OUT = Path("./week6_outputs"); OUT.mkdir(parents=True, exist_ok=True)

# ---------------- Parameters (V1) ----------------
run_id = "W6_vector_01"
dt_target = 0.2                # we’ll approximate this with clean arrays
dwell_s = 20.0                 # at intermediate stations

# Train & limits (fixed for W6)
mass = 180_000.0               # kg (not used in analytic kinematics, kept for energy force sign)
a_acc = 0.60                   # m/s^2  accel
a_brk = 0.80                   # m/s^2  service brake
P_max = 3.0e6                  # W (for info; we’ll compute power from kinematics)
F_max = 150e3                  # N  (for info)
F_brk_max = 180e3              # N  (for info)
regen_eff = 0.35               # recovered fraction during braking

# Simple resistance for power sign (not used for kinematics)
A, B, C = 3000.0, 30.0, 0.6    # N, N·s/m, N·s^2/m^2
def davis(v): return A + B*v + C*v*v

# Corridor
segments_m   = [1200.0, 1600.0, 1400.0]   # 3 segments (4 stations total)
speed_limits = [25.0,   22.0,   20.0]     # m/s
station_d = [0.0]
for L in segments_m:
    station_d.append(station_d[-1] + L)
total_d = station_d[-1]

# --------------- Analytic phase builder ---------------
def segment_profile(L, vmax, a_acc, a_brk):
    """Return piecewise time, pos, vel arrays for one segment (start v0=0, end v=0)."""
    # Ideal distances to accel/brake to/from vmax
    d_acc = (vmax*vmax)/(2*a_acc)
    d_brk = (vmax*vmax)/(2*a_brk)

    if d_acc + d_brk <= L:
        # Trapezoidal: ACCEL -> CRUISE -> BRAKE
        d_cruise = L - (d_acc + d_brk)
        t_acc    = vmax / a_acc
        t_brk    = vmax / a_brk
        t_cruise = d_cruise / vmax

        # sample counts per phase (aim near dt_target, min 5 samples per phase)
        n_acc    = max(5, int(round(t_acc / dt_target)))
        n_cruise = max(5, int(round(t_cruise / dt_target))) if t_cruise > 0 else 0
        n_brk    = max(5, int(round(t_brk / dt_target)))

        # time vectors
        t_acc_v    = np.linspace(0, t_acc,    n_acc, endpoint=False)
        t_cruise_v = np.linspace(0, t_cruise, n_cruise, endpoint=False) if n_cruise>0 else np.array([])
        t_brk_v    = np.linspace(0, t_brk,    n_brk)

        # kinematics
        v_acc = a_acc * t_acc_v
        x_acc = 0.5 * a_acc * t_acc_v**2

        v_cruise = vmax * np.ones_like(t_cruise_v)
        x_cruise = d_acc + vmax * t_cruise_v

        v_brk = vmax - a_brk * t_brk_v
        v_brk = np.clip(v_brk, 0.0, None)
        x_brk = d_acc + d_cruise + (vmax * t_brk_v - 0.5 * a_brk * t_brk_v**2)

        # stitch
        t_seg = np.concatenate([t_acc_v, t_acc + t_cruise_v, t_acc + t_cruise + t_brk_v])
        v_seg = np.concatenate([v_acc, v_cruise, v_brk])
        x_seg = np.concatenate([x_acc, x_cruise, x_brk])
        phase = (["ACCEL"] * len(t_acc_v) +
                 ["CRUISE"] * len(t_cruise_v) +
                 ["BRAKE"] * len(t_brk_v))
        return t_seg, x_seg, v_seg, phase
    else:
        # Triangular: accelerate to v_peak then brake immediately
        v_peak = math.sqrt(L * 2.0 / (1.0/a_acc + 1.0/a_brk))
        t_acc  = v_peak / a_acc
        t_brk  = v_peak / a_brk
        d_acc  = (v_peak*v_peak)/(2*a_acc)  # equals L - d_brk
        # samples
        n_acc  = max(5, int(round(t_acc / dt_target)))
        n_brk  = max(5, int(round(t_brk / dt_target)))

        t_acc_v = np.linspace(0, t_acc, n_acc, endpoint=False)
        t_brk_v = np.linspace(0, t_brk, n_brk)

        v_acc = a_acc * t_acc_v
        x_acc = 0.5 * a_acc * t_acc_v**2

        v_brk = v_peak - a_brk * t_brk_v
        v_brk = np.clip(v_brk, 0.0, None)
        x_brk = d_acc + (v_peak * t_brk_v - 0.5 * a_brk * t_brk_v**2)

        t_seg = np.concatenate([t_acc_v, t_acc + t_brk_v])
        v_seg = np.concatenate([v_acc, v_brk])
        x_seg = np.concatenate([x_acc, x_brk])
        phase = (["ACCEL"] * len(t_acc_v) + ["BRAKE"] * len(t_brk_v))
        return t_seg, x_seg, v_seg, phase

# --------------- Build full run ---------------
rows = []
t0 = 0.0
x0 = 0.0
for seg_i, (L, vmax) in enumerate(zip(segments_m, speed_limits)):
    # segment profile (local time/pos)
    t_seg, x_seg, v_seg, phase = segment_profile(L, vmax, a_acc, a_brk)

    # shift to global time/pos
    t_glob = t0 + t_seg
    x_glob = x0 + x_seg

    # approximate acceleration from finite diff (prepend 0)
    a_seg = np.diff(v_seg, prepend=v_seg[0]) / np.diff(t_seg, prepend=dt_target)
    # power (traction + resist; negative → braking)
    F_res = davis(v_seg) * np.sign(v_seg)  # simple approximation
    F_cmd = mass * a_seg + F_res
    P = F_cmd * v_seg

    # cumulative energy
    # integrate with trapezoid on power; split traction vs regen
    dt_local = np.diff(t_glob, prepend=t_glob[0])
    P_pos = np.clip(P, 0, None)
    P_neg = np.clip(P, None, 0)
    E_trac = np.cumsum(P_pos * dt_local)      # J
    E_reg  = np.cumsum((-P_neg) * regen_eff * dt_local)  # J recovered

    for k in range(len(t_glob)):
        rows.append({
            "run_id": run_id,
            "t": float(t_glob[k]),
            "x_m": float(x_glob[k]),
            "segment": seg_i+1,
            "v_mps": float(v_seg[k]),
            "a_mps2": float(a_seg[k]),
            "limit_mps": float(vmax),
            "power_w": float(P[k]),
            "energy_kwh": float(E_trac[k]/3.6e6),
            "regen_kwh": float(E_reg[k]/3.6e6),
            "event": phase[k]
        })

    # snap to station and dwell (except terminal)
    t0 = float(t_glob[-1])
    x0 = float(x_glob[-1])
    if seg_i < len(segments_m)-1:
        n_dwell = max(1, int(round(dwell_s / dt_target)))
        t_dw = t0 + np.arange(1, n_dwell+1) * (dwell_s / n_dwell)
        for td in t_dw:
            rows.append({
                "run_id": run_id,
                "t": float(td),
                "x_m": float(x0),
                "segment": seg_i+1,
                "v_mps": 0.0,
                "a_mps2": 0.0,
                "limit_mps": float(vmax),
                "power_w": 0.0,
                "energy_kwh": float(E_trac[-1]/3.6e6),
                "regen_kwh": float(E_reg[-1]/3.6e6),
                "event": "DWELL"
            })
        t0 = float(t_dw[-1])

# --------------- Outputs ---------------
df = pd.DataFrame(rows)
OUT.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT/"timeline_w6.csv", index=False)

distance_km = total_d/1000.0
kwh = float(df["energy_kwh"].max()) if len(df) else 0.0
kpi = {
    "run_id": run_id,
    "distance_km": round(distance_km, 3),
    "trip_time_s": round(float(df["t"].max() if len(df) else 0.0), 2),
    "energy_kwh": round(kwh, 3),
    "kwh_per_km": round(kwh / distance_km, 3) if distance_km>0 else 0.0,
    "regen_kwh": round(float(df["regen_kwh"].max() if len(df) else 0.0), 3)
}
with open(OUT/"kpi_w6.json","w") as f:
    json.dump(kpi, f, indent=2)

if HAVE_MPL and len(df):
    plt.figure(figsize=(8,4)); plt.plot(df["t"], df["v_mps"])
    plt.xlabel("Time (s)"); plt.ylabel("Speed (m/s)"); plt.title("Speed vs Time (Week 6)")
    plt.tight_layout(); plt.savefig(OUT/"speed_time_w6.png", dpi=150); plt.close()

    plt.figure(figsize=(8,4)); plt.plot(df["t"], df["energy_kwh"])
    plt.xlabel("Time (s)"); plt.ylabel("Cumulative Energy (kWh)"); plt.title("Cumulative Energy (Week 6)")
    plt.tight_layout(); plt.savefig(OUT/"energy_cum_w6.png", dpi=150); plt.close()
else:
    print("Plotting skipped or matplotlib not installed.")

print("[done] saved to:", OUT.resolve())
print("[kpi]", kpi)
