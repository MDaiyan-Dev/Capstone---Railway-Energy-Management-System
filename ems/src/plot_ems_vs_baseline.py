#!/usr/bin/env python3
"""
Plot baseline train speed vs EMS target speed over time.

Inputs:
  bus/in/telemetry.train.state.v1.jsonl
  bus/out/ems.command.v1.jsonl

Output:
  ems_speed_profile.png in the repo root (or same folder as this script).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple

import matplotlib.pyplot as plt


# Paths relative to this file
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]  # ems/src -> ems -> root

BUS_IN = REPO_ROOT / "bus" / "in"
BUS_OUT = REPO_ROOT / "bus" / "out"

STATE_FILE = BUS_IN / "telemetry.train.state.v1.jsonl"
CMD_FILE = BUS_OUT / "ems.command.v1.jsonl"

OUT_PNG = REPO_ROOT / "ems_speed_profile.png"


@dataclass
class StateSample:
  t_s: float
  v_mps: float


@dataclass
class CommandSample:
  t_s: float
  target_speed_mps: float
  rationale: str


def load_states() -> List[StateSample]:
  if not STATE_FILE.exists():
    raise FileNotFoundError(f"Missing state file: {STATE_FILE}")
  out: List[StateSample] = []
  with STATE_FILE.open("r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      if not line:
        continue
      obj = json.loads(line)
      out.append(
        StateSample(
          t_s=float(obj["t_s"]),
          v_mps=float(obj["v_mps"]),
        )
      )
  out.sort(key=lambda s: s.t_s)
  return out


def load_commands() -> List[CommandSample]:
  if not CMD_FILE.exists():
    raise FileNotFoundError(f"Missing command file: {CMD_FILE}")
  out: List[CommandSample] = []
  with CMD_FILE.open("r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      if not line:
        continue
      obj = json.loads(line)
      out.append(
        CommandSample(
          t_s=float(obj["t_s"]),
          target_speed_mps=float(obj["target_speed_mps"]),
          rationale=str(obj.get("rationale", "baseline_follow")),
        )
      )
  out.sort(key=lambda c: c.t_s)
  return out


def align(states: List[StateSample],
          cmds: List[CommandSample]) -> Tuple[List[float], List[float], List[float], List[str]]:
  """
  Align by nearest time.
  Returns:
    t_list, v_baseline_list, v_target_list, rationale_list
  """
  if not states or not cmds:
    raise RuntimeError("Need both states and commands")

  t_list: List[float] = []
  v_base_list: List[float] = []
  v_target_list: List[float] = []
  rationale_list: List[str] = []

  j = 0
  for s in states:
    while j + 1 < len(cmds) and abs(cmds[j + 1].t_s - s.t_s) < abs(cmds[j].t_s - s.t_s):
      j += 1
    c = cmds[j]
    t_list.append(s.t_s)
    v_base_list.append(s.v_mps)
    v_target_list.append(c.target_speed_mps)
    rationale_list.append(c.rationale)

  return t_list, v_base_list, v_target_list, rationale_list


def plot_profile(t, v_base, v_target, rationale):
  plt.figure(figsize=(10, 5))
  plt.plot(t, v_base, label="Baseline speed v_mps")
  plt.plot(t, v_target, label="EMS target_speed_mps", linestyle="--")

  # Simple colored background for dominant rationales
  # Map rationale to colors
  colors = {
    "baseline_follow": "lightgray",
    "eco_coast_1": "lightgreen",
    "peak_shave_1": "lightcoral",
    "catchup_cancel_coast": "lightskyblue",
  }

  current_r = rationale[0]
  start_t = t[0]
  for i in range(1, len(t)):
    if rationale[i] != current_r:
      end_t = t[i]
      color = colors.get(current_r)
      if color:
        plt.axvspan(start_t, end_t, color=color, alpha=0.15, linewidth=0)
      current_r = rationale[i]
      start_t = t[i]

  # Last segment
  color = colors.get(current_r)
  if color:
    plt.axvspan(start_t, t[-1], color=color, alpha=0.15, linewidth=0)

  plt.xlabel("Time (s)")
  plt.ylabel("Speed (m/s)")
  plt.title("Baseline vs EMS target speed with rationale regions")
  plt.legend()
  plt.tight_layout()
  plt.savefig(OUT_PNG)
  print(f"Saved plot to {OUT_PNG}")


def main():
  states = load_states()
  cmds = load_commands()
  t, v_base, v_target, rationale = align(states, cmds)
  print(f"Loaded {len(states)} state samples and {len(cmds)} commands")
  plot_profile(t, v_base, v_target, rationale)


if __name__ == "__main__":
  main()
