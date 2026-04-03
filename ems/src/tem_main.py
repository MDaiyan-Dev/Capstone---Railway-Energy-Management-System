#!/usr/bin/env python3
"""
Transactive Energy Module (TEM) - Railway Energy Management System
Capstone 2025-26 | Ontario Tech University
Author: Destiny Mekwunye

This module is the economic optimization core of the REMS. It reads telemetry
data (price, load demand, battery SOC) and produces charge/discharge decisions
for the HESS battery system, exposing cost metrics to the dashboard.

Inputs (from Simulator / EMS):
  - market_price_cents_per_kwh
  - load_demand_kw
  - soc_batt (0.0 - 1.0)
  - battery_power_limit_kw
  - tou_period (optional: "off_peak", "mid_peak", "on_peak")

Outputs:
  - To EMS:    charge/discharge command, dispatch_power_kw, target_soc
  - To Dashboard: current_price_signal, transaction_decision, soc, cost savings, optimizer_status

Acceptance tests covered:
  AT-F1: Input data parsing
  AT-F2: Low price charging
  AT-F3: High price discharging
  AT-F4: Battery protection (SOC floor)
  AT-F5: Demand balancing (grid supplement)
  AT-U1: Decision transparency
  AT-U2: Scenario switching
  AT-P1: Decision response time
  AT-P2: Continuous data processing
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BUS_IN = REPO_ROOT / "bus" / "in"
BUS_OUT = REPO_ROOT / "bus" / "out"
BUS_OUT.mkdir(parents=True, exist_ok=True)

TEM_CMD_FILE = BUS_OUT / "tem.command.v1.jsonl"
TEM_MONITOR_FILE = BUS_OUT / "tem.monitor.v1.jsonl"

# ---------------------------------------------------------------------------
# TEM Configuration / Thresholds
# ---------------------------------------------------------------------------

# Price thresholds (cents per kWh)
LOW_PRICE_THRESHOLD = 8.0    # charge battery when price is at or below this
HIGH_PRICE_THRESHOLD = 15.0  # discharge battery when price is at or above this

# SOC bounds (0.0 - 1.0)
SOC_MIN = 0.20   # AT-F4: never discharge below this (battery protection)
SOC_MAX = 0.90   # never charge above this

# Default battery parameters (can be overridden by EMS input)
DEFAULT_BATTERY_POWER_LIMIT_KW = 1000.0
DEFAULT_CHARGE_EFFICIENCY = 0.95
DEFAULT_DISCHARGE_EFFICIENCY = 0.95

# Decision response time budget (seconds) - AT-P1
DECISION_LATENCY_BUDGET_S = 1.0

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TEMInput:
    """Structured input consumed by TEM from Simulator and EMS."""
    timestamp: float
    load_demand_kw: float
    market_price_cents_per_kwh: float
    soc_batt: float                          # 0.0 - 1.0
    battery_power_limit_kw: float = DEFAULT_BATTERY_POWER_LIMIT_KW
    charge_efficiency: float = DEFAULT_CHARGE_EFFICIENCY
    discharge_efficiency: float = DEFAULT_DISCHARGE_EFFICIENCY
    operational_status: str = "normal"
    fault_flags: int = 0
    tou_period: str = "unknown"
    scenario_id: str = "default"
    forecast_price_vector: Optional[List[float]] = None


@dataclass
class TEMDispatch:
    """Output sent to EMS to control the HESS battery."""
    timestamp: float
    command: str           # "charge", "discharge", "grid_supply", "idle"
    dispatch_power_kw: float
    target_soc: float
    validity_timestamp: float
    rationale: str


@dataclass
class TEMMonitor:
    """Output sent to Dashboard for display."""
    timestamp: float
    current_price_signal: float       # cents/kWh
    transaction_decision: str         # human-readable: "Charging", "Discharging", etc.
    battery_soc: float
    estimated_cost_savings: float     # cumulative $ saved vs always-grid
    optimizer_status: str             # "optimizing" or "fallback"
    load_demand_kw: float
    dispatch_power_kw: float


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class InputValidationError(Exception):
    pass


def validate_input(data: dict) -> TEMInput:
    """
    Parse and validate a raw telemetry dict into a TEMInput.
    Raises InputValidationError if required fields are missing or invalid.
    Covers AT-F1: Input Data Parsing.
    """
    required = ["timestamp", "load_demand_kw", "market_price_cents_per_kwh", "soc_batt"]
    for field in required:
        if field not in data:
            raise InputValidationError(f"Missing required field: '{field}'")

    try:
        ts = float(data["timestamp"])
        load = float(data["load_demand_kw"])
        price = float(data["market_price_cents_per_kwh"])
        soc = float(data["soc_batt"])
    except (TypeError, ValueError) as e:
        raise InputValidationError(f"Non-numeric value in required field: {e}") from e

    if not (0.0 <= soc <= 1.0):
        raise InputValidationError(f"soc_batt out of range [0,1]: {soc}")
    if load < 0.0:
        raise InputValidationError(f"load_demand_kw cannot be negative: {load}")
    if price < 0.0:
        raise InputValidationError(f"market_price_cents_per_kwh cannot be negative: {price}")

    return TEMInput(
        timestamp=ts,
        load_demand_kw=load,
        market_price_cents_per_kwh=price,
        soc_batt=soc,
        battery_power_limit_kw=float(data.get("battery_power_limit_kw", DEFAULT_BATTERY_POWER_LIMIT_KW)),
        charge_efficiency=float(data.get("charge_efficiency", DEFAULT_CHARGE_EFFICIENCY)),
        discharge_efficiency=float(data.get("discharge_efficiency", DEFAULT_DISCHARGE_EFFICIENCY)),
        operational_status=str(data.get("operational_status", "normal")),
        fault_flags=int(data.get("fault_flags", 0)),
        tou_period=str(data.get("tou_period", "unknown")),
        scenario_id=str(data.get("scenario_id", "default")),
        forecast_price_vector=data.get("forecast_price_vector", None),
    )


# ---------------------------------------------------------------------------
# Core decision engine
# ---------------------------------------------------------------------------

class TransactiveEnergyModule:
    """
    Main TEM decision engine.

    Decision logic (rule-based with fallback):
      1. If fault or operational_status != "normal" -> idle (safe fallback)
      2. AT-F4: If SOC <= SOC_MIN -> protect battery, issue grid_supply command
      3. AT-F2: If price is LOW and SOC < SOC_MAX -> CHARGE from grid
      4. AT-F3: If price is HIGH and SOC > SOC_MIN -> DISCHARGE to support load
      5. AT-F5: If load_demand > max battery discharge -> supplement from grid
      6. Otherwise -> idle / baseline_follow
    """

    def __init__(self):
        self.cumulative_cost_baseline = 0.0   # cost if we always used grid
        self.cumulative_cost_actual = 0.0     # actual cost with TEM decisions
        self.decision_count = 0
        self.last_decision_latency_s = 0.0

    @property
    def estimated_cost_savings(self) -> float:
        return max(0.0, self.cumulative_cost_baseline - self.cumulative_cost_actual)

    def decide(self, inp: TEMInput, dt_s: float = 1.0) -> tuple[TEMDispatch, TEMMonitor, str]:
        """
        Make a charge/discharge/idle/grid_supply decision for one timestep.

        Returns (TEMDispatch, TEMMonitor, optimizer_status_str)
        """
        t_start = time.perf_counter()
        optimizer_status = "optimizing"

        # --- Update cumulative cost tracking ---
        # Baseline: always buy from grid at current price
        cents_per_kwh = inp.market_price_cents_per_kwh
        dollars_per_kwh = cents_per_kwh / 100.0
        baseline_cost_step = inp.load_demand_kw * (dt_s / 3600.0) * dollars_per_kwh
        self.cumulative_cost_baseline += baseline_cost_step

        # --- Safety fallback: fault or abnormal status ---
        if inp.fault_flags != 0 or inp.operational_status not in ("normal", "ok", ""):
            optimizer_status = "fallback"
            dispatch = TEMDispatch(
                timestamp=inp.timestamp,
                command="idle",
                dispatch_power_kw=0.0,
                target_soc=inp.soc_batt,
                validity_timestamp=inp.timestamp + 1.0,
                rationale=f"Fallback: fault_flags={inp.fault_flags} status={inp.operational_status}",
            )
            self.cumulative_cost_actual += baseline_cost_step
            return self._finalize(inp, dispatch, optimizer_status, t_start)

        # --- AT-F4: Battery protection — SOC at or below minimum ---
        if inp.soc_batt <= SOC_MIN:
            # Cannot discharge. Supply load entirely from grid.
            grid_power_kw = inp.load_demand_kw
            actual_cost_step = grid_power_kw * (dt_s / 3600.0) * dollars_per_kwh
            self.cumulative_cost_actual += actual_cost_step

            dispatch = TEMDispatch(
                timestamp=inp.timestamp,
                command="grid_supply",
                dispatch_power_kw=0.0,
                target_soc=SOC_MIN,
                validity_timestamp=inp.timestamp + 1.0,
                rationale=f"Battery protection: SOC={inp.soc_batt:.2%} <= SOC_MIN={SOC_MIN:.0%}. Grid only.",
            )
            return self._finalize(inp, dispatch, optimizer_status, t_start)

        # --- AT-F2: Low price → charge battery ---
        if cents_per_kwh <= LOW_PRICE_THRESHOLD and inp.soc_batt < SOC_MAX:
            # Charge at battery power limit (capped so we don't exceed SOC_MAX)
            headroom_kwh = (SOC_MAX - inp.soc_batt) * _estimate_capacity_kwh(inp)
            max_charge_by_soc = headroom_kwh / (dt_s / 3600.0) if dt_s > 0 else 0.0
            charge_power_kw = min(inp.battery_power_limit_kw, max_charge_by_soc)
            charge_power_kw = max(charge_power_kw, 0.0)

            # We still need to supply the load from the grid
            grid_power_kw = inp.load_demand_kw + charge_power_kw / inp.charge_efficiency
            actual_cost_step = grid_power_kw * (dt_s / 3600.0) * dollars_per_kwh
            self.cumulative_cost_actual += actual_cost_step

            dispatch = TEMDispatch(
                timestamp=inp.timestamp,
                command="charge",
                dispatch_power_kw=charge_power_kw,
                target_soc=SOC_MAX,
                validity_timestamp=inp.timestamp + 1.0,
                rationale=(
                    f"Low price ({cents_per_kwh:.1f} c/kWh <= {LOW_PRICE_THRESHOLD:.0f} c/kWh). "
                    f"Charging at {charge_power_kw:.1f} kW. SOC={inp.soc_batt:.2%}."
                ),
            )
            return self._finalize(inp, dispatch, optimizer_status, t_start)

        # --- AT-F3: High price → discharge battery to support load ---
        if cents_per_kwh >= HIGH_PRICE_THRESHOLD and inp.soc_batt > SOC_MIN:
            # Discharge to cover as much load as possible
            available_kwh = (inp.soc_batt - SOC_MIN) * _estimate_capacity_kwh(inp)
            max_discharge_by_soc = available_kwh / (dt_s / 3600.0) if dt_s > 0 else 0.0
            discharge_power_kw = min(
                inp.battery_power_limit_kw,
                max_discharge_by_soc,
                inp.load_demand_kw / inp.discharge_efficiency,
            )
            discharge_power_kw = max(discharge_power_kw, 0.0)

            # AT-F5: if discharge can't cover full load, supplement from grid
            battery_supply_kw = discharge_power_kw * inp.discharge_efficiency
            grid_supplement_kw = max(0.0, inp.load_demand_kw - battery_supply_kw)

            actual_cost_step = grid_supplement_kw * (dt_s / 3600.0) * dollars_per_kwh
            self.cumulative_cost_actual += actual_cost_step

            if grid_supplement_kw > 0.01:
                rationale = (
                    f"High price ({cents_per_kwh:.1f} c/kWh >= {HIGH_PRICE_THRESHOLD:.0f} c/kWh). "
                    f"Discharging {discharge_power_kw:.1f} kW. "
                    f"Grid supplement {grid_supplement_kw:.1f} kW. SOC={inp.soc_batt:.2%}."
                )
                command = "discharge"  # partial grid supplement handled in rationale
            else:
                rationale = (
                    f"High price ({cents_per_kwh:.1f} c/kWh >= {HIGH_PRICE_THRESHOLD:.0f} c/kWh). "
                    f"Discharging {discharge_power_kw:.1f} kW to supply full load. SOC={inp.soc_batt:.2%}."
                )
                command = "discharge"

            dispatch = TEMDispatch(
                timestamp=inp.timestamp,
                command=command,
                dispatch_power_kw=discharge_power_kw,
                target_soc=SOC_MIN,
                validity_timestamp=inp.timestamp + 1.0,
                rationale=rationale,
            )
            return self._finalize(inp, dispatch, optimizer_status, t_start)

        # --- AT-F5: Demand exceeds battery capacity even at mid price → grid supplement ---
        max_discharge_power = inp.battery_power_limit_kw * inp.discharge_efficiency
        if inp.load_demand_kw > max_discharge_power and inp.soc_batt > SOC_MIN:
            # Battery helps but grid must supplement
            grid_supplement_kw = inp.load_demand_kw - max_discharge_power
            actual_cost_step = grid_supplement_kw * (dt_s / 3600.0) * dollars_per_kwh
            self.cumulative_cost_actual += actual_cost_step

            dispatch = TEMDispatch(
                timestamp=inp.timestamp,
                command="discharge",
                dispatch_power_kw=inp.battery_power_limit_kw,
                target_soc=SOC_MIN,
                validity_timestamp=inp.timestamp + 1.0,
                rationale=(
                    f"Demand ({inp.load_demand_kw:.1f} kW) exceeds battery output "
                    f"({max_discharge_power:.1f} kW). Grid supplement {grid_supplement_kw:.1f} kW."
                ),
            )
            return self._finalize(inp, dispatch, optimizer_status, t_start)

        # --- Default: idle / follow baseline ---
        self.cumulative_cost_actual += baseline_cost_step
        dispatch = TEMDispatch(
            timestamp=inp.timestamp,
            command="idle",
            dispatch_power_kw=0.0,
            target_soc=inp.soc_batt,
            validity_timestamp=inp.timestamp + 1.0,
            rationale=(
                f"Mid-range price ({cents_per_kwh:.1f} c/kWh). "
                f"SOC={inp.soc_batt:.2%}. No action required."
            ),
        )
        return self._finalize(inp, dispatch, optimizer_status, t_start)

    def _finalize(
        self,
        inp: TEMInput,
        dispatch: TEMDispatch,
        optimizer_status: str,
        t_start: float,
    ) -> tuple[TEMDispatch, TEMMonitor, str]:
        latency = time.perf_counter() - t_start
        self.last_decision_latency_s = latency
        self.decision_count += 1

        # Map command to human-readable transaction decision
        decision_labels = {
            "charge": "Charging from grid (low price)",
            "discharge": "Discharging battery (high price)",
            "grid_supply": "Grid supply only (battery protection)",
            "idle": "Idle — no energy transaction",
        }
        transaction_decision = decision_labels.get(dispatch.command, dispatch.command)

        monitor = TEMMonitor(
            timestamp=inp.timestamp,
            current_price_signal=inp.market_price_cents_per_kwh,
            transaction_decision=transaction_decision,
            battery_soc=inp.soc_batt,
            estimated_cost_savings=self.estimated_cost_savings,
            optimizer_status=optimizer_status,
            load_demand_kw=inp.load_demand_kw,
            dispatch_power_kw=dispatch.dispatch_power_kw,
        )
        return dispatch, monitor, optimizer_status


def _estimate_capacity_kwh(inp: TEMInput) -> float:
    """
    Estimate usable battery capacity from power limit.
    Uses a simple heuristic: 1 hour at max power as a proxy.
    In a real system this would come from the EMS state.
    """
    return inp.battery_power_limit_kw * 1.0  # 1-hour equivalent in kWh


# ---------------------------------------------------------------------------
# Batch runner (reads telemetry JSONL from bus/in)
# ---------------------------------------------------------------------------

def load_telemetry_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load all lines from a JSONL telemetry file."""
    if not path.exists():
        raise FileNotFoundError(f"Telemetry file not found: {path}")
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def merge_telemetry(
    energy_records: List[Dict[str, Any]],
    price_per_kwh_cents: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Build TEM input records from energy sample JSONL.
    In the live system these would come from the Simulator with full price data.
    For batch mode we inject a synthetic price signal to demonstrate all decision paths.
    """
    merged = []
    n = len(energy_records)
    for i, rec in enumerate(energy_records):
        # Synthetic price: oscillates to exercise all decision branches
        # off-peak (cheap) -> on-peak (expensive) -> mid -> repeat
        cycle_frac = (i / max(n - 1, 1))
        if cycle_frac < 0.33:
            price = 5.0    # low: triggers charging
        elif cycle_frac < 0.66:
            price = 20.0   # high: triggers discharging
        else:
            price = 11.0   # mid: idle

        # Derive synthetic SOC from energy_kwh (decreasing as energy is consumed)
        max_kwh = max(r.get("energy_kwh", 0.0) for r in energy_records) or 1.0
        energy_fraction = rec.get("energy_kwh", 0.0) / max_kwh
        soc = max(SOC_MIN, 0.90 - energy_fraction * 0.5)

        merged.append({
            "timestamp": rec.get("t_s", float(i)),
            "load_demand_kw": max(rec.get("power_w", 0.0) / 1000.0, 0.0),
            "market_price_cents_per_kwh": price,
            "soc_batt": soc,
            "battery_power_limit_kw": DEFAULT_BATTERY_POWER_LIMIT_KW,
            "charge_efficiency": DEFAULT_CHARGE_EFFICIENCY,
            "discharge_efficiency": DEFAULT_DISCHARGE_EFFICIENCY,
            "operational_status": "normal",
            "fault_flags": 0,
            "tou_period": "off_peak" if price < 10 else ("on_peak" if price >= HIGH_PRICE_THRESHOLD else "mid_peak"),
            "scenario_id": rec.get("run_id", "batch"),
        })
    return merged


def run_batch(input_records: Optional[List[Dict[str, Any]]] = None) -> None:
    """
    Run TEM in batch mode over telemetry records.
    Writes dispatch commands to bus/out/tem.command.v1.jsonl
    Writes monitor data to bus/out/tem.monitor.v1.jsonl
    """
    if input_records is None:
        # Try to load from bus/in
        energy_path = BUS_IN / "telemetry.energy.sample.v1.jsonl"
        print(f"[TEM] Loading telemetry from {energy_path}…")
        energy_records = load_telemetry_jsonl(energy_path)
        input_records = merge_telemetry(energy_records)

    if not input_records:
        print("[TEM] No input records found. Aborting.")
        return

    print(f"[TEM] Running batch over {len(input_records)} telemetry samples…")

    tem = TransactiveEnergyModule()
    dispatch_records = []
    monitor_records = []

    prev_t = None
    for raw in input_records:
        try:
            inp = validate_input(raw)
        except InputValidationError as e:
            print(f"[TEM] Skipping invalid record: {e}")
            continue

        dt_s = (inp.timestamp - prev_t) if (prev_t is not None) else 1.0
        dt_s = max(dt_s, 0.01)
        prev_t = inp.timestamp

        dispatch, monitor, _ = tem.decide(inp, dt_s=dt_s)
        dispatch_records.append(asdict(dispatch))
        monitor_records.append(asdict(monitor))

    # Write outputs
    TEM_CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TEM_CMD_FILE.open("w", encoding="utf-8") as f:
        for rec in dispatch_records:
            f.write(json.dumps(rec) + "\n")

    with TEM_MONITOR_FILE.open("w", encoding="utf-8") as f:
        for rec in monitor_records:
            f.write(json.dumps(rec) + "\n")

    print(f"[TEM] Wrote {len(dispatch_records)} dispatch commands → {TEM_CMD_FILE}")
    print(f"[TEM] Wrote {len(monitor_records)} monitor records   → {TEM_MONITOR_FILE}")
    print(f"[TEM] Total decisions: {tem.decision_count}")
    print(f"[TEM] Estimated cost savings: ${tem.estimated_cost_savings:.4f}")
    print(f"[TEM] Last decision latency: {tem.last_decision_latency_s*1000:.2f} ms")


# ---------------------------------------------------------------------------
# Single-step API (used by data_layer/api.py for live snapshot)
# ---------------------------------------------------------------------------

_global_tem = TransactiveEnergyModule()


def step(raw_input: Dict[str, Any], dt_s: float = 1.0) -> Dict[str, Any]:
    """
    Process one telemetry sample and return a combined dispatch + monitor dict.
    Called by the Flask API layer for live /api/snapshot enrichment.
    """
    try:
        inp = validate_input(raw_input)
    except InputValidationError as e:
        return {
            "error": str(e),
            "command": "idle",
            "dispatch_power_kw": 0.0,
            "optimizer_status": "fallback",
        }

    dispatch, monitor, status = _global_tem.decide(inp, dt_s=dt_s)
    return {
        "command": dispatch.command,
        "dispatch_power_kw": dispatch.dispatch_power_kw,
        "target_soc": dispatch.target_soc,
        "rationale": dispatch.rationale,
        "current_price_signal": monitor.current_price_signal,
        "transaction_decision": monitor.transaction_decision,
        "battery_soc": monitor.battery_soc,
        "estimated_cost_savings": monitor.estimated_cost_savings,
        "optimizer_status": status,
        "validity_timestamp": dispatch.validity_timestamp,
    }



# ---------------------------------------------------------------------------
# Entry point (batch mode only — tests are in tem_tests.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    try:
        run_batch()
    except FileNotFoundError as e:
        print(f"[TEM] Batch mode skipped (no telemetry files): {e}")
        print("[TEM] To generate telemetry, run:")
        print("  python simulator/src/run_sim.py --mode hess --run-id MR90_hess")
        print("  python simulator/src/emit_jsonl_from_timeline.py --timeline simulator/outputs/timeline_MR90_hess.csv")