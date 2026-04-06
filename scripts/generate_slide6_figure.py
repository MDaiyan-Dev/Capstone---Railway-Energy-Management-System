#!/usr/bin/env python3
"""Generate a clean comparison figure for Slide 6 from KPI JSON outputs."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "simulator" / "outputs"
BASELINE_KPI = OUTPUT_DIR / "kpi_MR90_baseline.json"
HESS_KPI = OUTPUT_DIR / "kpi_MR90_hess.json"
OUT_PNG = OUTPUT_DIR / "slide6_simulator_comparison.png"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as infile:
        data = json.load(infile)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def main() -> None:
    baseline = load_json(BASELINE_KPI)
    hess = load_json(HESS_KPI)

    grid_energy = [
        float(baseline["grid_energy_kwh"]),
        float(hess["grid_energy_kwh"]),
    ]
    regen_hess = [
        float(hess["regen_captured_kwh"]),
        float(hess["regen_lost_kwh"]),
    ]

    plt.style.use("default")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), dpi=200)
    fig.patch.set_facecolor("white")

    colors = ["#4c78a8", "#72b7b2"]

    ax = axes[0]
    bars = ax.bar(["Baseline", "HESS"], grid_energy, color=colors, width=0.6)
    ax.set_title("Grid Energy", fontsize=12)
    ax.set_ylabel("kWh", fontsize=10)
    ax.tick_params(labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, grid_energy):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            value,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax = axes[1]
    bars = ax.bar(["Captured", "Lost"], regen_hess, color=colors, width=0.6)
    ax.set_title("HESS Regeneration", fontsize=12)
    ax.set_ylabel("kWh", fontsize=10)
    ax.tick_params(labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, regen_hess):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            value,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    fig.suptitle("Simulator KPI Comparison", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT_PNG, bbox_inches="tight")
    plt.close(fig)

    print(f"Wrote: {OUT_PNG}")


if __name__ == "__main__":
    main()
