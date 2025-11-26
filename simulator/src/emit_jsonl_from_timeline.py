#!/usr/bin/env python3
"""
Emit JSONL telemetry files from a timeline CSV.

Default input:
  simulator/outputs/timeline_*.csv  (configurable via --timeline)

Outputs (relative to repo root):
  bus/in/telemetry.train.state.v1.jsonl
  bus/in/telemetry.energy.sample.v1.jsonl
  bus/in/telemetry.event.stop.v1.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

BUS_IN_DIR = REPO_ROOT / "bus" / "in"
STATE_JSONL = BUS_IN_DIR / "telemetry.train.state.v1.jsonl"
ENERGY_JSONL = BUS_IN_DIR / "telemetry.energy.sample.v1.jsonl"
STOP_JSONL = BUS_IN_DIR / "telemetry.event.stop.v1.jsonl"


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
    event: str


def read_timeline(csv_path: Path) -> List[TimelineRow]:
    rows: List[TimelineRow] = []
    with csv_path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(
                TimelineRow(
                    run_id=raw["run_id"],
                    t_s=float(raw["t"]),
                    x_m=float(raw["x_m"]),
                    segment=int(raw["segment"]),
                    v_mps=float(raw["v_mps"]),
                    a_mps2=float(raw["a_mps2"]),
                    limit_mps=float(raw["limit_mps"]),
                    power_w=float(raw["power_w"]),
                    energy_kwh=float(raw["energy_kwh"]),
                    regen_kwh=float(raw["regen_kwh"]),
                    event=str(raw["event"]).strip().upper(),
                )
            )
    if not rows:
        raise RuntimeError(f"No rows read from {csv_path}")
    return rows


def emit_state_and_energy(rows: List[TimelineRow]) -> None:
    BUS_IN_DIR.mkdir(parents=True, exist_ok=True)

    with STATE_JSONL.open("w", encoding="utf-8") as f_state, \
         ENERGY_JSONL.open("w", encoding="utf-8") as f_energy:

        for r in rows:
            state = {
                "run_id": r.run_id,
                "t_s": r.t_s,
                "segment": r.segment,
                "x_m": r.x_m,
                "v_mps": r.v_mps,
                "a_mps2": r.a_mps2,
                "limit_mps": r.limit_mps,
            }
            energy = {
                "run_id": r.run_id,
                "t_s": r.t_s,
                "power_w": r.power_w,
                "energy_kwh": r.energy_kwh,
                "regen_kwh": r.regen_kwh,
            }

            f_state.write(json.dumps(state) + "\n")
            f_energy.write(json.dumps(energy) + "\n")


def emit_stop_events(rows: List[TimelineRow]) -> None:
    BUS_IN_DIR.mkdir(parents=True, exist_ok=True)

    station_index = 0
    in_dwell = False
    dwell_start_t: Optional[float] = None
    last_t: Optional[float] = None
    run_id: Optional[str] = None

    events: List[dict] = []

    for r in rows:
        run_id = r.run_id
        is_dwell = (r.event == "DWELL")

        if is_dwell and not in_dwell:
            in_dwell = True
            dwell_start_t = r.t_s
            last_t = r.t_s
        elif is_dwell and in_dwell:
            last_t = r.t_s
        elif (not is_dwell) and in_dwell:
            in_dwell = False
            if dwell_start_t is not None and last_t is not None:
                station_index += 1
                dwell_s = max(0.0, last_t - dwell_start_t)
                events.append(
                    {
                        "run_id": run_id,
                        "station_index": station_index,
                        "scheduled_ts": dwell_start_t,
                        "actual_ts": dwell_start_t,
                        "dwell_s": dwell_s,
                    }
                )
            dwell_start_t = None
            last_t = None

    if in_dwell and dwell_start_t is not None and last_t is not None:
        station_index += 1
        dwell_s = max(0.0, last_t - dwell_start_t)
        events.append(
            {
                "run_id": run_id,
                "station_index": station_index,
                "scheduled_ts": dwell_start_t,
                "actual_ts": dwell_start_t,
                "dwell_s": dwell_s,
            }
        )

    with STOP_JSONL.open("w", encoding="utf-8") as f_stop:
        for evt in events:
            f_stop.write(json.dumps(evt) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit telemetry JSONL from a simulator timeline CSV."
    )
    default_timeline = REPO_ROOT / "simulator" / "outputs" / "timeline_W6_vector_01.csv"
    parser.add_argument(
        "--timeline",
        type=Path,
        default=default_timeline,
        help="Path to timeline CSV (default: simulator/outputs/timeline_W6_vector_01.csv).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timeline_csv = args.timeline

    if not timeline_csv.exists():
        raise FileNotFoundError(f"Timeline CSV not found at {timeline_csv}")

    print(f"Reading timeline from {timeline_csv}")
    rows = read_timeline(timeline_csv)
    print(f"Read {len(rows)} samples for run_id={rows[0].run_id}")

    print("Emitting state and energy JSONL...")
    emit_state_and_energy(rows)
    print(f"  -> {STATE_JSONL}")
    print(f"  -> {ENERGY_JSONL}")

    print("Emitting stop events JSONL...")
    emit_stop_events(rows)
    print(f"  -> {STOP_JSONL}")

    print("Done.")


if __name__ == "__main__":
    main()
