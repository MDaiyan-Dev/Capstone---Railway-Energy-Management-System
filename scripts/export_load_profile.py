#!/usr/bin/env python
"""Export a lightweight traction load profile CSV from a simulator timeline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a time-series traction load profile for one simulator run."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Run ID used to locate simulator/outputs/timeline_<run_id>.csv.",
    )
    parser.add_argument(
        "--out",
        help="Optional output path. Defaults to simulator/outputs/load_profile_<run_id>.csv.",
    )
    parser.add_argument(
        "--decimate",
        type=int,
        default=1,
        help="Write every Nth row. Default is 1, which writes all rows.",
    )
    return parser.parse_args()


def export_load_profile(run_id: str, out_path: Path | None, decimate: int) -> Path:
    if decimate < 1:
        raise ValueError("--decimate must be at least 1")

    outputs_dir = Path("simulator/outputs")
    timeline_path = outputs_dir / f"timeline_{run_id}.csv"
    if not timeline_path.exists():
        raise FileNotFoundError(f"Missing timeline file: {timeline_path}")
    power_cap_w = read_power_cap_w(outputs_dir=outputs_dir, run_id=run_id)

    if out_path is None:
        out_path = outputs_dir / f"load_profile_{run_id}.csv"

    max_load = None
    max_t = None
    rows_written = 0

    with timeline_path.open("r", newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        required_cols = {"t", "power_w"}
        missing = [col for col in required_cols if col not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{timeline_path} missing required columns: {missing}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(["t_s", "traction_load_w"])

            for idx, row in enumerate(reader):
                power_w = float(row["power_w"])
                if power_w > power_cap_w + 1e-6:
                    raise ValueError(
                        "Source timeline exceeds train.P_max_w: "
                        f"run_id={run_id}, t={row['t']}, power_w={power_w:.12g}, "
                        f"P_max_w={power_cap_w:.12g}"
                    )

                if idx % decimate != 0:
                    continue

                t_s = float(row["t"])
                traction_load_w = max(power_w, 0.0)
                writer.writerow([f"{t_s:.12g}", f"{traction_load_w:.12g}"])

                rows_written += 1
                if max_load is None or traction_load_w > max_load:
                    max_load = traction_load_w
                    max_t = t_s

    if rows_written == 0:
        raise ValueError(f"No data rows written from {timeline_path}")

    print(f"max_traction_load_w={max_load:.12g}, t_s={max_t:.12g}")
    print(f"rows_written={rows_written}")
    print(f"wrote: {out_path}")
    return out_path


def main() -> None:
    args = parse_args()
    out_path = Path(args.out) if args.out else None
    export_load_profile(run_id=args.run_id, out_path=out_path, decimate=args.decimate)


if __name__ == "__main__":
    main()
