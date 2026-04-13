#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_layer.api import SIM_OUTPUT_DIR, build_tem_artifact  # noqa: E402


DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "mr90_default.json"
SIM_RUNNER_PATH = REPO_ROOT / "simulator" / "src" / "run_sim.py"
BASELINE_EXPO_RUN_ID = "EXPO_baseline_reference"


@dataclass(frozen=True)
class ExpoRunSpec:
    run_id: str
    scenario_name: str
    mode: str
    build_overrides: Callable[[Dict[str, Any]], Dict[str, Any]]
    tem_baseline_run_id: Optional[str]


def load_default_config() -> Dict[str, Any]:
    with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Default config must be a JSON object: {DEFAULT_CONFIG_PATH}")
    return data


def no_overrides(_: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def build_high_price_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    pricing = cfg.get("pricing", {}) if isinstance(cfg.get("pricing"), dict) else {}
    default_price = float(pricing.get("grid_price_per_kwh", 0.0) or 0.0)
    return {
        "pricing": {
            "grid_price_per_kwh": max(0.25, round(default_price + 0.25, 4))
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
    artifact = build_tem_artifact(
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
