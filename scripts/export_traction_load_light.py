#!/usr/bin/env python
"""Export lightweight traction-load CSVs from simulator timeline outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_RUN_IDS = ["MR90_baseline", "MR90_hess"]


def export_run(run_id: str, outputs_dir: Path) -> None:
    timeline_path = outputs_dir / f"timeline_{run_id}.csv"
    if not timeline_path.exists():
        raise FileNotFoundError(f"Missing timeline file: {timeline_path}")

    df = pd.read_csv(timeline_path)

    required_cols = ["t", "power_w", "energy_kwh"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"{timeline_path} missing required columns: {missing}")

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
