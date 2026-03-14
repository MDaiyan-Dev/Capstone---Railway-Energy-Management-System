#!/usr/bin/env python
"""Export lightweight traction-load CSVs from simulator timeline outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_RUN_IDS = ["MR90_baseline", "MR90_hess"]


def read_power_cap_w(outputs_dir: Path, run_id: str) -> float:
    config_path = outputs_dir / f"config_{run_id}.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing config file for power-cap validation: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as infile:
        cfg = json.load(infile)

    try:
        return float(cfg["train"]["P_max_w"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Could not read train.P_max_w from config file: {config_path}"
        ) from exc


def export_run(run_id: str, outputs_dir: Path) -> None:
    timeline_path = outputs_dir / f"timeline_{run_id}.csv"
    if not timeline_path.exists():
        raise FileNotFoundError(f"Missing timeline file: {timeline_path}")

    df = pd.read_csv(timeline_path)
    power_cap_w = read_power_cap_w(outputs_dir=outputs_dir, run_id=run_id)

    required_cols = ["t", "power_w", "energy_kwh"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"{timeline_path} missing required columns: {missing}")

    over_cap = df["power_w"] > (power_cap_w + 1e-6)
    if over_cap.any():
        first_bad = df.loc[over_cap, ["t", "power_w"]].iloc[0]
        raise ValueError(
            "Source timeline exceeds train.P_max_w: "
            f"run_id={run_id}, t={first_bad['t']:.12g}, "
            f"power_w={first_bad['power_w']:.12g}, P_max_w={power_cap_w:.12g}"
        )

    out_df = pd.DataFrame(
        {
            "t_s": df["t"],
            "traction_load_w": df["power_w"].clip(lower=0),
            "energy_kwh": df["energy_kwh"],
        }
    )

    out_path = outputs_dir / f"traction_load_{run_id}_light.csv"
    out_df.to_csv(out_path, index=False)

    max_idx = out_df["traction_load_w"].idxmax()
    max_val = float(out_df.loc[max_idx, "traction_load_w"])
    max_t = float(out_df.loc[max_idx, "t_s"])
    print(f"{run_id}: max_traction_load_w={max_val:.6f}, t_s={max_t:.6f}")
    print(f"wrote: {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export lightweight traction-load CSVs from simulator outputs."
    )
    parser.add_argument(
        "--run-ids",
        nargs=2,
        metavar=("BASELINE_RUN_ID", "HESS_RUN_ID"),
        default=DEFAULT_RUN_IDS,
        help="Two run IDs to export, in order baseline hess.",
    )
    parser.add_argument(
        "--outputs-dir",
        default="simulator/outputs",
        help="Directory containing timeline_<run_id>.csv files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs_dir = Path(args.outputs_dir)
    for run_id in args.run_ids:
        export_run(run_id=run_id, outputs_dir=outputs_dir)


if __name__ == "__main__":
    main()
