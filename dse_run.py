#!/usr/bin/env python3
"""Unified DSE runner for SECDA-Sandbox.

Pipeline stage mapping to DSE Explorer Upgrade Plan 2:
1) variant_generation
2) simulation_binary_generation
3) systemc_simulation
4) hls
5) hlx
6) fpga_binary_compilation
7) fpga_mapping_experiment_execution
8) results_collection
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Iterable


PROJECT_STAGE_KEYS = [
    "variant_generation",
]

RUN_STAGE_KEYS = [
    "simulation_binary_generation",
    "systemc_simulation",
    "hls",
    "hlx",
    "fpga_binary_compilation",
    "fpga_mapping_experiment_execution",
    "results_collection",
]

PLAN_STAGE_KEYS = PROJECT_STAGE_KEYS + RUN_STAGE_KEYS

FLOW_STAGE_MAP: dict[str, dict[str, bool]] = {
    "sim": {
        "generate": True,
        "sim": True,
        "hls": False,
        "hlx": False,
        "hw": False,
        "run": False,
        "collect": True,
        "parse": False,
    },
    "fpga": {
        "generate": True,
        "sim": False,
        "hls": True,
        "hlx": True,
        "hw": False,
        "run": True,
        "collect": True,
        "parse": True,
    },
    "lite": {
        "generate": True,
        "sim": False,
        "hls": True,
        "hlx": False,
        "hw": False,
        "run": False,
        "collect": True,
        "parse": False,
    },
    "sim_lite": {
        "generate": True,
        "sim": True,
        "hls": True,
        "hlx": False,
        "hw": False,
        "run": False,
        "collect": True,
        "parse": False,
    },
    "all": {
        "generate": True,
        "sim": True,
        "hls": True,
        "hlx": True,
        "hw": False,
        "run": True,
        "collect": True,
        "parse": True,
    },
}

FLOW_SIM_BEFORE_HW = {"sim", "sim_lite", "all"}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_logs_dir(out_root: Path) -> Path:
    return out_root / "status_logs"


def project_log_path(out_root: Path, name: str) -> Path:
    return project_logs_dir(out_root) / name


def run_cmd(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def run_cmd_timed(
    cmd: list[str],
    cwd: Path,
    timeout_sec: int | None,
    *,
    log_path: Path | None = None,
    append_log: bool = False,
) -> dict:
    print("$", " ".join(cmd))
    started = time.monotonic()
    timed_out = False
    return_code: int | None = None
    output_text = ""

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    log_mode = "a" if append_log else "w"
    log_context = log_path.open(log_mode) if log_path is not None else nullcontext()
    with log_context as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if timeout_sec is None or timeout_sec <= 0:
                output_text, _ = proc.communicate()
            else:
                output_text, _ = proc.communicate(timeout=timeout_sec)
            return_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            output_text = exc.output or ""
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                tail_text, _ = proc.communicate(timeout=10)
                output_text += tail_text or ""
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                tail_text, _ = proc.communicate()
                output_text += tail_text or ""

        if output_text:
            print(output_text, end="")
            if log_file is not None:
                log_file.write(output_text)

    duration = time.monotonic() - started
    return {
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_sec": duration,
        "timeout_sec": timeout_sec,
        "ok": (not timed_out and return_code == 0),
    }


def load_settings(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")
    return json.loads(path.read_text())


def format_experiment_folder(source_exp: Path, fmt: str) -> str:
    mapping = {
        "exp_name": source_exp.parent.name,
        "exp_version": source_exp.name,
        "experiment": source_exp.name,
        "exp_path": source_exp.as_posix(),
    }
    return fmt.format_map(mapping)


def iter_run_dirs(out_root: Path) -> Iterable[Path]:
    for d in sorted(out_root.iterdir()):
        if not d.is_dir():
            continue
        if d.name in {"dataset", "all_results"}:
            continue
        if (d / "hw_params.json").exists():
            yield d


def read_json_safe(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return dict(default)


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def blank_stage_record() -> dict:
    return {
        "state": "not_started",
        "start_time": None,
        "end_time": None,
        "duration_sec": None,
        "command": None,
        "log_path": None,
        "error_reason": None,
        "timeout_sec": None,
        "return_code": None,
        "skipped_due_to_stage": None,
    }


def compact_stage_record(stage: dict) -> dict:
    compact = {"state": stage.get("state", "not_started")}
    for key in [
        "start_time",
        "end_time",
        "duration_sec",
        "command",
        "log_path",
        "error_reason",
        "timeout_sec",
        "return_code",
        "skipped_due_to_stage",
    ]:
        value = stage.get(key)
        if value is not None:
            compact[key] = value
    return compact


def compact_run_status(status: dict) -> dict:
    compact = {
        "run_id": status.get("run_id"),
        "current_stage": status.get("current_stage"),
        "overall_state": status.get("overall_state"),
        "last_updated": status.get("last_updated"),
        "stages": {},
    }
    for stage_key in RUN_STAGE_KEYS:
        stage = (status.get("stages") or {}).get(stage_key)
        if not isinstance(stage, dict):
            continue
        if stage.get("state", "not_started") == "not_started":
            continue
        compact["stages"][stage_key] = compact_stage_record(stage)
    return compact


def stage_log_path(stage_key: str, run_dir: Path, settings: dict, out_root: Path) -> str:
    results_dir = run_dir / settings["results_dir_name"]
    run_id = run_dir.name
    mapping = {
        "simulation_binary_generation": results_dir / settings["sim_run_log_name"].format(run_id=run_id),
        "systemc_simulation": results_dir / settings["sim_run_log_name"].format(run_id=run_id),
        "hls": results_dir / "outputHLS.log",
        "hlx": results_dir / "outputHLX.log",
        "fpga_binary_compilation": results_dir / settings["run_log_name"].format(run_id=run_id),
        "fpga_mapping_experiment_execution": results_dir / settings["run_log_name"].format(run_id=run_id),
        "results_collection": project_log_path(out_root, "collect_dataset.log"),
    }
    return str(mapping[stage_key])


def remote_run_success_from_log(run_log_path: Path) -> bool | None:
    if not run_log_path.exists():
        return None
    try:
        text = run_log_path.read_text(errors="replace")
    except OSError:
        return None
    if "Summary: run_id=" in text:
        return True
    if text.strip():
        return False
    return None


def blank_run_status(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "current_stage": None,
        "overall_state": "not_started",
        "last_updated": now_iso(),
        "stages": {k: blank_stage_record() for k in RUN_STAGE_KEYS},
    }


def cli_invocation() -> str:
    argv = [Path(sys.argv[0]).name, *sys.argv[1:]]
    quoted = " ".join(shlex.quote(arg) for arg in argv)
    return f"{Path(sys.executable).name} {quoted}"


def extract_sample_from_command(command: str | None) -> int | None:
    if not command:
        return None

    parts = command.split()
    for index, part in enumerate(parts):
        if part in {"--sample", "-s"} and index + 1 < len(parts):
            try:
                return int(parts[index + 1])
            except ValueError:
                return None
    return None


def normalize_generation_command(command: str | None, sample: int | None) -> str | None:
    if not command:
        return None
    if "dse_explorer.py" not in command:
        return command

    try:
        parts = shlex.split(command)
    except ValueError:
        return command

    experiment: str | None = None
    for index, part in enumerate(parts):
        if part == "--experiment" and index + 1 < len(parts):
            experiment = parts[index + 1]
            break

    if not experiment:
        return command

    repo_root = Path(__file__).resolve().parent
    exp_path = Path(experiment)
    if exp_path.is_absolute():
        try:
            experiment = exp_path.relative_to(repo_root).as_posix()
        except ValueError:
            experiment = exp_path.as_posix()

    normalized = [Path(sys.executable).name, Path(__file__).name, "--experiment", experiment]
    if sample and sample > 0:
        normalized.extend(["-s", str(sample)])
    return " ".join(shlex.quote(part) for part in normalized)


def run_status_path(run_dir: Path, run_status_filename: str) -> Path:
    return run_dir / run_status_filename


def load_run_status(run_dir: Path, run_status_filename: str) -> dict:
    run_id = run_dir.name
    base = blank_run_status(run_id)
    current = read_json_safe(run_status_path(run_dir, run_status_filename), base)

    current.setdefault("run_id", run_id)
    current.setdefault("current_stage", None)
    current.setdefault("overall_state", "not_started")
    current.setdefault("last_updated", now_iso())
    stages = current.setdefault("stages", {})
    for key in RUN_STAGE_KEYS:
        stage = stages.setdefault(key, blank_stage_record())
        for k, v in blank_stage_record().items():
            stage.setdefault(k, v)
    return current


def save_run_status(run_dir: Path, run_status_filename: str, status: dict) -> None:
    status["last_updated"] = now_iso()
    write_json_atomic(run_status_path(run_dir, run_status_filename), compact_run_status(status))


def recalc_run_overall_state(status: dict) -> str:
    stages = status.get("stages", {})
    states = [stages[k].get("state") for k in RUN_STAGE_KEYS if k in stages]
    if any(s == "running" for s in states):
        return "running"
    if any(s == "timeout" for s in states):
        return "timeout"
    if any(s == "failed" for s in states):
        return "failed"
    terminal = {"success", "skipped", "not_started"}
    if states and all(s in terminal for s in states):
        if any(s == "success" for s in states):
            return "success"
    return "not_started"


def mark_stage_running(status: dict, stage_key: str, command: list[str], timeout_sec: int | None) -> None:
    stage = status["stages"][stage_key]
    stage["state"] = "running"
    stage["start_time"] = now_iso()
    stage["end_time"] = None
    stage["duration_sec"] = None
    stage["command"] = " ".join(command)
    stage["error_reason"] = None
    stage["timeout_sec"] = timeout_sec
    stage["return_code"] = None
    stage["skipped_due_to_stage"] = None
    status["current_stage"] = stage_key


def mark_stage_result(
    status: dict,
    stage_key: str,
    result_state: str,
    duration_sec: float | None,
    return_code: int | None,
    error_reason: str | None,
) -> None:
    stage = status["stages"][stage_key]
    stage["state"] = result_state
    stage["end_time"] = now_iso()
    stage["duration_sec"] = duration_sec
    stage["return_code"] = return_code
    stage["error_reason"] = error_reason
    status["overall_state"] = recalc_run_overall_state(status)


def mark_stage_skipped(status: dict, stage_key: str, skipped_due_to_stage: str) -> None:
    stage = status["stages"][stage_key]
    stage["state"] = "skipped"
    stage["start_time"] = stage["start_time"] or now_iso()
    stage["end_time"] = now_iso()
    stage["duration_sec"] = 0.0
    stage["error_reason"] = "dependency_failed"
    stage["skipped_due_to_stage"] = skipped_due_to_stage
    status["overall_state"] = recalc_run_overall_state(status)


def project_status_path(out_root: Path, project_status_filename: str) -> Path:
    return out_root / project_status_filename


def default_project_status() -> dict:
    return {
        "project": {
            "variant_generation": "not_started",
            "last_updated": now_iso(),
            "samples": None,
            "generation_command": None,
            "generation_log_path": None,
            "project_result_status": "not_started",
            "project_result_log_path": None,
            "project_dataset_status": "not_started",
            "project_dataset_log_path": None,
        },
        "runs": {},
        "summary": {
            "total_runs": 0,
            "succeeded_runs": 0,
            "failed_runs": 0,
            "timed_out_runs": 0,
            "skipped_runs": 0,
            "collected_runs": 0,
        },
    }


def reset_project_status(out_root: Path, project_status_filename: str) -> None:
    write_json_atomic(project_status_path(out_root, project_status_filename), default_project_status())


def load_project_status(out_root: Path, project_status_filename: str) -> dict:
    base = default_project_status()
    data = read_json_safe(project_status_path(out_root, project_status_filename), base)

    legacy_stages = data.pop("stages", {}) if isinstance(data.get("stages"), dict) else {}

    project = data.setdefault("project", {})
    if not isinstance(project, dict):
        project = {}
        data["project"] = project

    project.pop("state", None)

    project.setdefault(
        "variant_generation",
        (legacy_stages.get("variant_generation") or {}).get("state", base["project"]["variant_generation"]),
    )
    project.setdefault("last_updated", base["project"]["last_updated"])
    project.setdefault(
        "generation_command",
        (legacy_stages.get("variant_generation") or {}).get("command"),
    )
    project.setdefault("samples", extract_sample_from_command(project.get("generation_command")))
    project["generation_command"] = normalize_generation_command(
        project.get("generation_command"),
        project.get("samples"),
    )
    project.setdefault(
        "project_result_status",
        (legacy_stages.get("results_collection") or {}).get("state", base["project"]["project_result_status"]),
    )
    project.setdefault("generation_log_path", str(project_log_path(out_root, "variant_generation.log")))
    project.setdefault("project_result_log_path", str(project_log_path(out_root, "collect_results.log")))
    project.setdefault(
        "project_dataset_status",
        (legacy_stages.get("results_collection") or {}).get("state", base["project"]["project_dataset_status"]),
    )
    project.setdefault("project_dataset_log_path", str(project_log_path(out_root, "collect_dataset.log")))

    data.setdefault("runs", {})
    data.setdefault("summary", base["summary"])
    for key, value in base["summary"].items():
        data["summary"].setdefault(key, value)
    return data


def save_project_status(out_root: Path, project_status_filename: str, data: dict) -> None:
    data.setdefault("project", {})
    data["project"]["last_updated"] = now_iso()
    write_json_atomic(project_status_path(out_root, project_status_filename), data)


def filtered_run_stages(run_status: dict) -> dict:
    stages = run_status.get("stages", {})
    visible: dict = {}
    for stage_key in RUN_STAGE_KEYS:
        stage = stages.get(stage_key)
        if not isinstance(stage, dict):
            continue
        if stage.get("state") == "not_started":
            continue
        visible[stage_key] = compact_stage_record(stage)
    return visible


def summarize_stage_states(run_statuses: list[dict], stage_keys: list[str]) -> str:
    states: list[str] = []
    for run_status in run_statuses:
        for stage_key in stage_keys:
            stage = (run_status.get("stages") or {}).get(stage_key, {})
            state = stage.get("state", "not_started")
            if state != "not_started":
                states.append(state)

    if not states:
        return "not_started"
    if any(state == "running" for state in states):
        return "running"
    if any(state == "timeout" for state in states):
        return "timeout"
    if any(state == "failed" for state in states):
        return "failed"
    if all(state in {"success", "skipped"} for state in states):
        return "success"
    return "in_progress"


def backfill_run_status_metadata(status: dict, run_dir: Path, settings: dict, out_root: Path) -> None:
    for stage_key in RUN_STAGE_KEYS:
        stage = (status.get("stages") or {}).get(stage_key)
        if not isinstance(stage, dict):
            continue
        if stage.get("state", "not_started") == "not_started":
            continue
        if not stage.get("log_path"):
            stage["log_path"] = stage_log_path(stage_key, run_dir, settings, out_root)


def refresh_project_from_runs(out_root: Path, run_dirs: list[Path], args: argparse.Namespace, settings: dict) -> None:
    project = load_project_status(out_root, args.project_status_filename)
    runs_map: dict = {}
    all_run_statuses: list[dict] = []

    succeeded = 0
    failed = 0
    timed_out = 0
    skipped = 0
    collected = 0

    for run_dir in run_dirs:
        run_status = load_run_status(run_dir, args.run_status_filename)
        backfill_run_status_metadata(run_status, run_dir, settings, out_root)
        run_status["overall_state"] = recalc_run_overall_state(run_status)
        save_run_status(run_dir, args.run_status_filename, run_status)
        all_run_statuses.append(run_status)

        run_id = run_dir.name
        runs_map[run_id] = {
            "run_id": run_status.get("run_id", run_id),
            "current_stage": run_status.get("current_stage"),
            "overall_state": run_status.get("overall_state"),
            "last_updated": run_status.get("last_updated"),
            "stages": filtered_run_stages(run_status),
        }

        overall = run_status.get("overall_state")
        if overall == "success":
            succeeded += 1
        elif overall == "failed":
            failed += 1
        elif overall == "timeout":
            timed_out += 1

        if any(run_status["stages"][k]["state"] == "skipped" for k in RUN_STAGE_KEYS):
            skipped += 1

        if run_status["stages"]["results_collection"]["state"] == "success":
            collected += 1

    total = len(run_dirs)
    project["runs"] = runs_map
    project["summary"] = {
        "total_runs": total,
        "succeeded_runs": succeeded,
        "failed_runs": failed,
        "timed_out_runs": timed_out,
        "skipped_runs": skipped,
        "collected_runs": collected,
    }

    project_meta = project.setdefault("project", {})
    if project_meta.get("samples") is None:
        project_meta["samples"] = total
    project_meta["project_result_status"] = summarize_stage_states(
        all_run_statuses,
        [
            "simulation_binary_generation",
            "systemc_simulation",
            "hls",
            "hlx",
            "fpga_binary_compilation",
            "fpga_mapping_experiment_execution",
        ],
    )
    project_meta["project_dataset_status"] = summarize_stage_states(
        all_run_statuses,
        ["results_collection"],
    )

    save_project_status(out_root, args.project_status_filename, project)


def mark_project_stage(
    out_root: Path,
    args: argparse.Namespace,
    stage_key: str,
    state: str,
    *,
    command: str | None = None,
    duration_sec: float | None = None,
    error_reason: str | None = None,
    timeout_sec: int | None = None,
) -> None:
    project = load_project_status(out_root, args.project_status_filename)

    project_meta = project.setdefault("project", {})
    if stage_key == "variant_generation":
        project_meta["variant_generation"] = state
        if command:
            project_meta["generation_command"] = cli_invocation()
            project_meta["generation_log_path"] = str(project_log_path(out_root, "variant_generation.log"))
        if getattr(args, "sample", None) is not None:
            project_meta["samples"] = args.sample if args.sample > 0 else None

    save_project_status(out_root, args.project_status_filename, project)


def mark_project_collection_status(
    out_root: Path,
    args: argparse.Namespace,
    *,
    result_status: str | None = None,
    dataset_status: str | None = None,
) -> None:
    project = load_project_status(out_root, args.project_status_filename)
    project_meta = project.setdefault("project", {})
    if result_status is not None:
        project_meta["project_result_status"] = result_status
        project_meta["project_result_log_path"] = str(project_log_path(out_root, "collect_results.log"))
    if dataset_status is not None:
        project_meta["project_dataset_status"] = dataset_status
        project_meta["project_dataset_log_path"] = str(project_log_path(out_root, "collect_dataset.log"))
    save_project_status(out_root, args.project_status_filename, project)


def is_stage_success(run_status: dict, stage_key: str) -> bool:
    return run_status["stages"][stage_key]["state"] == "success"


def get_timeout(enable_timeouts: bool, value: int) -> int | None:
    if not enable_timeouts:
        return None
    return value if value > 0 else None


class _TeeWriter:
    """Writes to both a stream and a file, used to tee stdout/stderr to a log."""

    def __init__(self, stream: object, log_path: Path) -> None:
        self._stream = stream
        self._log = log_path.open("a", buffering=1)

    def write(self, data: str) -> int:
        self._stream.write(data)  # type: ignore[attr-defined]
        self._log.write(data)
        return len(data)

    def flush(self) -> None:
        self._stream.flush()  # type: ignore[attr-defined]
        self._log.flush()

    def fileno(self) -> int:
        return self._stream.fileno()  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


def launch_status_monitor_terminal(
    repo_root: Path,
    project_status_file: Path,
    watch_interval: float,
    outstream_log: Path | None = None,
) -> tuple[bool, str | None]:
    monitor_script = repo_root / "DSE_Explorer" / "scripts" / "project_status_monitor.py"
    if not monitor_script.exists():
        return False, "status monitor script not found"

    monitor_cmd = [
        sys.executable,
        str(monitor_script),
        "--project-status",
        str(project_status_file),
        "--watch-interval",
        str(max(watch_interval, 0.2)),
    ]
    monitor_cmd_text = " ".join(shlex.quote(part) for part in monitor_cmd)

    tmux = shutil.which("tmux")
    if tmux:
        session = "dse-monitor"
        try:
            # Kill any stale session so the layout is always fresh.
            subprocess.run(
                [tmux, "kill-session", "-t", session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Create a new detached session; the first pane shows the outstream log.
            if outstream_log is not None:
                left_cmd = f"tail -n +1 -f {shlex.quote(str(outstream_log))}"
            else:
                left_cmd = "bash"

            subprocess.run(
                [tmux, "new-session", "-d", "-s", session, "-x", "220", "-y", "50"],
                check=True,
            )
            subprocess.run(
                [tmux, "send-keys", "-t", f"{session}:0", left_cmd, "Enter"],
                check=True,
            )
            # Second pane (right) runs the status monitor.
            subprocess.run(
                [tmux, "split-window", "-h", "-t", f"{session}:0", monitor_cmd_text],
                check=True,
            )
            # Keep focus on the outstream pane.
            subprocess.run(
                [tmux, "select-pane", "-t", f"{session}:0.0"],
                check=True,
            )
            attach_cmd = f"tmux attach -t {session}"
            return True, attach_cmd
        except (OSError, subprocess.CalledProcessError):
            pass

    manual_hint = f"Open a second terminal and run: {monitor_cmd_text}"
    return False, manual_hint


def execute_sim_stages(
    out_root: Path,
    run_dirs: list[Path],
    args: argparse.Namespace,
    settings: dict,
) -> None:
    mark_project_stage(out_root, args, "simulation_binary_generation", "running", command="per-run sim_*.sh", timeout_sec=get_timeout(args.enable_timeouts, args.timeout_sim_bin))
    mark_project_stage(out_root, args, "systemc_simulation", "running", command="per-run sim_*.sh", timeout_sec=get_timeout(args.enable_timeouts, args.timeout_sim_run))

    timeout_sim = None
    if args.enable_timeouts:
        timeout_sim = args.timeout_sim_bin + args.timeout_sim_run

    for run_dir in run_dirs:
        status = load_run_status(run_dir, args.run_status_filename)
        if args.resume and is_stage_success(status, "simulation_binary_generation") and is_stage_success(status, "systemc_simulation"):
            continue

        candidates = sorted(run_dir.glob(settings["sim_glob"]))
        if not candidates:
            mark_stage_result(status, "simulation_binary_generation", "failed", 0.0, None, "missing_sim_script")
            mark_stage_skipped(status, "systemc_simulation", "simulation_binary_generation")
            save_run_status(run_dir, args.run_status_filename, status)
            continue

        cmd = ["bash", candidates[0].name]
        if args.dry_run:
            cmd = ["env", "DRY_RUN=1"] + cmd

        mark_stage_running(status, "simulation_binary_generation", cmd, get_timeout(args.enable_timeouts, args.timeout_sim_bin))
        mark_stage_running(status, "systemc_simulation", cmd, get_timeout(args.enable_timeouts, args.timeout_sim_run))
        sim_log_path = stage_log_path("simulation_binary_generation", run_dir, settings, out_root)
        status["stages"]["simulation_binary_generation"]["log_path"] = sim_log_path
        status["stages"]["systemc_simulation"]["log_path"] = sim_log_path
        save_run_status(run_dir, args.run_status_filename, status)

        result = run_cmd_timed(cmd, run_dir, timeout_sim)
        if result["timed_out"]:
            mark_stage_result(status, "simulation_binary_generation", "timeout", result["duration_sec"], None, "timeout")
            mark_stage_skipped(status, "systemc_simulation", "simulation_binary_generation")
        elif result["ok"]:
            mark_stage_result(status, "simulation_binary_generation", "success", result["duration_sec"], 0, None)
            mark_stage_result(status, "systemc_simulation", "success", result["duration_sec"], 0, None)
        else:
            mark_stage_result(status, "simulation_binary_generation", "failed", result["duration_sec"], result["return_code"], "command_failed")
            mark_stage_skipped(status, "systemc_simulation", "simulation_binary_generation")
        save_run_status(run_dir, args.run_status_filename, status)

    mark_project_stage(out_root, args, "simulation_binary_generation", "success")
    mark_project_stage(out_root, args, "systemc_simulation", "success")
    refresh_project_from_runs(out_root, run_dirs, args, settings)


def main() -> int:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent
    default_settings = repo_root / "DSE_Explorer" / "dse_setting.json"

    p = argparse.ArgumentParser(description="Unified DSE runner")
    p.add_argument("--experiment", required=True, help="Experiment path, e.g. experiments/mm_exp/v1")
    p.add_argument("--settings", default=str(default_settings), help="Path to dse_setting.json")
    p.add_argument("--output", default=None, help="Base output directory")
    p.add_argument("--sample", "-s", type=int, default=0, help="Deterministic sample size for generation")
    p.add_argument(
        "--stage",
        "-st",
        choices=["generate", "hls", "hlx", "hw", "run", "sim", "collect", "parse", "all"],
        default="all",
        help="Pipeline stage to execute",
    )
    p.add_argument(
        "--flow",
        choices=["sim", "fpga", "lite", "sim_lite", "all"],
        default="sim_lite",
        help="Run a predefined pipeline flow (overrides --stage selection, default: sim_lite)",
    )
    p.add_argument("--resume", action="store_true", help="Skip run folders already successful")
    p.add_argument("--dry-run", action="store_true", help="Forward dry-run mode to generated scripts")

    p.add_argument("--project-status-filename", default="Project_Status.json", help="Project-level status filename")
    p.add_argument("--run-status-filename", default="Run_Status.json", help="Per-run status filename")
    p.add_argument("--monitor", "-m", action="store_true", help="Launch live status monitor in a second terminal")
    p.add_argument("--monitor-interval", type=float, default=2.0, help="Monitor refresh interval in seconds")
    p.add_argument(
        "--enable-timeouts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable stage timeout checks (default: enabled)",
    )
    p.add_argument("--timeout-sim-bin", type=int, default=900, help="Timeout in seconds for simulation binary generation")
    p.add_argument("--timeout-sim-run", type=int, default=1800, help="Timeout in seconds for SystemC simulation")
    p.add_argument("--timeout-hls", type=int, default=3600, help="Timeout in seconds for HLS")
    p.add_argument("--timeout-hlx", type=int, default=10800, help="Timeout in seconds for HLX")
    p.add_argument("--timeout-fpga-compile", type=int, default=1800, help="Timeout in seconds for FPGA binary compilation")
    p.add_argument("--timeout-fpga-exec", type=int, default=1800, help="Timeout in seconds for FPGA mapping and experiment execution")
    args = p.parse_args()

    settings_path = Path(args.settings).resolve()
    settings = load_settings(settings_path)

    source_exp = Path(args.experiment)
    if not source_exp.is_absolute():
        source_exp = (repo_root / source_exp).resolve()
    if not source_exp.exists():
        raise FileNotFoundError(f"Experiment path not found: {source_exp}")

    out_base = Path(args.output).resolve() if args.output else (repo_root / settings["output_root"]).resolve()
    exp_folder = format_experiment_folder(source_exp, settings["experiment_folder_format"])
    out_root = out_base / exp_folder

    if out_root.exists():
        reset_project_status(out_root, args.project_status_filename)

    # Tee stdout/stderr to a log file so the tmux outstream pane can tail it.
    dse_log = out_root / "dse_run.log"
    out_root.mkdir(parents=True, exist_ok=True)
    dse_log.write_text("")  # truncate/create
    sys.stdout = _TeeWriter(sys.stdout, dse_log)  # type: ignore[assignment]
    sys.stderr = _TeeWriter(sys.stderr, dse_log)  # type: ignore[assignment]

    if args.monitor:
        launched, message = launch_status_monitor_terminal(
            repo_root,
            out_root / args.project_status_filename,
            args.monitor_interval,
            outstream_log=dse_log,
        )
        if launched:
            print(f"[monitor] tmux session started — attach with: {message}")
            print(f"[monitor] Watching: {out_root / args.project_status_filename}")
        else:
            print(f"[monitor] Unable to auto-launch tmux session. {message}")

    dse_explorer = repo_root / "DSE_Explorer" / "dse_explorer.py"
    parse_hw = repo_root / "DSE_Explorer" / "scripts" / "parse_hardware.py"
    parse_perf = repo_root / "DSE_Explorer" / "scripts" / "parse_performance.py"
    parse_hls = repo_root / "DSE_Explorer" / "scripts" / "parse_hls.py"

    do_generate = args.stage in {"generate", "all"}
    do_hls = args.stage in {"hls", "all"}
    do_hlx = args.stage in {"hlx", "all"}
    do_hw = args.stage in {"hw", "all"}
    do_run = args.stage in {"run", "all"}
    do_sim = args.stage in {"sim", "all"}
    do_collect = args.stage in {"collect", "all"}
    do_parse = args.stage in {"parse", "all"}

    if args.flow:
        flow_config = FLOW_STAGE_MAP[args.flow]
        do_generate = flow_config["generate"]
        do_hls = flow_config["hls"]
        do_hlx = flow_config["hlx"]
        do_hw = flow_config["hw"]
        do_run = flow_config["run"]
        do_sim = flow_config["sim"]
        do_collect = flow_config["collect"]
        do_parse = flow_config["parse"]

    if do_generate:
        cmd = [
            sys.executable,
            str(dse_explorer),
            "--experiment",
            str(source_exp),
            "--settings",
            str(settings_path),
            "--output",
            str(out_base),
        ]
        if args.sample > 0:
            cmd += ["--sample", str(args.sample)]
        if args.dry_run:
            cmd += ["--dry-run"]

        out_root.mkdir(parents=True, exist_ok=True)
        mark_project_stage(out_root, args, "variant_generation", "running", command=" ".join(cmd))
        started = time.monotonic()
        result = run_cmd_timed(
            cmd,
            repo_root,
            None,
            log_path=project_log_path(out_root, "variant_generation.log"),
        )
        if result["ok"]:
            mark_project_stage(
                out_root,
                args,
                "variant_generation",
                "success",
                duration_sec=result["duration_sec"],
            )
        else:
            reason = "timeout" if result["timed_out"] else "command_failed"
            mark_project_stage(
                out_root,
                args,
                "variant_generation",
                "failed" if not result["timed_out"] else "timeout",
                duration_sec=time.monotonic() - started,
                error_reason=reason,
            )
            return 1

    if (do_hw or do_run or do_sim or do_collect or do_parse) and not out_root.exists():
        raise FileNotFoundError(f"Output root does not exist: {out_root}. Run generate stage first.")

    run_dirs = list(iter_run_dirs(out_root))
    for run_dir in run_dirs:
        legacy_status_path = run_dir / settings.get("status_filename", "status.json")
        if legacy_status_path.exists():
            legacy_status_path.unlink()

        # After fresh variant generation, clear per-run stage history so status starts empty.
        if do_generate:
            save_run_status(run_dir, args.run_status_filename, blank_run_status(run_dir.name))
        else:
            rs = load_run_status(run_dir, args.run_status_filename)
            save_run_status(run_dir, args.run_status_filename, rs)
    refresh_project_from_runs(out_root, run_dirs, args, settings)

    if args.flow in FLOW_SIM_BEFORE_HW and do_sim:
        execute_sim_stages(out_root, run_dirs, args, settings)
        do_sim = False

    # Stage 4 and 5: HLS/HLX
    run_hls = do_hw or do_hls
    run_hlx = do_hw or do_hlx

    if run_hls:
        mark_project_stage(out_root, args, "hls", "running", command="per-run hw_gen_*.sh 1 0", timeout_sec=get_timeout(args.enable_timeouts, args.timeout_hls))
        for run_dir in run_dirs:
            status = load_run_status(run_dir, args.run_status_filename)
            if args.resume and is_stage_success(status, "hls"):
                continue

            candidates = sorted(run_dir.glob(settings["hw_gen_glob"]))
            if not candidates:
                mark_stage_result(status, "hls", "failed", 0.0, None, "missing_hw_gen_script")
                mark_stage_skipped(status, "hlx", "hls")
                save_run_status(run_dir, args.run_status_filename, status)
                continue

            cmd = ["env", "COPY_BITS=0", "bash", candidates[0].name, "1", "0"]
            if args.dry_run:
                cmd = ["env", "DRY_RUN=1"] + cmd
            timeout_hls = get_timeout(args.enable_timeouts, args.timeout_hls)
            mark_stage_running(status, "hls", cmd, timeout_hls)
            status["stages"]["hls"]["log_path"] = stage_log_path("hls", run_dir, settings, out_root)
            save_run_status(run_dir, args.run_status_filename, status)

            result = run_cmd_timed(cmd, run_dir, timeout_hls)
            if result["timed_out"]:
                mark_stage_result(status, "hls", "timeout", result["duration_sec"], None, "timeout")
                mark_stage_skipped(status, "hlx", "hls")
            elif result["ok"]:
                mark_stage_result(status, "hls", "success", result["duration_sec"], 0, None)
            else:
                mark_stage_result(status, "hls", "failed", result["duration_sec"], result["return_code"], "command_failed")
                mark_stage_skipped(status, "hlx", "hls")
            save_run_status(run_dir, args.run_status_filename, status)

        mark_project_stage(out_root, args, "hls", "success")
        refresh_project_from_runs(out_root, run_dirs, args, settings)

    if run_hlx:
        mark_project_stage(out_root, args, "hlx", "running", command="per-run hw_gen_*.sh 0 1", timeout_sec=get_timeout(args.enable_timeouts, args.timeout_hlx))
        for run_dir in run_dirs:
            status = load_run_status(run_dir, args.run_status_filename)

            if not is_stage_success(status, "hls"):
                mark_stage_skipped(status, "hlx", "hls")
                save_run_status(run_dir, args.run_status_filename, status)
                continue
            if args.resume and is_stage_success(status, "hlx"):
                continue

            candidates = sorted(run_dir.glob(settings["hw_gen_glob"]))
            if not candidates:
                mark_stage_result(status, "hlx", "failed", 0.0, None, "missing_hw_gen_script")
                save_run_status(run_dir, args.run_status_filename, status)
                continue

            cmd = ["env", "COPY_BITS=1", "bash", candidates[0].name, "0", "1"]
            if args.dry_run:
                cmd = ["env", "DRY_RUN=1"] + cmd
            timeout_hlx = get_timeout(args.enable_timeouts, args.timeout_hlx)
            mark_stage_running(status, "hlx", cmd, timeout_hlx)
            status["stages"]["hlx"]["log_path"] = stage_log_path("hlx", run_dir, settings, out_root)
            save_run_status(run_dir, args.run_status_filename, status)

            result = run_cmd_timed(cmd, run_dir, timeout_hlx)
            if result["timed_out"]:
                mark_stage_result(status, "hlx", "timeout", result["duration_sec"], None, "timeout")
            elif result["ok"]:
                mark_stage_result(status, "hlx", "success", result["duration_sec"], 0, None)
            else:
                mark_stage_result(status, "hlx", "failed", result["duration_sec"], result["return_code"], "command_failed")
            save_run_status(run_dir, args.run_status_filename, status)

        mark_project_stage(out_root, args, "hlx", "success")
        refresh_project_from_runs(out_root, run_dirs, args, settings)

    # Stage 2 and 3: sim binary + simulation
    if do_sim:
        execute_sim_stages(out_root, run_dirs, args, settings)

    # Stage 6 and 7: FPGA compile + mapping/execute (driven by run script)
    if do_run:
        mark_project_stage(out_root, args, "fpga_binary_compilation", "running", command="per-run run_*.sh")
        mark_project_stage(out_root, args, "fpga_mapping_experiment_execution", "running", command="per-run run_*.sh")

        timeout_run = None
        if args.enable_timeouts:
            timeout_run = args.timeout_fpga_compile + args.timeout_fpga_exec

        for run_dir in run_dirs:
            status = load_run_status(run_dir, args.run_status_filename)

            if not is_stage_success(status, "hlx"):
                mark_stage_skipped(status, "fpga_binary_compilation", "hlx")
                mark_stage_skipped(status, "fpga_mapping_experiment_execution", "hlx")
                save_run_status(run_dir, args.run_status_filename, status)
                continue

            if args.resume and is_stage_success(status, "fpga_binary_compilation") and is_stage_success(status, "fpga_mapping_experiment_execution"):
                continue

            candidates = sorted(run_dir.glob(settings["run_glob"]))
            if not candidates:
                mark_stage_result(status, "fpga_binary_compilation", "failed", 0.0, None, "missing_run_script")
                mark_stage_skipped(status, "fpga_mapping_experiment_execution", "fpga_binary_compilation")
                save_run_status(run_dir, args.run_status_filename, status)
                continue

            cmd = ["bash", candidates[0].name]
            if args.dry_run:
                cmd = ["env", "DRY_RUN=1"] + cmd

            mark_stage_running(status, "fpga_binary_compilation", cmd, get_timeout(args.enable_timeouts, args.timeout_fpga_compile))
            mark_stage_running(status, "fpga_mapping_experiment_execution", cmd, get_timeout(args.enable_timeouts, args.timeout_fpga_exec))
            run_log_path = stage_log_path("fpga_binary_compilation", run_dir, settings, out_root)
            status["stages"]["fpga_binary_compilation"]["log_path"] = run_log_path
            status["stages"]["fpga_mapping_experiment_execution"]["log_path"] = run_log_path
            save_run_status(run_dir, args.run_status_filename, status)

            result = run_cmd_timed(cmd, run_dir, timeout_run)

            run_log_path = Path(stage_log_path("fpga_binary_compilation", run_dir, settings, out_root))
            bazel_ok = run_log_path.exists() or result["ok"]
            remote_ok = bool(result["ok"] and remote_run_success_from_log(run_log_path) is not False)

            if result["timed_out"]:
                mark_stage_result(status, "fpga_binary_compilation", "timeout", result["duration_sec"], None, "timeout")
                mark_stage_skipped(status, "fpga_mapping_experiment_execution", "fpga_binary_compilation")
            else:
                if bazel_ok:
                    mark_stage_result(status, "fpga_binary_compilation", "success", result["duration_sec"], 0, None)
                else:
                    reason = "command_failed" if not result["ok"] else "unknown_compile_failure"
                    mark_stage_result(status, "fpga_binary_compilation", "failed", result["duration_sec"], result["return_code"], reason)

                if not bazel_ok:
                    mark_stage_skipped(status, "fpga_mapping_experiment_execution", "fpga_binary_compilation")
                elif remote_ok:
                    mark_stage_result(status, "fpga_mapping_experiment_execution", "success", result["duration_sec"], 0, None)
                else:
                    reason = "command_failed" if not result["ok"] else "unknown_execution_failure"
                    mark_stage_result(status, "fpga_mapping_experiment_execution", "failed", result["duration_sec"], result["return_code"], reason)

            save_run_status(run_dir, args.run_status_filename, status)

        mark_project_stage(out_root, args, "fpga_binary_compilation", "success")
        mark_project_stage(out_root, args, "fpga_mapping_experiment_execution", "success")
        refresh_project_from_runs(out_root, run_dirs, args, settings)

    # Stage 8: collection always runs.
    if do_collect:
        mark_project_collection_status(out_root, args, result_status="running", dataset_status="running")

        collect_a = run_cmd_timed(
            ["bash", settings["collect_results_name"]],
            out_root,
            None,
            log_path=project_log_path(out_root, "collect_results.log"),
        )
        collect_b = run_cmd_timed(
            ["bash", settings["collect_dataset_name"]],
            out_root,
            None,
            log_path=project_log_path(out_root, "collect_dataset.log"),
        )

        for run_dir in run_dirs:
            status = load_run_status(run_dir, args.run_status_filename)
            status["stages"]["results_collection"]["log_path"] = stage_log_path("results_collection", run_dir, settings, out_root)
            if collect_a["ok"] and collect_b["ok"]:
                mark_stage_result(status, "results_collection", "success", collect_a["duration_sec"] + collect_b["duration_sec"], 0, None)
            else:
                mark_stage_result(status, "results_collection", "failed", collect_a["duration_sec"] + collect_b["duration_sec"], None, "collection_failed")
            save_run_status(run_dir, args.run_status_filename, status)

        mark_project_collection_status(
            out_root,
            args,
            result_status="success" if collect_a["ok"] else "failed",
            dataset_status="success" if collect_b["ok"] else "failed",
        )
        refresh_project_from_runs(out_root, run_dirs, args, settings)

    if do_parse:
        dataset_dir = out_root / settings["dataset_dir"]
        if not dataset_dir.exists():
            raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}. Run collect stage first.")
        run_cmd([sys.executable, str(parse_hw), "--dataset", str(dataset_dir)], repo_root)
        run_cmd([sys.executable, str(parse_perf), "--dataset", str(dataset_dir)], repo_root)
        run_cmd([sys.executable, str(parse_hls), "--dataset", str(dataset_dir)], repo_root)

    refresh_project_from_runs(out_root, run_dirs, args, settings)

    dataset_dir = out_root / settings["dataset_dir"]
    hw_csv = dataset_dir / "results_summary.csv"
    perf_csv = dataset_dir / "performance_summary.csv"
    hls_csv = dataset_dir / "hls_summary.csv"
    print("\nOutputs")
    print(f"- Runs root: {out_root}")
    print(f"- Project status: {out_root / args.project_status_filename}")
    if hw_csv.exists():
        print(f"- Hardware metrics: {hw_csv}")
    if perf_csv.exists():
        print(f"- Performance metrics: {perf_csv}")
    if hls_csv.exists():
        print(f"- HLS metrics: {hls_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
