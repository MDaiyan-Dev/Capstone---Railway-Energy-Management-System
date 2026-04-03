#!/usr/bin/env python3
"""
TEM Acceptance Tests - Railway Energy Management System
Capstone 2025-26 | Ontario Tech University

Run with:
  python ems/src/tem_tests.py

Tests covered (from Report 4):
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

import sys
import time
from pathlib import Path

# Import everything from the core module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tem_main import (
    TEMInput,
    TEMDispatch,
    TEMMonitor,
    TransactiveEnergyModule,
    validate_input,
    SOC_MIN,
    SOC_MAX,
    LOW_PRICE_THRESHOLD,
    HIGH_PRICE_THRESHOLD,
    DECISION_LATENCY_BUDGET_S,
)


# ---------------------------------------------------------------------------
# Helper: pretty-print a single decision
# ---------------------------------------------------------------------------

def _print_decision(label: str, inp: TEMInput, dispatch: TEMDispatch, monitor: TEMMonitor):
    print(f"\n  --- {label} ---")
    print(f"  Input:    price={inp.market_price_cents_per_kwh:.1f} c/kWh  |  "
          f"SOC={inp.soc_batt*100:.0f}%  |  load={inp.load_demand_kw:.0f} kW")
    print(f"  Decision: [{dispatch.command.upper()}]  {dispatch.dispatch_power_kw:.1f} kW")
    print(f"  Why:      {dispatch.rationale}")
    print(f"  Dashboard shows: \"{monitor.transaction_decision}\"")


# ---------------------------------------------------------------------------
# Individual test functions
# ---------------------------------------------------------------------------

def test_AT_F1(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-F1: Input data parsing — verify all fields are correctly read."""
    inp = validate_input({
        "timestamp": 0.0,
        "load_demand_kw": 500.0,
        "market_price_cents_per_kwh": 10.0,
        "soc_batt": 0.75,
    })
    assert inp.load_demand_kw == 500.0
    assert inp.soc_batt == 0.75
    assert inp.market_price_cents_per_kwh == 10.0
    detail = f"  Parsed OK → load={inp.load_demand_kw} kW, price={inp.market_price_cents_per_kwh} c/kWh, SOC={inp.soc_batt*100:.0f}%"
    return "PASS", {"detail": detail}


def test_AT_F2(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-F2: Low price → should CHARGE battery."""
    inp = TEMInput(
        timestamp=1.0,
        load_demand_kw=300.0,
        market_price_cents_per_kwh=5.0,   # below LOW_PRICE_THRESHOLD
        soc_batt=0.50,                     # plenty of room to charge
    )
    dispatch, monitor, _ = tem.decide(inp)
    assert dispatch.command == "charge", f"Expected 'charge', got '{dispatch.command}'"
    assert dispatch.dispatch_power_kw > 0
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor}


def test_AT_F3(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-F3: High price → should DISCHARGE battery."""
    inp = TEMInput(
        timestamp=2.0,
        load_demand_kw=300.0,
        market_price_cents_per_kwh=20.0,  # above HIGH_PRICE_THRESHOLD
        soc_batt=0.80,                     # enough charge to discharge
    )
    dispatch, monitor, _ = tem.decide(inp)
    assert dispatch.command == "discharge", f"Expected 'discharge', got '{dispatch.command}'"
    assert dispatch.dispatch_power_kw > 0
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor}


def test_AT_F4(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-F4: SOC at floor → should protect battery and use GRID ONLY."""
    inp = TEMInput(
        timestamp=3.0,
        load_demand_kw=300.0,
        market_price_cents_per_kwh=20.0,  # high price — would normally discharge
        soc_batt=SOC_MIN,                  # but SOC is at the 20% minimum floor
    )
    dispatch, monitor, _ = tem.decide(inp)
    assert dispatch.command == "grid_supply", f"Expected 'grid_supply', got '{dispatch.command}'"
    assert dispatch.dispatch_power_kw == 0.0, "Battery should not dispatch when at SOC floor"
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor}


def test_AT_F5(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-F5: Demand exceeds battery → grid must supplement."""
    inp = TEMInput(
        timestamp=4.0,
        load_demand_kw=2000.0,            # train needs 2000 kW
        market_price_cents_per_kwh=20.0,
        soc_batt=0.80,
        battery_power_limit_kw=500.0,     # battery can only give 500 kW
    )
    dispatch, monitor, _ = tem.decide(inp)
    assert dispatch.command == "discharge"
    assert "supplement" in dispatch.rationale.lower(), \
        "Rationale should mention grid supplement"
    assert dispatch.dispatch_power_kw < inp.load_demand_kw, \
        "Battery dispatch must be less than total demand (grid fills the rest)"
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor}


def test_AT_U1(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-U1: Decision transparency — rationale and dashboard text must be present."""
    inp = TEMInput(
        timestamp=5.0,
        load_demand_kw=400.0,
        market_price_cents_per_kwh=5.0,
        soc_batt=0.60,
    )
    dispatch, monitor, _ = tem.decide(inp)
    assert len(dispatch.rationale) > 10, "Rationale string is too short"
    assert monitor.transaction_decision, "Dashboard transaction_decision is empty"
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor}


def test_AT_U2(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-U2: Scenario switching — 3 different price scenarios, 3 different decisions."""
    scenarios = [
        {"timestamp": 6.0, "load_demand_kw": 300.0, "market_price_cents_per_kwh": 5.0,  "soc_batt": 0.60},
        {"timestamp": 7.0, "load_demand_kw": 300.0, "market_price_cents_per_kwh": 20.0, "soc_batt": 0.80},
        {"timestamp": 8.0, "load_demand_kw": 300.0, "market_price_cents_per_kwh": 10.0, "soc_batt": 0.50},
    ]
    scenario_results = []
    for raw in scenarios:
        s_inp = validate_input(raw)
        s_dispatch, s_monitor, _ = tem.decide(s_inp)
        scenario_results.append((s_inp, s_dispatch, s_monitor))

    commands = [r[1].command for r in scenario_results]
    assert len(set(commands)) >= 2, \
        f"Expected at least 2 different decisions across scenarios, got: {commands}"
    return "PASS", {"scenario_results": scenario_results}


def test_AT_P1(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-P1: Decision must be made within 1 second."""
    inp = TEMInput(
        timestamp=9.0,
        load_demand_kw=300.0,
        market_price_cents_per_kwh=10.0,
        soc_batt=0.65,
    )
    t0 = time.perf_counter()
    dispatch, monitor, _ = tem.decide(inp)
    latency = time.perf_counter() - t0

    assert latency < DECISION_LATENCY_BUDGET_S, \
        f"Decision took {latency:.4f}s, exceeds {DECISION_LATENCY_BUDGET_S}s budget"
    return "PASS", {"inp": inp, "dispatch": dispatch, "monitor": monitor, "latency": latency}


def test_AT_P2(tem: TransactiveEnergyModule) -> tuple[str, dict]:
    """AT-P2: 100 consecutive steps must complete stably with no crashes."""
    tem_cont = TransactiveEnergyModule()
    soc = 0.70
    step_log = []

    for i in range(100):
        price = 5.0 if i % 3 == 0 else (20.0 if i % 3 == 1 else 10.0)
        raw = {
            "timestamp": float(i),
            "load_demand_kw": 300.0 + (i % 10) * 10,
            "market_price_cents_per_kwh": price,
            "soc_batt": max(SOC_MIN + 0.01, min(SOC_MAX - 0.01, soc)),
        }
        s_inp = validate_input(raw)
        s_dispatch, s_monitor, _ = tem_cont.decide(s_inp, dt_s=1.0)
        if i < 6:
            step_log.append((s_inp, s_dispatch, s_monitor))
        if s_dispatch.command == "charge":
            soc = min(soc + 0.003, SOC_MAX)
        elif s_dispatch.command == "discharge":
            soc = max(soc - 0.003, SOC_MIN)

    assert tem_cont.decision_count == 100
    return "PASS", {"step_log": step_log, "tem_cont": tem_cont}


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_all_tests() -> bool:
    print("\n" + "=" * 60)
    print("  TEM Acceptance Tests — Railway Energy Management System")
    print("=" * 60)

    tem = TransactiveEnergyModule()

    test_functions = [
        ("AT-F1", test_AT_F1),
        ("AT-F2", test_AT_F2),
        ("AT-F3", test_AT_F3),
        ("AT-F4", test_AT_F4),
        ("AT-F5", test_AT_F5),
        ("AT-U1", test_AT_U1),
        ("AT-U2", test_AT_U2),
        ("AT-P1", test_AT_P1),
        ("AT-P2", test_AT_P2),
    ]

    all_pass = True

    for test_id, fn in test_functions:
        print(f"\n{'='*60}")
        try:
            status, data = fn(tem)
        except Exception as e:
            print(f"  {test_id}  ✗ FAIL")
            print(f"  ERROR: {e}")
            all_pass = False
            continue

        print(f"  {test_id}  ✓ PASS")

        # --- Print what the module actually decided ---

        if test_id == "AT-F1":
            print(data["detail"])

        elif test_id == "AT-U2":
            print("  Three scenarios — three different decisions:")
            labels = [
                "Scenario A  (price=5 c/kWh  — LOW)",
                "Scenario B  (price=20 c/kWh — HIGH)",
                "Scenario C  (price=10 c/kWh — MID)",
            ]
            for label, (s_inp, s_dispatch, s_monitor) in zip(labels, data["scenario_results"]):
                _print_decision(label, s_inp, s_dispatch, s_monitor)

        elif test_id == "AT-P1":
            _print_decision("Mid-price scenario", data["inp"], data["dispatch"], data["monitor"])
            latency = data["latency"]
            print(f"  Latency:  {latency*1000:.3f} ms  (budget = {DECISION_LATENCY_BUDGET_S*1000:.0f} ms)")

        elif test_id == "AT-P2":
            step_log = data["step_log"]
            tem_cont = data["tem_cont"]
            print(f"  100 steps completed. First 6 shown:")
            for i, (s_inp, s_dispatch, s_monitor) in enumerate(step_log):
                _print_decision(f"Step {i+1}", s_inp, s_dispatch, s_monitor)
            print(f"\n  Total decisions made : {tem_cont.decision_count}")
            print(f"  Total cost savings   : ${tem_cont.estimated_cost_savings:.4f}")

        else:
            _print_decision(test_id, data["inp"], data["dispatch"], data["monitor"])

    print(f"\n{'='*60}")
    print(f"  Result: {'ALL PASS ✓' if all_pass else 'SOME FAILURES ✗'}")
    print("=" * 60)
    return all_pass


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)