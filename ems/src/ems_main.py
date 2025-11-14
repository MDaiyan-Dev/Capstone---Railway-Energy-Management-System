#!/usr/bin/env python3
"""
EMS V1 (batch mode) for Capstone Railway EMS.

Reads simulator telemetry from bus/in JSONL files and produces a command
timeline in bus/out/ems.command.v1.jsonl using V1 policies:

  - eco_coast_1
  - catchup_cancel_coast
  - peak_shave_1
  - baseline_follow

This is an offline / advisory EMS: it runs over a completed run (e.g. week6)
and shows what commands it would have sent at each tick.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple
import math

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]  # ems/src -> ems -> root
BUS_IN = REPO_ROOT / "bus" / "in"
BUS_OUT = REPO_ROOT / "bus" / "out"

STATE_FILE = BUS_IN / "telemetry.train.state.v1.jsonl"
ENERGY_FILE = BUS_IN / "telemetry.energy.sample.v1.jsonl"
CMD_FILE = BUS_OUT / "ems.command.v1.jsonl"

BUS_OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Parameters (match your spec)
# ---------------------------------------------------------------------------

TICK_S = 0.2

CRUISE_COAST_FRAC = (0.30, 0.50)    # 30–50 percent of corridor
ECO_CRUISE_RATIO = 0.90             # 90 percent of speed limit

LATE_THRESHOLD_S = 5.0              # lateness threshold for catchup rule

PEAK_POWER_THRESHOLD_W = 2.5e6      # 2.5 MW threshold
PEAK_POWER_WINDOW_S = 5.0           # 5 s rolling window

# hard-coded corridor length for week6 (4.2 km); read from data if you want
DEFAULT_CORRIDOR_M = 4200.0


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class TrainState:
    run_id: str
    t_s: float
    segment: int
    x_m: float
    v_mps: float
    limit_mps: float


@dataclass
class EnergySample:
    run_id: str
    t_s: float
    power_w: float
    energy_kwh: float
    regen_kwh: float


@dataclass
class Command:
    run_id: str
    t_s: float
    target_speed_mps: float
    rationale: str
    mode: str = "speed"
    valid_for_s: float = 1.0


# ---------------------------------------------------------------------------
# Load telemetry
# ---------------------------------------------------------------------------

def load_state_samples() -> List[TrainState]:
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"Missing {STATE_FILE}")
    out: List[TrainState] = []
    with STATE_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(
                TrainState(
                    run_id=obj["run_id"],
                    t_s=float(obj["t_s"]),
                    segment=int(obj["segment"]),
                    x_m=float(obj["x_m"]),
                    v_mps=float(obj["v_mps"]),
                    limit_mps=float(obj["limit_mps"]),
                )
            )
    out.sort(key=lambda s: s.t_s)
    return out


def load_energy_samples() -> List[EnergySample]:
    if not ENERGY_FILE.exists():
        raise FileNotFoundError(f"Missing {ENERGY_FILE}")
    out: List[EnergySample] = []
    with ENERGY_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(
                EnergySample(
                    run_id=obj["run_id"],
                    t_s=float(obj["t_s"]),
                    power_w=float(obj["power_w"]),
                    energy_kwh=float(obj["energy_kwh"]),
                    regen_kwh=float(obj["regen_kwh"]),
                )
            )
    out.sort(key=lambda e: e.t_s)
    return out


def align_telemetry(states: List[TrainState],
                    energies: List[EnergySample]) -> List[Tuple[TrainState, EnergySample]]:
    """Assume one energy sample per tick; match by index, fallback by nearest time."""
    if len(states) == len(energies):
        return list(zip(states, energies))

    # fallback: nearest neighbor by time
    result: List[Tuple[TrainState, EnergySample]] = []
    j = 0
    for s in states:
        while j + 1 < len(energies) and abs(energies[j+1].t_s - s.t_s) < abs(energies[j].t_s - s.t_s):
            j += 1
        result.append((s, energies[j]))
    return result


# ---------------------------------------------------------------------------
# EMS logic (V1 policies)
# ---------------------------------------------------------------------------

class EMSBatch:
    def __init__(self, aligned: List[Tuple[TrainState, EnergySample]]):
        self.aligned = aligned
        self.corridor_m = self._infer_corridor_length()
        self.trip_end_t = aligned[-1][0].t_s if aligned else 0.0
        self.power_window: List[EnergySample] = []
        self.rationale_counts: Dict[str, int] = {}

    def _infer_corridor_length(self) -> float:
        if not self.aligned:
            return DEFAULT_CORRIDOR_M
        max_x = max(s.x_m for (s, _) in self.aligned)
        return max(max_x, DEFAULT_CORRIDOR_M * 0.5)

    def update_power_window(self, e: EnergySample):
        self.power_window.append(e)
        cutoff = e.t_s - PEAK_POWER_WINDOW_S
        self.power_window = [p for p in self.power_window if p.t_s >= cutoff]

    def compute_eta(self, s: TrainState) -> float:
        """Very crude ETA: distance remaining / max(current speed, small)."""
        remaining = self.corridor_m - s.x_m
        if remaining <= 0:
            return 0.0
        v = max(s.v_mps, 0.5)  # avoid div by 0, assume minimum crawl
        return remaining / v

    def decide(self, s: TrainState, e: EnergySample) -> Command:
        run_id = s.run_id

        # Update rolling power window
        self.update_power_window(e)

        # 1. Catchup rule: if ETA > threshold, cancel coasting and go full limit
        eta = self.compute_eta(s)
        if eta > LATE_THRESHOLD_S:
            cmd = Command(run_id, s.t_s, s.limit_mps, "catchup_cancel_coast")
            self._bump("catchup_cancel_coast")
            return cmd

        # 2. Peak shave rule: if average power in window above threshold, trim speed
        if self.power_window:
            avg_power = sum(p.power_w for p in self.power_window) / len(self.power_window)
        else:
            avg_power = 0.0

        if avg_power > PEAK_POWER_THRESHOLD_W:
            target = max(0.0, s.v_mps - 1.0)
            cmd = Command(run_id, s.t_s, target, "peak_shave_1")
            self._bump("peak_shave_1")
            return cmd

        # 3. Eco coasting rule: between 30–50% of corridor, cap at 90% of limit
        frac = s.x_m / self.corridor_m if self.corridor_m > 0 else 0.0
        if CRUISE_COAST_FRAC[0] <= frac <= CRUISE_COAST_FRAC[1]:
            target = s.limit_mps * ECO_CRUISE_RATIO
            cmd = Command(run_id, s.t_s, target, "eco_coast_1")
            self._bump("eco_coast_1")
            return cmd

        # 4. Baseline follow
        cmd = Command(run_id, s.t_s, s.limit_mps, "baseline_follow")
        self._bump("baseline_follow")
        return cmd

    def _bump(self, name: str):
        self.rationale_counts[name] = self.rationale_counts.get(name, 0) + 1


# ---------------------------------------------------------------------------
# Run EMS in batch over one trajectory
# ---------------------------------------------------------------------------

def run_batch():
    print("[EMS] Loading telemetry from bus/in…")
    states = load_state_samples()
    energies = load_energy_samples()
    aligned = align_telemetry(states, energies)
    if not aligned:
        print("[EMS] No aligned samples; aborting.")
        return

    ems = EMSBatch(aligned)

    # Clean previous commands
    if CMD_FILE.exists():
        CMD_FILE.unlink()

    print(f"[EMS] Running batch over {len(aligned)} ticks "
          f"(corridor ~{ems.corridor_m:.1f} m, trip {ems.trip_end_t:.1f} s)…")

    with CMD_FILE.open("w", encoding="utf-8") as f:
        for s, e in aligned:
            cmd = ems.decide(s, e)
            obj = {
                "run_id": cmd.run_id,
                "t_s": cmd.t_s,
                "mode": cmd.mode,
                "target_speed_mps": cmd.target_speed_mps,
                "rationale": cmd.rationale,
                "valid_for_s": cmd.valid_for_s,
            }
            f.write(json.dumps(obj) + "\n")

    print(f"[EMS] Wrote commands to {CMD_FILE}")
    print("[EMS] Rationale counts:")
    for k, v in sorted(ems.rationale_counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    run_batch()
