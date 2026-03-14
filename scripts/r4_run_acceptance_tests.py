#!/usr/bin/env python3
"""Run Report 4 acceptance tests and generate Section 3 evidence artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "test_outputs" / "R4"
SIM_OUTPUT_DIR = REPO_ROOT / "simulator" / "outputs"
BASE_CONFIG_PATH = REPO_ROOT / "config" / "mr90_default.json"
SIM_RUNNER_PATH = REPO_ROOT / "simulator" / "src" / "run_sim.py"
API_RUNNER_PATH = REPO_ROOT / "data_layer" / "api.py"
API_BASE = "http://127.0.0.1:5000"
PERFORMED_BY = "Mohammad Daiyan"
PRICE_PER_KWH = 0.25
API_TIMEOUT_BOUND_S = 60.0
HEALTH_TIMEOUT_S = 25.0
HTTP_TIMEOUT_S = 70.0
RUN_ID_COUNTER = 0
TEST_ORDER = [
    "FT-1",
    "FT-2",
    "FT-3",
    "FT-4",
    "UT-1",
    "UT-2",
    "UT-3",
    "CT-1",
    "CT-2",
    "CT-3",
    "PT-1",
]


class TestFailure(Exception):
    """Raised when a test assertion fails."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_run_id(prefix: str) -> str:
    global RUN_ID_COUNTER
    RUN_ID_COUNTER += 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in prefix)
    return f"R4_{safe_prefix}_{stamp}_{RUN_ID_COUNTER:02d}"


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise TestFailure(f"Expected JSON object in {path}")
    return data


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_command(args: List[str], timeout: float = 120.0) -> Dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "elapsed_s": round(elapsed, 3),
    }


def http_request(
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            body = json.loads(raw_body) if raw_body else None
            return {"status": response.status, "body": body, "url": url}
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        try:
            body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            body = {"raw_body": raw_body}
        return {"status": exc.code, "body": body, "url": url}


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


def get_git_commit() -> str:
    result = run_command(["git", "rev-parse", "HEAD"], timeout=15.0)
    if result["returncode"] != 0:
        raise RuntimeError(f"Failed to read git commit: {result['stderr']}")
    return result["stdout"].strip()


def timeline_artifacts(run_id: str) -> Tuple[Path, Path, Path]:
    return (
        SIM_OUTPUT_DIR / f"timeline_{run_id}.csv",
        SIM_OUTPUT_DIR / f"kpi_{run_id}.json",
        SIM_OUTPUT_DIR / f"config_{run_id}.json",
    )


def csv_headers_and_rows(path: Path) -> Tuple[List[str], int]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        row_count = sum(1 for _ in reader)
    return headers, row_count


def start_api_server() -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, str(API_RUNNER_PATH)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=creationflags,
    )
    wait_for_api_health()
    return proc


def wait_for_api_health() -> None:
    deadline = time.time() + HEALTH_TIMEOUT_S
    last_error = None
    while time.time() < deadline:
        try:
            response = http_request("GET", "/api/health", timeout=5.0)
            if response["status"] == 200 and isinstance(response["body"], dict) and response["body"].get("status") == "ok":
                return
            last_error = f"Unexpected health payload: {response}"
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"API health check failed: {last_error}")


def stop_api_server(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8.0)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        pass


def ensure_snapshot_source() -> Dict[str, Any]:
    run_id = "MR90_hess"
    timeline_path, kpi_path, config_path = timeline_artifacts(run_id)
    if timeline_path.exists() and kpi_path.exists() and config_path.exists():
        return {"run_id": run_id, "created": False}
    result = run_command(
        [
            sys.executable,
            str(SIM_RUNNER_PATH),
            "--mode",
            "hess",
            "--run-id",
            run_id,
            "--config",
            str(BASE_CONFIG_PATH),
        ],
        timeout=120.0,
    )
    ensure(result["returncode"] == 0, f"Failed to prepare snapshot source: {result['stderr']}")
    ensure(timeline_path.exists() and kpi_path.exists() and config_path.exists(), "Snapshot source artifacts missing")
    return {"run_id": run_id, "created": True, "command": result}


def make_evidence(
    git_commit: str,
    test_id: str,
    pass_fail: str,
    comments: str,
    key_observations: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "performed_by": PERFORMED_BY,
        "executed_at_utc": utc_now_iso(),
        "git_commit": git_commit,
        "test_id": test_id,
        "pass_fail": pass_fail,
        "comments": comments,
        "key_observations": key_observations,
    }


def persist_evidence(evidence: Dict[str, Any]) -> Path:
    evidence_path = OUTPUT_DIR / f"evidence_{evidence['test_id']}.json"
    write_json(evidence_path, evidence)
    return evidence_path


def execute_test(
    test_id: str,
    comments: str,
    fn,
    git_commit: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        observations = fn(context)
        pass_fail = "PASS"
        comment_text = comments
    except Exception as exc:
        pass_fail = "FAIL"
        comment_text = f"{comments} Test failed: {exc}"
        observations = {"error": str(exc)}
    evidence = make_evidence(git_commit, test_id, pass_fail, comment_text, observations)
    evidence_path = persist_evidence(evidence)
    return {
        "Test Case#": test_id,
        "Date UTC": evidence["executed_at_utc"],
        "Result": pass_fail,
        "Comments": comment_text,
        "Evidence File": str(evidence_path.relative_to(REPO_ROOT)).replace("\\", "/"),
    }


def test_ft1(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = make_run_id("FT1_baseline")
    result = run_command(
        [
            sys.executable,
            str(SIM_RUNNER_PATH),
            "--mode",
            "baseline",
            "--run-id",
            run_id,
            "--config",
            str(BASE_CONFIG_PATH),
        ]
    )
    ensure(result["returncode"] == 0, f"Simulator returned {result['returncode']}")
    timeline_path, kpi_path, config_path = timeline_artifacts(run_id)
    ensure(timeline_path.exists(), "Timeline artifact missing")
    ensure(kpi_path.exists(), "KPI artifact missing")
    ensure(config_path.exists(), "Resolved config artifact missing")
    headers, row_count = csv_headers_and_rows(timeline_path)
    ensure(row_count > 0, "Timeline CSV is empty")
    return {
        "run_id": run_id,
        "command": result,
        "artifacts": {
            "timeline": str(timeline_path),
            "kpi": str(kpi_path),
            "config": str(config_path),
        },
        "timeline_exists": True,
        "kpi_exists": True,
        "config_exists": True,
        "timeline_row_count": row_count,
        "timeline_headers": headers,
    }


def test_ft2(context: Dict[str, Any]) -> Dict[str, Any]:
    baseline_id = make_run_id("FT2_baseline")
    hess_id = make_run_id("FT2_hess")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump({"pricing": {"grid_price_per_kwh": PRICE_PER_KWH}}, tmp, indent=2)
        overrides_path = Path(tmp.name)
    try:
        baseline_result = run_command(
            [
                sys.executable,
                str(SIM_RUNNER_PATH),
                "--mode",
                "baseline",
                "--run-id",
                baseline_id,
                "--config",
                str(BASE_CONFIG_PATH),
                "--overrides",
                str(overrides_path),
            ]
        )
        ensure(baseline_result["returncode"] == 0, "Baseline setup for FT-2 failed")
        hess_result = run_command(
            [
                sys.executable,
                str(SIM_RUNNER_PATH),
                "--mode",
                "hess",
                "--run-id",
                hess_id,
                "--config",
                str(BASE_CONFIG_PATH),
                "--baseline-run-id",
                baseline_id,
                "--overrides",
                str(overrides_path),
            ]
        )
        ensure(hess_result["returncode"] == 0, "HESS run for FT-2 failed")
        _, hess_kpi_path, _ = timeline_artifacts(hess_id)
        kpi = read_json(hess_kpi_path)
        required_keys = [
            "gridEnergyCost_total",
            "gridEnergyCost_per_train_km",
            "gridEnergySavings_total_vs_baseline",
            "gridEnergySavings_per_train_km_vs_baseline",
        ]
        for key in required_keys:
            ensure(key in kpi, f"Missing KPI key: {key}")
        ensure(isinstance(kpi["gridEnergySavings_total_vs_baseline"], (int, float)), "Savings total is not numeric")
        ensure(isinstance(kpi["gridEnergySavings_per_train_km_vs_baseline"], (int, float)), "Savings per km is not numeric")
        return {
            "baseline_run_id": baseline_id,
            "hess_run_id": hess_id,
            "overrides_path": str(overrides_path),
            "baseline_command": baseline_result,
            "hess_command": hess_result,
            "kpi_keys": sorted(kpi.keys()),
            "kpi_values": {key: kpi[key] for key in required_keys},
        }
    finally:
        try:
            overrides_path.unlink(missing_ok=True)
        except OSError:
            pass


def test_ft3(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = make_run_id("FT3_override")
    override_payload = {
        "corridor": {"dwell_s": 27.0},
        "pricing": {"grid_price_per_kwh": PRICE_PER_KWH},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(override_payload, tmp, indent=2)
        overrides_path = Path(tmp.name)
    hash_before = sha256_file(BASE_CONFIG_PATH)
    try:
        result = run_command(
            [
                sys.executable,
                str(SIM_RUNNER_PATH),
                "--mode",
                "baseline",
                "--run-id",
                run_id,
                "--config",
                str(BASE_CONFIG_PATH),
                "--overrides",
                str(overrides_path),
            ]
        )
        ensure(result["returncode"] == 0, "Simulator run for FT-3 failed")
        _, _, resolved_config_path = timeline_artifacts(run_id)
        resolved_cfg = read_json(resolved_config_path)
        hash_after = sha256_file(BASE_CONFIG_PATH)
        ensure(hash_before == hash_after, "Base config hash changed")
        ensure(float(resolved_cfg["corridor"]["dwell_s"]) == 27.0, "Resolved dwell_s does not match override")
        ensure(float(resolved_cfg["pricing"]["grid_price_per_kwh"]) == PRICE_PER_KWH, "Resolved grid price does not match override")
        return {
            "run_id": run_id,
            "command": result,
            "base_config_sha256_before": hash_before,
            "base_config_sha256_after": hash_after,
            "resolved_config_path": str(resolved_config_path),
            "resolved_values": {
                "corridor.dwell_s": resolved_cfg["corridor"]["dwell_s"],
                "pricing.grid_price_per_kwh": resolved_cfg["pricing"]["grid_price_per_kwh"],
            },
        }
    finally:
        try:
            overrides_path.unlink(missing_ok=True)
        except OSError:
            pass


def test_ft4(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = make_run_id("FT4_api")
    payload = {
        "run_id": run_id,
        "mode": "baseline",
        "overrides": {
            "corridor": {"dwell_s": 24.0},
            "pricing": {"grid_price_per_kwh": PRICE_PER_KWH},
        },
    }
    response = http_request("POST", "/api/sim/run", payload=payload)
    ensure(response["status"] == 200, f"Expected 200, got {response['status']}")
    body = response["body"]
    ensure(isinstance(body, dict) and body.get("status") == "ok", "API run response did not report ok")
    timeline_path, kpi_path, config_path = timeline_artifacts(run_id)
    ensure(timeline_path.exists(), "Timeline artifact missing after API run")
    ensure(kpi_path.exists(), "KPI artifact missing after API run")
    ensure(config_path.exists(), "Config artifact missing after API run")
    context["ft4_run_id"] = run_id
    return {
        "request_payload": payload,
        "api_response": body,
        "artifact_exists": {
            "timeline": timeline_path.exists(),
            "kpi": kpi_path.exists(),
            "config": config_path.exists(),
        },
    }


def test_ut1(context: Dict[str, Any]) -> Dict[str, Any]:
    config_response = http_request("GET", "/api/config/default")
    runs_response = http_request("GET", "/api/runs/list")
    ensure(config_response["status"] == 200, "Default config request failed")
    ensure(runs_response["status"] == 200, "Runs list request failed")
    cfg = config_response["body"]
    runs = runs_response["body"]
    ensure(isinstance(cfg, dict), "Default config payload is not a JSON object")
    for key in ["train", "corridor", "hess", "pricing"]:
        ensure(key in cfg, f"Missing config key: {key}")
    ensure(isinstance(runs, list), "Runs list payload is not an array")
    return {
        "config_keys_present": sorted([key for key in cfg.keys() if key in {"train", "corridor", "hess", "pricing"}]),
        "run_count": len(runs),
        "sample_run": runs[0] if runs else None,
    }


def test_ut2(context: Dict[str, Any]) -> Dict[str, Any]:
    invalid_run_payload = {
        "run_id": "bad run id",
        "mode": "baseline",
        "overrides": {"pricing": {"grid_price_per_kwh": PRICE_PER_KWH}},
    }
    invalid_overrides_payload = {
        "run_id": make_run_id("UT2_invalid"),
        "mode": "baseline",
        "overrides": "not-an-object",
    }
    response_a = http_request("POST", "/api/sim/run", payload=invalid_run_payload)
    response_b = http_request("POST", "/api/sim/run", payload=invalid_overrides_payload)
    ensure(response_a["status"] == 400, f"Invalid run_id expected 400, got {response_a['status']}")
    ensure(response_b["status"] == 400, f"Invalid overrides expected 400, got {response_b['status']}")
    return {
        "invalid_run_id_request": invalid_run_payload,
        "invalid_run_id_response": response_a,
        "invalid_overrides_request": invalid_overrides_payload,
        "invalid_overrides_response": response_b,
    }


def test_ut3(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = make_run_id("UT3_valid")
    payload = {
        "run_id": run_id,
        "mode": "hess",
        "overrides": {
            "corridor": {"dwell_s": 26.0},
            "pricing": {"grid_price_per_kwh": PRICE_PER_KWH},
        },
    }
    create_response = http_request("POST", "/api/sim/run", payload=payload)
    ensure(create_response["status"] == 200, f"Run creation failed with {create_response['status']}")
    replay_response = http_request("GET", f"/api/runs/{run_id}")
    ensure(replay_response["status"] == 200, f"Replay request failed with {replay_response['status']}")
    body = replay_response["body"]
    ensure(isinstance(body, dict), "Replay payload is not a JSON object")
    meta = body.get("meta")
    ensure(isinstance(meta, dict), "Replay payload missing meta")
    ensure("kpiSummary" in meta and isinstance(meta["kpiSummary"], dict), "Replay payload missing meta.kpiSummary")
    context["replay_run_id"] = run_id
    return {
        "request_payload": payload,
        "run_create_response": create_response["body"],
        "replay_status": replay_response["status"],
        "replay_meta_keys": sorted(meta.keys()),
        "kpi_summary_keys": sorted(meta["kpiSummary"].keys()),
    }


def test_ct1(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = context.get("replay_run_id") or context.get("ft4_run_id")
    ensure(isinstance(run_id, str) and run_id, "No completed run available for CT-1")
    response = http_request("GET", f"/api/runs/{run_id}")
    ensure(response["status"] == 200, f"Replay request failed with {response['status']}")
    body = response["body"]
    ensure(isinstance(body, dict), "Replay response is not a JSON object")
    meta = body.get("meta")
    timeline = body.get("timeline")
    ensure(isinstance(meta, dict), "Replay payload missing meta")
    ensure(isinstance(timeline, dict), "Replay payload missing timeline")
    ensure(isinstance(meta.get("kpiSummary"), dict), "Replay payload missing meta.kpiSummary")
    ensure(meta.get("runId") == run_id, "meta.runId does not match requested run_id")
    return {
        "requested_run_id": run_id,
        "meta_run_id": meta.get("runId"),
        "meta_keys": sorted(meta.keys()),
        "timeline_keys": sorted(timeline.keys()),
        "has_kpi_summary": True,
    }


def test_ct2(context: Dict[str, Any]) -> Dict[str, Any]:
    snapshot_info = context.get("snapshot_info")
    if snapshot_info is None:
        snapshot_info = ensure_snapshot_source()
        context["snapshot_info"] = snapshot_info
    response = http_request("GET", "/api/snapshot")
    ensure(response["status"] == 200, f"Snapshot request failed with {response['status']}")
    body = response["body"]
    ensure(isinstance(body, dict), "Snapshot response is not a JSON object")
    for key in ["kpi", "kpiSummary", "status", "events"]:
        ensure(key in body, f"Snapshot response missing key: {key}")
    return {
        "snapshot_source": snapshot_info,
        "snapshot_keys": sorted(body.keys()),
        "status_keys": sorted(body["status"].keys()) if isinstance(body.get("status"), dict) else None,
    }


def test_ct3(context: Dict[str, Any]) -> Dict[str, Any]:
    config_response = http_request("GET", "/api/config/default")
    runs_response = http_request("GET", "/api/runs/list")
    ensure(config_response["status"] == 200, "Default config request failed")
    ensure(runs_response["status"] == 200, "Runs list request failed")
    cfg = config_response["body"]
    runs = runs_response["body"]
    ensure(isinstance(cfg, dict), "Default config response is not a JSON object")
    ensure(all(key in cfg for key in ["train", "corridor", "hess", "pricing"]), "Default config missing required sections")
    ensure(isinstance(runs, list), "Runs list response is not a JSON array")
    sample_entry = runs[0] if runs else None
    if sample_entry is not None:
        ensure(isinstance(sample_entry, dict), "Runs list sample entry is not an object")
        ensure("run_id" in sample_entry, "Runs list sample entry missing run_id")
    return {
        "config_sections": {key: key in cfg for key in ["train", "corridor", "hess", "pricing"]},
        "runs_count": len(runs),
        "sample_run_entry": sample_entry,
    }


def test_pt1(context: Dict[str, Any]) -> Dict[str, Any]:
    run_id = make_run_id("PT1_api")
    payload = {
        "run_id": run_id,
        "mode": "baseline",
        "overrides": {
            "corridor": {"dwell_s": 23.0},
            "pricing": {"grid_price_per_kwh": PRICE_PER_KWH},
        },
    }
    started = time.perf_counter()
    response = http_request("POST", "/api/sim/run", payload=payload, timeout=HTTP_TIMEOUT_S)
    elapsed_s = time.perf_counter() - started
    ensure(response["status"] == 200, f"Performance run failed with {response['status']}")
    ensure(elapsed_s < API_TIMEOUT_BOUND_S, f"Run completed in {elapsed_s:.3f}s, exceeding timeout bound")
    return {
        "request_payload": payload,
        "response_status": response["status"],
        "elapsed_seconds": round(elapsed_s, 3),
        "timeout_bound_seconds": API_TIMEOUT_BOUND_S,
    }


def write_results(rows: List[Dict[str, Any]]) -> None:
    csv_path = OUTPUT_DIR / "section3_test_results.csv"
    md_path = OUTPUT_DIR / "section3_test_results.md"
    fieldnames = ["Test Case#", "Date UTC", "Result", "Comments", "Evidence File"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    md_lines = [
        "| Test Case# | Date UTC | Result | Comments | Evidence File |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        md_lines.append(
            f"| {row['Test Case#']} | {row['Date UTC']} | {row['Result']} | "
            f"{row['Comments']} | {row['Evidence File']} |"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    git_commit = get_git_commit()
    context: Dict[str, Any] = {"git_commit": git_commit}
    api_proc = start_api_server()

    tests = [
        ("FT-1", "Baseline run generated required artifacts and non-empty timeline output.", test_ft1),
        ("FT-2", "HESS KPI output included implemented cost and savings fields using a priced baseline reference.", test_ft2),
        ("FT-3", "Resolved config captured override values while the base config remained unchanged.", test_ft3),
        ("FT-4", "API-triggered simulator run returned ok and referenced generated artifacts.", test_ft4),
        ("UT-1", "Control workflow dependencies returned expected structures for default config and recent runs.", test_ut1),
        ("UT-2", "Invalid submission cases were rejected through objective API-side checks.", test_ut2),
        ("UT-3", "A valid edited run was created through the API path and exposed replay data with KPI summary.", test_ut3),
        ("CT-1", "Replay payload exposed the expected top-level contract and matched the requested run ID.", test_ct1),
        ("CT-2", "Snapshot payload exposed the expected live-data contract including KPI summary.", test_ct2),
        ("CT-3", "Default config and run-list endpoints matched the expected interface types.", test_ct3),
        ("PT-1", "API-triggered run completed successfully within the implemented 60 second timeout bound.", test_pt1),
    ]

    rows: List[Dict[str, Any]] = []
    try:
        for test_id, comments, fn in tests:
            rows.append(execute_test(test_id, comments, fn, git_commit, context))
    finally:
        stop_api_server(api_proc)

    ordered_rows = sorted(rows, key=lambda row: TEST_ORDER.index(row["Test Case#"]))
    write_results(ordered_rows)

    failed = [row for row in ordered_rows if row["Result"] != "PASS"]
    print(f"[r4] wrote results to {OUTPUT_DIR}")
    print(f"[r4] total tests: {len(ordered_rows)}, failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
