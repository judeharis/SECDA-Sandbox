#!/usr/bin/env python3
"""Live terminal monitor for DSE Project_Status.json.

Shows per-run state in real time while dse_run.py executes in another terminal.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
from pathlib import Path


RUN_STAGE_ORDER = [
    "simulation_binary_generation",
    "systemc_simulation",
    "hls",
    "hlx",
    "fpga_binary_compilation",
    "fpga_mapping_experiment_execution",
    "results_collection",
]

STAGE_LABELS = {
    "simulation_binary_generation": "sim_bin",
    "systemc_simulation": "sysc",
    "hls": "hls",
    "hlx": "hlx",
    "fpga_binary_compilation": "fpga_compile",
    "fpga_mapping_experiment_execution": "fpga_exec",
    "results_collection": "collect",
}

STATE_LABELS = {
    "running": "RUN",
    "success": "OK",
    "failed": "FAIL",
    "timeout": "TIMEOUT",
    "skipped": "SKIP",
}


def now_local() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def merged_run_view(project_root: Path, run_id: str, run_data: dict) -> dict:
    """Prefer live per-run status when available to avoid stale project-level lag."""
    merged = dict(run_data) if isinstance(run_data, dict) else {}
    run_status_path = project_root / run_id / "Run_Status.json"
    live = read_json_safe(run_status_path)
    if not isinstance(live, dict) or not live:
        return merged

    # Overlay volatile fields from Run_Status.json for real-time monitor accuracy.
    for key in ["current_stage", "overall_state", "last_updated"]:
        if key in live:
            merged[key] = live[key]

    live_stages = live.get("stages")
    if isinstance(live_stages, dict):
        merged["stages"] = live_stages

    return merged


def infer_current_stage(run_data: dict) -> str:
    stages = run_data.get("stages", {})

    # Prefer actively running stages from live stage state.
    for stage_name, stage_data in stages.items():
        if isinstance(stage_data, dict) and stage_data.get("state") == "running":
            return stage_name

    # If no stage is running, choose the furthest attempted stage in pipeline order.
    if isinstance(stages, dict):
        for stage_name in reversed(RUN_STAGE_ORDER):
            stage_data = stages.get(stage_name)
            if not isinstance(stage_data, dict):
                continue
            state = str(stage_data.get("state", "not_started"))
            if state != "not_started":
                return stage_name

    # Legacy fallback for older status formats.
    current = run_data.get("current_stage")
    if current:
        return str(current)

    for stage_name, stage_data in stages.items():
        if isinstance(stage_data, dict) and stage_data.get("state") in {"failed", "timeout"}:
            return stage_name

    for stage_name, stage_data in stages.items():
        if isinstance(stage_data, dict) and stage_data.get("state") == "success":
            return stage_name

    return "-"


def extract_error(run_data: dict, current_stage: str) -> str:
    stages = run_data.get("stages", {})

    if current_stage in stages and isinstance(stages[current_stage], dict):
        reason = stages[current_stage].get("error_reason")
        if reason:
            return str(reason)

    for stage_name, stage_data in stages.items():
        if not isinstance(stage_data, dict):
            continue
        if stage_data.get("state") in {"failed", "timeout"}:
            reason = stage_data.get("error_reason") or stage_data.get("state")
            return f"{stage_name}:{reason}"

    return ""


def attempted_stages_summary(run_data: dict) -> str:
    stages = run_data.get("stages", {})
    if not isinstance(stages, dict):
        return "-"

    parts: list[str] = []
    for stage_name in RUN_STAGE_ORDER:
        stage_data = stages.get(stage_name)
        if not isinstance(stage_data, dict):
            continue
        state = str(stage_data.get("state", "not_started"))
        if state == "not_started":
            continue
        label = STAGE_LABELS.get(stage_name, stage_name)
        state_label = STATE_LABELS.get(state, state.upper())
        parts.append(f"{label}({state_label})")

    return " || ".join(parts) if parts else "-"


def shorten(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def terminal_width(default: int = 140) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def render(project_status_path: Path, clear_screen: bool) -> None:
    data = read_json_safe(project_status_path)
    project_root = project_status_path.parent
    width = terminal_width()

    if clear_screen:
        print("\033[2J\033[H", end="")

    print(f"DSE Project Status Monitor  |  {now_local()}")
    print(f"Project status file: {project_status_path}")

    if not data:
        print("Waiting for Project_Status.json ...")
        return

    project = data.get("project", {})
    summary = data.get("summary", {})
    runs = data.get("runs", {})

    variant_state = project.get("variant_generation", "unknown")
    project_result_state = project.get("project_result_status", "unknown")
    dataset_state = project.get("project_dataset_status", "unknown")

    print(
        "Project: "
        f"variant_generation={variant_state}  "
        f"project_result_status={project_result_state}  "
        f"project_dataset_status={dataset_state}"
    )
    print(
        "Summary: "
        f"total={summary.get('total_runs', 0)}  "
        f"ok={summary.get('succeeded_runs', 0)}  "
        f"failed={summary.get('failed_runs', 0)}  "
        f"timeout={summary.get('timed_out_runs', 0)}  "
        f"skipped={summary.get('skipped_runs', 0)}  "
        f"collected={summary.get('collected_runs', 0)}"
    )

    print("-" * min(width, 220))
    print("Runs")
    print("-" * min(width, 220))

    if not isinstance(runs, dict) or not runs:
        print("No runs yet.")
        return

    for run_id in sorted(runs.keys()):
        run_data = runs.get(run_id, {})
        if not isinstance(run_data, dict):
            continue
        run_data = merged_run_view(project_root, run_id, run_data)
        overall = str(run_data.get("overall_state", "-"))
        current = infer_current_stage(run_data)
        error = extract_error(run_data, current)
        print(f"[{run_id}] overall={overall} current={current} error={error or '-'}")

        stages = run_data.get("stages", {})
        if not isinstance(stages, dict):
            print("  - no stage data")
            continue

        for stage_name in RUN_STAGE_ORDER:
            stage_data = stages.get(stage_name)
            if not isinstance(stage_data, dict):
                continue
            state = str(stage_data.get("state", "not_started"))
            label = STAGE_LABELS.get(stage_name, stage_name)
            state_label = STATE_LABELS.get(state, state.upper())
            marker = ">>" if stage_name == current else "  "
            reason = stage_data.get("error_reason")
            if reason:
                print(f"  {marker} {label:<13} : {state_label:<7} ({reason})")
            else:
                print(f"  {marker} {label:<13} : {state_label}")
        print("-" * min(width, 220))


def main() -> int:
    parser = argparse.ArgumentParser(description="Live monitor for Project_Status.json")
    parser.add_argument("--project-status", required=True, help="Path to Project_Status.json")
    parser.add_argument("--watch-interval", type=float, default=2.0, help="Refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="Render once and exit")
    parser.add_argument("--no-clear", action="store_true", help="Do not clear screen between updates")
    args = parser.parse_args()

    status_path = Path(args.project_status).resolve()
    interval = max(args.watch_interval, 0.2)

    if args.once:
        render(status_path, clear_screen=not args.no_clear)
        return 0

    try:
        while True:
            render(status_path, clear_screen=not args.no_clear)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
