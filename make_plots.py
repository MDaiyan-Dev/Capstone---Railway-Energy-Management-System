import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[0]
OUT = ROOT / "simulator" / "outputs"

# Paths
base_timeline_path = OUT / "timeline_MR90_baseline.csv"
hess_timeline_path = OUT / "timeline_MR90_hess.csv"
base_kpi_path = OUT / "kpi_MR90_baseline.json"
hess_kpi_path = OUT / "kpi_MR90_hess.json"

# Load data
df_base = pd.read_csv(base_timeline_path)
df_hess = pd.read_csv(hess_timeline_path)

with open(base_kpi_path, "r") as f:
    kpi_base = json.load(f)
with open(hess_kpi_path, "r") as f:
    kpi_hess = json.load(f)

# -------------------------
# Figure 1: Grid energy per trip
# -------------------------
labels = ["Baseline", "HESS"]
grid_vals = [
    kpi_base.get("grid_energy_kwh", 0.0),
    kpi_hess.get("grid_energy_kwh", 0.0),
]

plt.figure(figsize=(6, 4))
plt.bar(labels, grid_vals)
plt.ylabel("Grid energy per trip [kWh]")
plt.title("Grid energy comparison per trip")
for i, v in enumerate(grid_vals):
    plt.text(i, v + 0.5, f"{v:.1f}", ha="center", va="bottom")
plt.tight_layout()
plt.savefig(OUT / "fig1_grid_energy.png", dpi=200)
plt.close()

# -------------------------
# Figure 2: Regen captured vs lost (stacked)
# -------------------------
modes = ["Baseline", "HESS"]
captured = [
    kpi_base.get("regen_captured_kwh", 0.0),
    kpi_hess.get("regen_captured_kwh", 0.0),
]
lost = [
    kpi_base.get("regen_lost_kwh", 0.0),
    kpi_hess.get("regen_lost_kwh", 0.0),
]

x = range(len(modes))
plt.figure(figsize=(6, 4))
plt.bar(x, captured, label="Captured")
plt.bar(x, lost, bottom=captured, label="Lost")
plt.xticks(x, modes)
plt.ylabel("Regenerated energy [kWh]")
plt.title("Regenerative energy captured vs lost")
plt.legend()
plt.tight_layout()
plt.savefig(OUT / "fig2_regen_stacked.png", dpi=200)
plt.close()

# -------------------------
# Figure 3: Grid power over time
# -------------------------
# Use positive grid power only and convert to kW
df_base_plot = df_base.copy()
df_hess_plot = df_hess.copy()

df_base_plot["P_grid_kw"] = df_base_plot["P_grid_w"].clip(lower=0.0) / 1000.0
df_hess_plot["P_grid_kw"] = df_hess_plot["P_grid_w"].clip(lower=0.0) / 1000.0

# Downsample to keep lines smooth but not insane
step = max(1, len(df_base_plot) // 2000)
df_base_plot = df_base_plot.iloc[::step, :]
df_hess_plot = df_hess_plot.iloc[::step, :]

plt.figure(figsize=(8, 4))
plt.plot(df_base_plot["t"], df_base_plot["P_grid_kw"], label="Baseline")
plt.plot(df_hess_plot["t"], df_hess_plot["P_grid_kw"], label="HESS", linestyle="--")
plt.xlabel("Time [s]")
plt.ylabel("Grid power [kW]")
plt.title("Grid power profile over trip")
plt.legend()
plt.tight_layout()
plt.savefig(OUT / "fig3_grid_power_vs_time.png", dpi=200)
plt.close()

# -------------------------
# Figure 4: Storage behaviour over time (HESS)
# -------------------------
df_hess_soc = df_hess.copy()
t = df_hess_soc["t"]

soc = df_hess_soc["soc_batt"] * 100.0  # percent
e_sc = df_hess_soc["e_sc_kwh"]         # kWh

fig, ax1 = plt.subplots(figsize=(8, 4))

ax1.plot(t, soc, label="Battery SoC", color="tab:blue")
ax1.set_xlabel("Time [s]")
ax1.set_ylabel("Battery SoC [%]", color="tab:blue")
ax1.tick_params(axis="y", labelcolor="tab:blue")

ax2 = ax1.twinx()
ax2.plot(t, e_sc, label="Supercap energy", color="tab:orange")
ax2.set_ylabel("Supercap energy [kWh]", color="tab:orange")
ax2.tick_params(axis="y", labelcolor="tab:orange")

plt.title("Storage behaviour over trip")
fig.tight_layout()
plt.savefig(OUT / "fig4_storage_behaviour.png", dpi=200)
plt.close()

print("Wrote figures to:", OUT)
