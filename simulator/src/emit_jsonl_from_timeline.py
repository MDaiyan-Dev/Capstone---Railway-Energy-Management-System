#!/usr/bin/env python3
"""
Emit JSONL telemetry files from the week 6 timeline CSV.

Inputs:
  simulator/src/Demo/week6_outputs/timeline_w6.csv

Outputs (relative to repo root):
  bus/in/telemetry.train.state.v1.jsonl
  bus/in/telemetry.energy.sample.v1.jsonl
  bus/in/telemetry.event.stop.v1.jsonl

Assumptions:
  - timeline_w6.csv has columns:
      run_id,t,x_m,segment,v_mps,a_mps2,limit_mps,power_w,energy_kwh,regen_kwh,event
  - Each contiguous DWELL block corresponds to one station stop.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# --- Paths --------------------------------------------------------------------


THIS_FILE = Path(__file__).resolve()
# Repo root: Capstone---Railway-Energy-Management-System/
REPO_ROOT = THIS_FILE.parents[2]

TIMELINE_CSV = REPO_ROOT / "simulator" / "src" / "Demo" / "week6_outputs" / "timeline_w6.csv"
BUS_IN_DIR = REPO_ROOT / "bus" / "in"

STATE_JSONL = BUS_IN_DIR / "telemetry.train.state.v1.jsonl"
ENERGY_JSONL = BUS_IN_DIR / "telemetry.energy.sample.v1.jsonl"
STOP_JSONL = BUS_IN_DIR / "telemetry.event.stop.v1.jsonl"


# --- Types --------------------------------------------------------------------


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


# --- Core logic ----------------------------------------------------------------


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
    """
    Derive stop events from DWELL blocks.

    Each contiguous block of DWELL rows is treated as one station stop:
      - station_index: 1, 2, ...
      - scheduled_ts: equal to actual_ts for now (no schedule deviation model yet)
      - actual_ts: first t_s of the DWELL block
      - dwell_s: last_t_s - first_t_s of the block
    """
    BUS_IN_DIR.mkdir(parents=True, exist_ok=True)

    station_index = 0
    in_dwell = False
    dwell_start_t: Optional[float] = None
    last_t: Optional[float] = None
    run_id: Optional[str] = None

    events: List[dict] = []

    for r in rows:
        run_id = r.run_id  # same for all rows in week 6
        is_dwell = (r.event == "DWELL")

        if is_dwell and not in_dwell:
            # Entering a dwell block
            in_dwell = True
            dwell_start_t = r.t_s
            last_t = r.t_s
        elif is_dwell and in_dwell:
            # Continuing dwell
            last_t = r.t_s
        elif (not is_dwell) and in_dwell:
            # Exiting a dwell block -> finalize station
            in_dwell = False
            if dwell_start_t is not None and last_t is not None:
                station_index += 1
                dwell_s = max(0.0, last_t - dwell_start_t)
                evt = {
                    "run_id": run_id,
                    "station_index": station_index,
                    "scheduled_ts": dwell_start_t,  # same as actual for now
                    "actual_ts": dwell_start_t,
                    "dwell_s": dwell_s,
                }
                events.append(evt)
            dwell_start_t = None
            last_t = None
        else:
            # Not dwell, not exiting dwell; nothing special
            pass

    # Handle case where file ends during DWELL
    if in_dwell and dwell_start_t is not None and last_t is not None:
        station_index += 1
        dwell_s = max(0.0, last_t - dwell_start_t)
        evt = {
            "run_id": run_id,
            "station_index": station_index,
            "scheduled_ts": dwell_start_t,
            "actual_ts": dwell_start_t,
            "dwell_s": dwell_s,
        }
        events.append(evt)

    with STOP_JSONL.open("w", encoding="utf-8") as f_stop:
        for evt in events:
            f_stop.write(json.dumps(evt) + "\n")


def main() -> None:
    if not TIMELINE_CSV.exists():
        raise FileNotFoundError(f"Timeline CSV not found at {TIMELINE_CSV}")

    print(f"Reading timeline from {TIMELINE_CSV}")
    rows = read_timeline(TIMELINE_CSV)
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
