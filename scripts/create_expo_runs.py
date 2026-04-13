#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "mr90_default.json"
SIM_RUNNER_PATH = REPO_ROOT / "simulator" / "src" / "run_sim.py"
SIM_OUTPUT_DIR = REPO_ROOT / "simulator" / "outputs"
BASELINE_EXPO_RUN_ID = "EXPO_baseline_reference"

API_BUILD_TEM_ARTIFACT = None
API_IMPORT_WARNING = None
try:
    from data_layer.api import build_tem_artifact as API_BUILD_TEM_ARTIFACT  # type: ignore
except ModuleNotFoundError as exc:
    if exc.name != "flask":
        raise
    API_IMPORT_WARNING = (
        "Flask is not installed in this Python environment. "
        "Using the standalone TEM artifact builder inside create_expo_runs.py."
    )


@dataclass(frozen=True)
class ExpoRunSpec:
    run_id: str
    scenario_name: str
    mode: str
    build_overrides: Callable[[Dict[str, Any]], Dict[str, Any]]
    tem_baseline_run_id: Optional[str]


def load_json_object(path: Path, label: str) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return data


def load_default_config() -> Dict[str, Any]:
    return load_json_object(DEFAULT_CONFIG_PATH, "Default config")


def load_kpi(run_id: str) -> Dict[str, Any]:
    return load_json_object(SIM_OUTPUT_DIR / f"kpi_{run_id}.json", "KPI JSON")


def load_resolved_config(run_id: str) -> Dict[str, Any]:
    return load_json_object(SIM_OUTPUT_DIR / f"config_{run_id}.json", "Resolved config JSON")


def _optional_clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _round_or_none(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def extract_run_metadata(cfg: Dict[str, Any]) -> Dict[str, Any]:
    meta = cfg.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    pricing = cfg.get("pricing", {})
    if not isinstance(pricing, dict):
        pricing = {}

    mode = _optional_clean_text(meta.get("mode"))
    if mode not in {"baseline", "hess"}:
        mode = None

    raw_price = pricing.get("grid_price_per_kwh")
    try:
        price_per_kwh = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError):
        price_per_kwh = None

    return {
        "scenario_name": _optional_clean_text(meta.get("scenario_name")),
        "mode": mode,
        "created_at_utc": _optional_clean_text(meta.get("created_at_utc")),
        "price_per_kwh": price_per_kwh,
    }


def build_tem_recommendations(
    mode: Optional[str],
    price_per_kwh: float,
    baseline_available: bool,
) -> Dict[str, str]:
    if mode == "baseline":
        recommendation = (
            "TEM analysis identifies this run as a reference for later HESS cost comparisons."
        )
        strategy = (
            "Baseline mode does not dispatch onboard storage. Use this run as the grid-cost reference for later comparison."
        )
    elif mode == "hess" and price_per_kwh >= 0.2:
        recommendation = (
            "Prioritize storage discharge during high-price periods to reduce grid draw."
        )
        strategy = (
            "Use battery and supercapacitor support during higher-cost traction demand and recover energy during braking where available."
        )
    elif mode == "hess" and price_per_kwh <= 0.05:
        recommendation = (
            "Charge storage during low-price periods where operationally feasible."
        )
        strategy = (
            "Favor charging storage during lower-cost windows, then use stored energy to offset later traction demand."
        )
    else:
        recommendation = (
            "Use storage selectively to trim grid demand while maintaining regenerative capture."
        )
        strategy = (
            "Blend moderate storage support with regenerative capture to reduce total grid cost without changing the simulator timeline."
        )

    if not baseline_available:
        strategy = (
            f"{strategy} Baseline comparison not available. Savings fields are informational only after a baseline run is provided."
        )

    return {
        "recommendation_summary": recommendation,
        "charge_discharge_strategy_summary": strategy,
    }


def build_tem_artifact_without_api(
    run_id: str,
    baseline_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    kpi = load_kpi(run_id)
    cfg = load_resolved_config(run_id)
    run_meta = extract_run_metadata(cfg)

    price_per_kwh = float(run_meta["price_per_kwh"] or 0.0)
    grid_energy_kwh = float(kpi.get("grid_energy_kwh", 0.0) or 0.0)
    distance_km = float(kpi.get("distance_km", 0.0) or 0.0)
    total_cost = grid_energy_kwh * price_per_kwh

    baseline_id_clean = _optional_clean_text(baseline_run_id)
    savings_total = None
    savings_per_train_km = None
    if baseline_id_clean is not None:
        baseline_kpi = load_kpi(baseline_id_clean)
        baseline_grid_energy_kwh = float(baseline_kpi.get("grid_energy_kwh", 0.0) or 0.0)
        baseline_total_cost = baseline_grid_energy_kwh * price_per_kwh
        savings_total = baseline_total_cost - total_cost
        savings_per_train_km = savings_total / distance_km if distance_km > 0.0 else None

    recommendations = build_tem_recommendations(
        mode=run_meta["mode"],
        price_per_kwh=price_per_kwh,
        baseline_available=baseline_id_clean is not None,
    )

    return {
        "run_id": run_id,
        "scenario_name": run_meta["scenario_name"],
        "mode": run_meta["mode"],
        "analyzed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pricing_used_per_kwh": _round_or_none(price_per_kwh, 6),
        "baseline_run_id": baseline_id_clean,
        "grid_energy_kwh": _round_or_none(grid_energy_kwh, 6),
        "distance_km": _round_or_none(distance_km, 6),
        "total_cost": _round_or_none(total_cost, 6),
        "savings_total_vs_baseline": _round_or_none(savings_total, 6),
        "savings_per_train_km_vs_baseline": _round_or_none(savings_per_train_km, 6),
        "recommendation_summary": recommendations["recommendation_summary"],
        "charge_discharge_strategy_summary": recommendations["charge_discharge_strategy_summary"],
        "status": "complete",
    }


def no_overrides(_: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def build_high_price_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    pricing = cfg.get("pricing", {}) if isinstance(cfg.get("pricing"), dict) else {}
    default_price = float(pricing.get("grid_price_per_kwh", 0.25) or 0.25)
    return {
        "pricing": {
            "grid_price_per_kwh": max(0.50, round(default_price + 0.25, 4))
        }
    }


def build_constrained_grid_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    hess_cfg = cfg.get("hess", {}) if isinstance(cfg.get("hess"), dict) else {}
    grid_cfg = hess_cfg.get("grid", {}) if isinstance(hess_cfg.get("grid"), dict) else {}
    default_sag_kw = float(grid_cfg.get("sag_max_power_kw", 0.0) or 0.0)
    return {
        "hess": {
            "grid": {
                "sag_max_power_kw": max(300, round(default_sag_kw * 0.7))
            }
        }
    }


EXPO_RUN_SPECS = (
    ExpoRunSpec(
        run_id=BASELINE_EXPO_RUN_ID,
        scenario_name="Baseline Reference",
        mode="baseline",
        build_overrides=no_overrides,
        tem_baseline_run_id=None,
    ),
    ExpoRunSpec(
        run_id="EXPO_hess_reference",
        scenario_name="HESS Reference",
        mode="hess",
        build_overrides=no_overrides,
        tem_baseline_run_id=BASELINE_EXPO_RUN_ID,
    ),
    ExpoRunSpec(
        run_id="EXPO_high_price",
        scenario_name="High Price Period",
        mode="hess",
        build_overrides=build_high_price_overrides,
        tem_baseline_run_id=BASELINE_EXPO_RUN_ID,
    ),
    ExpoRunSpec(
        run_id="EXPO_constrained_grid",
        scenario_name="Constrained Grid",
        mode="hess",
        build_overrides=build_constrained_grid_overrides,
        tem_baseline_run_id=BASELINE_EXPO_RUN_ID,
    ),
)


def write_temp_overrides(temp_dir: Path, run_id: str, overrides: Dict[str, Any]) -> Path:
    overrides_path = temp_dir / f"{run_id}_overrides.json"
    with overrides_path.open("w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2)
    return overrides_path


def run_simulator(spec: ExpoRunSpec, default_cfg: Dict[str, Any]) -> None:
    overrides = spec.build_overrides(default_cfg)
    with tempfile.TemporaryDirectory(prefix="rems_expo_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        overrides_path = write_temp_overrides(temp_dir, spec.run_id, overrides)

        cmd = [
            sys.executable,
            str(SIM_RUNNER_PATH),
            "--config",
            str(DEFAULT_CONFIG_PATH),
            "--out-dir",
            str(SIM_OUTPUT_DIR),
            "--run-id",
            spec.run_id,
            "--mode",
            spec.mode,
            "--scenario-name",
            spec.scenario_name,
            "--overrides",
            str(overrides_path),
        ]
        if spec.mode == "hess" and spec.tem_baseline_run_id:
            cmd.extend(["--baseline-run-id", spec.tem_baseline_run_id])

        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Simulator failed for {spec.run_id}\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )

        if proc.stdout.strip():
            print(proc.stdout.strip())


def write_tem_artifact(spec: ExpoRunSpec) -> Path:
    if API_BUILD_TEM_ARTIFACT is not None:
        artifact = API_BUILD_TEM_ARTIFACT(
            run_id=spec.run_id,
            baseline_run_id=spec.tem_baseline_run_id,
        )
    else:
        artifact = build_tem_artifact_without_api(
            run_id=spec.run_id,
            baseline_run_id=spec.tem_baseline_run_id,
        )
    tem_path = SIM_OUTPUT_DIR / f"tem_{spec.run_id}.json"
    tem_path.parent.mkdir(parents=True, exist_ok=True)
    with tem_path.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    return tem_path


def verify_run_outputs(spec: ExpoRunSpec) -> Dict[str, Path]:
    expected_paths = {
        "timeline": SIM_OUTPUT_DIR / f"timeline_{spec.run_id}.csv",
        "kpi": SIM_OUTPUT_DIR / f"kpi_{spec.run_id}.json",
        "config": SIM_OUTPUT_DIR / f"config_{spec.run_id}.json",
        "tem": SIM_OUTPUT_DIR / f"tem_{spec.run_id}.json",
    }
    missing = [name for name, path in expected_paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing expected expo artifacts for {spec.run_id}: {', '.join(missing)}"
        )
    return expected_paths


def main() -> None:
    default_cfg = load_default_config()

    if API_IMPORT_WARNING:
        print(API_IMPORT_WARNING)

    print("Refreshing canonical expo runs...")
    for spec in EXPO_RUN_SPECS:
        print(f"\n[{spec.run_id}] scenario={spec.scenario_name} mode={spec.mode}")
        run_simulator(spec, default_cfg)
        tem_path = write_tem_artifact(spec)
        outputs = verify_run_outputs(spec)
        print(f"  timeline: {outputs['timeline']}")
        print(f"  kpi:      {outputs['kpi']}")
        print(f"  config:   {outputs['config']}")
        print(f"  tem:      {tem_path}")

    print("\nCanonical expo runs are ready.")


if __name__ == "__main__":
    main()
