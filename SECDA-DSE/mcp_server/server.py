#!/usr/bin/env python3
"""MCP server for SECDA-Sandbox DSE workflows.

Exposes tools for:
- Running LLM-to-DSE experiments (llm_to_dse.py)
- Running the DSE pipeline directly (dse_run.py)
- Running DSE Explorer variant generation (dse_explorer.py)
- Building Bazel experiments
- Background job tracking (start / poll / kill)
- Reading generated results and status files
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SERVER_DIR.parent.parent  # SECDA-Sandbox/
DSE_RUN = REPO_ROOT / "dse_run.py"
LLM_TO_DSE = REPO_ROOT / "SECDA-DSE" / "llm_to_dse.py"
DSE_EXPLORER = REPO_ROOT / "DSE_Explorer" / "dse_explorer.py"
DSE_SETTINGS = REPO_ROOT / "DSE_Explorer" / "dse_setting.json"
CONFIG_JSON = REPO_ROOT / "config.json"
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
LLM_EXPERIMENTS_DIR = REPO_ROOT / "SECDA-DSE" / "llm_experiments"
INPUT_SPACE_DIR = REPO_ROOT / "SECDA-DSE" / "input_space"
DSE_GENERATED_DIR = REPO_ROOT / "DSE_Explorer" / "generated"
LLM_GENERATED_DIR = REPO_ROOT / "SECDA-DSE" / "llm_generated"

# ---------------------------------------------------------------------------
# Job registry
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_job_id() -> str:
    return uuid.uuid4().hex[:8]


def _start_job(label: str, cmd: list[str], cwd: Path) -> str:
    job_id = _new_job_id()
    log_path = _SERVER_DIR / "job_logs" / f"{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_file = log_path.open("w", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    record = {
        "job_id": job_id,
        "label": label,
        "cmd": cmd,
        "cwd": str(cwd),
        "pid": proc.pid,
        "log_path": str(log_path),
        "status": "running",
        "returncode": None,
    }

    with _jobs_lock:
        _jobs[job_id] = record

    def _watch() -> None:
        rc = proc.wait()
        log_file.close()
        with _jobs_lock:
            _jobs[job_id]["returncode"] = rc
            _jobs[job_id]["status"] = "done" if rc == 0 else "failed"

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return job_id


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "secda-sandbox",
    instructions=(
        "Tools for running and monitoring SECDA-Sandbox DSE experiments. "
        "Long-running operations return a job_id; use get_job_status / get_job_output "
        "to track them."
    ),
)

# ── Background job tools ────────────────────────────────────────────────────


@mcp.tool()
def list_jobs() -> list[dict]:
    """Return all tracked background jobs with their current status."""
    with _jobs_lock:
        return [
            {
                "job_id": j["job_id"],
                "label": j["label"],
                "status": j["status"],
                "returncode": j["returncode"],
                "pid": j["pid"],
            }
            for j in _jobs.values()
        ]


@mcp.tool()
def get_job_status(job_id: str) -> dict:
    """Return the current status record for a background job.

    Args:
        job_id: The job ID returned when the job was started.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"error": f"Unknown job_id: {job_id}"}
    return {
        "job_id": job["job_id"],
        "label": job["label"],
        "status": job["status"],
        "returncode": job["returncode"],
        "pid": job["pid"],
        "cmd": job["cmd"],
        "cwd": job["cwd"],
    }


@mcp.tool()
def get_job_output(
    job_id: str,
    tail_lines: Annotated[int, "Number of lines from the end of the log to return (0 = all)"] = 100,
) -> str:
    """Return the captured stdout/stderr output of a background job.

    Args:
        job_id: The job ID returned when the job was started.
        tail_lines: How many lines from the tail of the log to return (0 = entire log).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return f"Unknown job_id: {job_id}"
    log_path = Path(job["log_path"])
    if not log_path.exists():
        return "(no output yet)"
    text = log_path.read_text(errors="replace")
    if tail_lines > 0:
        lines = text.splitlines()
        return "\n".join(lines[-tail_lines:])
    return text


@mcp.tool()
def kill_job(job_id: str) -> dict:
    """Send SIGTERM to the process group of a running background job.

    Args:
        job_id: The job ID to kill.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"error": f"Unknown job_id: {job_id}"}
    if job["status"] != "running":
        return {"error": f"Job {job_id} is not running (status={job['status']})"}
    try:
        os.killpg(job["pid"], signal.SIGTERM)
        return {"ok": True, "message": f"SIGTERM sent to pid {job['pid']}"}
    except ProcessLookupError:
        return {"error": "Process not found (may have already exited)"}


# ── Build tools ─────────────────────────────────────────────────────────────


@mcp.tool()
def build_experiment(
    experiment: Annotated[str, "Experiment name, e.g. mm_exp"],
    version: Annotated[str, "Version subfolder, e.g. v1"] = "v1",
    extra_cxxopts: Annotated[list[str], "Additional --cxxopt flags"] = [],
) -> dict:
    """Build a SECDA Bazel experiment in SystemC simulation mode.

    Runs: bazel6 build //experiments/<experiment>/<version>:exp

    Args:
        experiment: Experiment directory name under experiments/, e.g. mm_exp.
        version: Version subfolder, e.g. v1.
        extra_cxxopts: Optional additional --cxxopt strings to append.

    Returns a dict with job_id and the full command.
    """
    target = f"//experiments/{experiment}/{version}:exp"
    cmd = [
        "bazel6", "build", target,
        "-c", "dbg",
        "--cxxopt=-DSYSC",
        "--cxxopt=-DACC_PROFILE",
        "--spawn_strategy=standalone",
        "--subcommands",
        "--platforms=//platform:linux_x64",
        "--@secda_tools//:config=sysc",
    ]
    for opt in extra_cxxopts:
        cmd += [f"--cxxopt={opt}"]

    label = f"build:{experiment}/{version}"
    job_id = _start_job(label, cmd, REPO_ROOT)
    return {"job_id": job_id, "label": label, "cmd": cmd}


# ── DSE Explorer tool ────────────────────────────────────────────────────────


@mcp.tool()
def run_dse_explorer(
    experiment_path: Annotated[str, "Relative path to experiment, e.g. experiments/mm_exp/v1"],
    output: Annotated[str, "Output directory for generated variants (default: DSE_Explorer/generated)"] = "",
    sample: Annotated[int, "Limit to this many runs deterministically (0 = all)"] = 0,
    dry_run: Annotated[bool, "Print actions without creating files"] = False,
    hw_params: Annotated[str, "Path to hw_params.json override (optional)"] = "",
) -> dict:
    """Run DSE Explorer to generate per-variant run folders from hw_params.json.

    Args:
        experiment_path: Relative path to the experiment directory.
        output: Override output directory for generated runs.
        sample: Deterministic sample size (0 = generate all variants).
        dry_run: If True, only print what would be done.
        hw_params: Optional override path to hw_params.json.

    Returns a dict with job_id.
    """
    cmd = [sys.executable, str(DSE_EXPLORER), "--experiment", experiment_path]
    if output:
        cmd += ["--output", output]
    if sample > 0:
        cmd += ["--sample", str(sample)]
    if dry_run:
        cmd += ["--dry-run"]
    if hw_params:
        cmd += ["--hw", hw_params]

    label = f"dse_explorer:{Path(experiment_path).name}"
    job_id = _start_job(label, cmd, REPO_ROOT)
    return {"job_id": job_id, "label": label, "cmd": cmd}


# ── dse_run tool ─────────────────────────────────────────────────────────────


@mcp.tool()
def run_dse(
    experiment_path: Annotated[str, "Relative path to experiment, e.g. experiments/mm_exp/v1"],
    flow: Annotated[str, "Flow preset: sim | fpga | lite | sim_lite | all"] = "sim_lite",
    output: Annotated[str, "Override output directory"] = "",
    sample: Annotated[int, "Deterministic sample size (0 = all)"] = 0,
    resume: Annotated[bool, "Skip runs already marked successful"] = False,
    dry_run: Annotated[bool, "Forward dry-run mode to generated scripts"] = False,
    enable_timeouts: Annotated[bool, "Enable per-stage timeouts"] = True,
    timeout_sim_bin: Annotated[int, "Timeout in seconds for sim binary generation"] = 900,
    timeout_sim_run: Annotated[int, "Timeout in seconds for SystemC simulation"] = 1800,
    timeout_hls: Annotated[int, "Timeout in seconds for HLS"] = 3600,
    timeout_hlx: Annotated[int, "Timeout in seconds for HLX"] = 10800,
    timeout_fpga_compile: Annotated[int, "Timeout in seconds for FPGA binary compile"] = 1800,
    timeout_fpga_exec: Annotated[int, "Timeout in seconds for FPGA execution"] = 1800,
) -> dict:
    """Run the full DSE pipeline (generate → sim/hls/hlx/run → collect → parse).

    Args:
        experiment_path: Path to the experiment directory (relative to repo root).
        flow: Flow preset controlling which stages execute.
        output: Override base output directory.
        sample: Limit variant count deterministically.
        resume: Skip runs with existing successful status.
        dry_run: Pass --dry-run to generated scripts.
        enable_timeouts: Enable per-stage process timeouts.
        timeout_sim_bin: Seconds before sim binary generation is killed.
        timeout_sim_run: Seconds before SystemC simulation is killed.
        timeout_hls: Seconds before HLS synthesis is killed.
        timeout_hlx: Seconds before HLX is killed.
        timeout_fpga_compile: Seconds before FPGA bitstream compile is killed.
        timeout_fpga_exec: Seconds before FPGA execution is killed.

    Returns a dict with job_id.
    """
    valid_flows = {"sim", "fpga", "lite", "sim_lite", "all"}
    if flow not in valid_flows:
        return {"error": f"Invalid flow '{flow}'. Must be one of: {sorted(valid_flows)}"}

    cmd = [
        sys.executable, str(DSE_RUN),
        "--experiment", experiment_path,
        "--flow", flow,
    ]
    if output:
        cmd += ["--output", output]
    if sample > 0:
        cmd += ["--sample", str(sample)]
    if resume:
        cmd += ["--resume"]
    if dry_run:
        cmd += ["--dry-run"]
    if not enable_timeouts:
        cmd += ["--no-enable-timeouts"]
    if timeout_sim_bin != 900:
        cmd += ["--timeout-sim-bin", str(timeout_sim_bin)]
    if timeout_sim_run != 1800:
        cmd += ["--timeout-sim-run", str(timeout_sim_run)]
    if timeout_hls != 3600:
        cmd += ["--timeout-hls", str(timeout_hls)]
    if timeout_hlx != 10800:
        cmd += ["--timeout-hlx", str(timeout_hlx)]
    if timeout_fpga_compile != 1800:
        cmd += ["--timeout-fpga-compile", str(timeout_fpga_compile)]
    if timeout_fpga_exec != 1800:
        cmd += ["--timeout-fpga-exec", str(timeout_fpga_exec)]

    label = f"dse_run:{Path(experiment_path).name}:{flow}"
    job_id = _start_job(label, cmd, REPO_ROOT)
    return {"job_id": job_id, "label": label, "cmd": cmd}


# ── llm_to_dse tool ──────────────────────────────────────────────────────────


@mcp.tool()
def run_llm_to_dse(
    input_dir: Annotated[str, "Source LLM experiment folder (relative or absolute)"],
    experiment_name: Annotated[str, "Target experiment name under SECDA-DSE/llm_experiments"],
    version: Annotated[str, "Version folder name, e.g. v1"] = "v1",
    target_board: Annotated[str, "Board: KRIA or Z1"] = "KRIA",
    flow: Annotated[str, "dse_run flow: sim | fpga | lite | sim_lite | all"] = "sim_lite",
    output_root: Annotated[str, "Override output root for generated DSE runs"] = "",
    sample: Annotated[int, "Deterministic sample size (0 = all)"] = 0,
    force: Annotated[bool, "Overwrite existing destination experiment folder"] = False,
    no_run: Annotated[bool, "Only validate/normalize/place; do not execute DSE stages"] = False,
    dry_run: Annotated[bool, "Forward dry-run to dse_run stages"] = False,
    strict_sim: Annotated[bool, "Fail immediately if sim stage fails"] = False,
    resume: Annotated[bool, "Pass --resume to dse_run"] = False,
    keep_input: Annotated[bool, "Copy source instead of moving it"] = True,
    enable_timeouts: Annotated[bool, "Enable per-stage timeouts"] = True,
    timeout_sim_bin: Annotated[int, "Timeout seconds for sim binary generation"] = 900,
    timeout_sim_run: Annotated[int, "Timeout seconds for SystemC simulation"] = 1800,
    timeout_hls: Annotated[int, "Timeout seconds for HLS"] = 3600,
    timeout_hlx: Annotated[int, "Timeout seconds for HLX"] = 10800,
    timeout_fpga_compile: Annotated[int, "Timeout seconds for FPGA binary compile"] = 1800,
    timeout_fpga_exec: Annotated[int, "Timeout seconds for FPGA execution"] = 1800,
) -> dict:
    """Validate, normalise, and place an LLM-generated experiment, then run dse_run.

    Wraps SECDA-DSE/llm_to_dse.py. The experiment is copied into
    SECDA-DSE/llm_experiments/<experiment_name>/<version> and the DSE
    pipeline is started with the chosen flow.

    Args:
        input_dir: Path to the source experiment folder containing BUILD,
                   experiment.cc, hw_params.json, and accelerator/.
        experiment_name: Name to give the experiment under llm_experiments/.
        version: Version subfolder (must match vN pattern, e.g. v1).
        target_board: FPGA board to target – normalises hw_params.json.
        flow: Which DSE stages to run.
        output_root: Override default SECDA-DSE/llm_generated output root.
        sample: Randomly sample this many variants (0 = all).
        force: Overwrite any pre-existing destination folder.
        no_run: Stop after placement without running DSE stages.
        dry_run: Forward --dry-run to dse_run.py.
        strict_sim: Exit on first sim failure.
        resume: Skip already-successful runs.
        keep_input: Copy (True) or move (False) the source folder.
        enable_timeouts: Enable per-stage process timeouts.
        timeout_sim_bin / timeout_sim_run / timeout_hls / timeout_hlx /
        timeout_fpga_compile / timeout_fpga_exec: Stage timeout overrides.

    Returns a dict with job_id.
    """
    valid_boards = {"KRIA", "Z1"}
    if target_board not in valid_boards:
        return {"error": f"Invalid board '{target_board}'. Must be one of: {sorted(valid_boards)}"}

    valid_flows = {"sim", "fpga", "lite", "sim_lite", "all"}
    if flow not in valid_flows:
        return {"error": f"Invalid flow '{flow}'. Must be one of: {sorted(valid_flows)}"}

    # Resolve input_dir relative to repo root if not absolute
    inp = Path(input_dir)
    if not inp.is_absolute():
        inp = REPO_ROOT / inp

    cmd = [
        sys.executable, str(LLM_TO_DSE),
        "--input", str(inp),
        "--experiment-name", experiment_name,
        "--version", version,
        "--target-board", target_board,
        "--flow", flow,
    ]
    if output_root:
        cmd += ["--output-root", output_root]
    if sample > 0:
        cmd += ["--sample", str(sample)]
    if force:
        cmd += ["--force"]
    if no_run:
        cmd += ["--no-run"]
    if dry_run:
        cmd += ["--dry-run"]
    if strict_sim:
        cmd += ["--strict-sim"]
    if resume:
        cmd += ["--resume"]
    if not keep_input:
        cmd += ["--no-keep-input"]
    if not enable_timeouts:
        cmd += ["--no-enable-timeouts"]
    if timeout_sim_bin != 900:
        cmd += ["--timeout-sim-bin", str(timeout_sim_bin)]
    if timeout_sim_run != 1800:
        cmd += ["--timeout-sim-run", str(timeout_sim_run)]
    if timeout_hls != 3600:
        cmd += ["--timeout-hls", str(timeout_hls)]
    if timeout_hlx != 10800:
        cmd += ["--timeout-hlx", str(timeout_hlx)]
    if timeout_fpga_compile != 1800:
        cmd += ["--timeout-fpga-compile", str(timeout_fpga_compile)]
    if timeout_fpga_exec != 1800:
        cmd += ["--timeout-fpga-exec", str(timeout_fpga_exec)]

    label = f"llm_to_dse:{experiment_name}/{version}:{flow}"
    job_id = _start_job(label, cmd, REPO_ROOT)
    return {"job_id": job_id, "label": label, "cmd": cmd}


# ── Listing / discovery tools ────────────────────────────────────────────────


@mcp.tool()
def list_experiments() -> list[dict]:
    """List all experiments in the experiments/ directory with their versions."""
    if not EXPERIMENTS_DIR.exists():
        return []
    result = []
    for exp_dir in sorted(EXPERIMENTS_DIR.iterdir()):
        if not exp_dir.is_dir() or exp_dir.name == "__pycache__":
            continue
        versions = [v.name for v in sorted(exp_dir.iterdir()) if v.is_dir()]
        result.append({"experiment": exp_dir.name, "versions": versions})
    return result


@mcp.tool()
def list_llm_experiments() -> list[dict]:
    """List all LLM-generated experiments placed in SECDA-DSE/llm_experiments/."""
    if not LLM_EXPERIMENTS_DIR.exists():
        return []
    result = []
    for exp_dir in sorted(LLM_EXPERIMENTS_DIR.iterdir()):
        if not exp_dir.is_dir():
            continue
        versions = [v.name for v in sorted(exp_dir.iterdir()) if v.is_dir()]
        result.append({"experiment": exp_dir.name, "versions": versions})
    return result


@mcp.tool()
def list_input_space() -> list[dict]:
    """List available LLM experiment inputs in SECDA-DSE/input_space/."""
    if not INPUT_SPACE_DIR.exists():
        return []
    result = []
    for item in sorted(INPUT_SPACE_DIR.iterdir()):
        entry: dict = {"name": item.name, "is_dir": item.is_dir()}
        if item.is_dir():
            hw = item / "hw_params.json"
            entry["has_hw_params"] = hw.exists()
            entry["has_accelerator"] = (item / "accelerator").exists()
        result.append(entry)
    return result


@mcp.tool()
def list_generated(
    root: Annotated[str, "Which generated root to list: 'dse' or 'llm' (default: 'dse')"] = "dse",
) -> list[dict]:
    """List generated DSE experiment folders and their run counts.

    Args:
        root: 'dse' for DSE_Explorer/generated, 'llm' for SECDA-DSE/llm_generated.
    """
    base = DSE_GENERATED_DIR if root == "dse" else LLM_GENERATED_DIR
    if not base.exists():
        return []
    result = []
    for exp_folder in sorted(base.iterdir()):
        if not exp_folder.is_dir():
            continue
        run_dirs = [d.name for d in sorted(exp_folder.iterdir()) if d.is_dir() and d.name not in {"dataset", "status_logs"}]
        status_path = exp_folder / "Project_Status.json"
        status = {}
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text())
            except Exception:
                pass
        result.append({
            "folder": exp_folder.name,
            "path": str(exp_folder.relative_to(REPO_ROOT)),
            "run_count": len(run_dirs),
            "project_status": status,
        })
    return result


# ── Status / results read tools ───────────────────────────────────────────────


@mcp.tool()
def get_project_status(
    generated_folder: Annotated[str, "Path to a generated experiment folder (relative to repo root or absolute)"],
) -> dict:
    """Read Project_Status.json for a generated DSE experiment.

    Args:
        generated_folder: Path to the generated folder, e.g.
                          DSE_Explorer/generated/mm_exp_v1
    """
    folder = _resolve_generated_path(generated_folder)
    status_path = folder / "Project_Status.json"
    if not status_path.exists():
        return {"error": f"Project_Status.json not found in {folder}"}
    return json.loads(status_path.read_text())


@mcp.tool()
def get_run_status(
    generated_folder: Annotated[str, "Path to generated experiment folder (relative to repo root or absolute)"],
    run_id: Annotated[str, "Run ID subfolder name, e.g. run_000"],
) -> dict:
    """Read Run_Status.json for a specific variant run.

    Args:
        generated_folder: Path to the parent generated experiment folder.
        run_id: Name of the run subfolder (e.g. run_000, run_001).
    """
    folder = _resolve_generated_path(generated_folder)
    run_path = folder / run_id / "Run_Status.json"
    if not run_path.exists():
        return {"error": f"Run_Status.json not found at {run_path}"}
    return json.loads(run_path.read_text())


@mcp.tool()
def list_run_statuses(
    generated_folder: Annotated[str, "Path to generated experiment folder (relative to repo root or absolute)"],
    status_filter: Annotated[str, "Filter by status field value (e.g. 'done', 'failed', 'running'). Empty = return all."] = "",
) -> list[dict]:
    """Read Run_Status.json for every variant in a generated folder.

    Args:
        generated_folder: Path to the parent generated experiment folder.
        status_filter: If set, only return runs whose overall_status matches this string.
    """
    folder = _resolve_generated_path(generated_folder)
    results = []
    for run_dir in sorted(folder.iterdir()):
        if not run_dir.is_dir() or run_dir.name in {"dataset", "status_logs"}:
            continue
        status_path = run_dir / "Run_Status.json"
        if not status_path.exists():
            continue
        try:
            data = json.loads(status_path.read_text())
        except Exception as exc:
            data = {"error": str(exc)}
        data["run_id"] = run_dir.name
        overall = data.get("overall_status", "")
        if status_filter and overall != status_filter:
            continue
        results.append(data)
    return results


@mcp.tool()
def get_results_summary(
    generated_folder: Annotated[str, "Path to generated experiment folder (relative to repo root or absolute)"],
    csv_name: Annotated[str, "CSV filename: results_summary | performance_summary | hls_summary"] = "results_summary",
    max_rows: Annotated[int, "Maximum rows to return (0 = all)"] = 50,
) -> dict:
    """Read a CSV results file from the dataset/ folder of a generated experiment.

    Args:
        generated_folder: Path to the generated experiment folder.
        csv_name: Which CSV to read. One of results_summary, performance_summary,
                  or hls_summary (without the .csv extension).
        max_rows: Limit the rows returned (0 = all rows).
    """
    folder = _resolve_generated_path(generated_folder)
    csv_path = folder / "dataset" / f"{csv_name}.csv"
    if not csv_path.exists():
        return {"error": f"{csv_name}.csv not found at {csv_path}"}
    lines = csv_path.read_text().splitlines()
    if max_rows > 0:
        lines = lines[: max_rows + 1]  # include header
    return {"csv": "\n".join(lines), "total_lines": len(csv_path.read_text().splitlines())}


@mcp.tool()
def get_config() -> dict:
    """Return the contents of the top-level config.json file."""
    if not CONFIG_JSON.exists():
        return {"error": "config.json not found"}
    return json.loads(CONFIG_JSON.read_text())


@mcp.tool()
def get_hw_params(
    experiment_path: Annotated[str, "Relative path to experiment, e.g. experiments/mm_exp/v1"],
) -> dict:
    """Read hw_params.json for an experiment.

    Args:
        experiment_path: Path to the experiment directory (relative to repo root).
    """
    p = REPO_ROOT / experiment_path / "hw_params.json"
    if not p.exists():
        return {"error": f"hw_params.json not found at {p}"}
    return json.loads(p.read_text())


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_generated_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (REPO_ROOT / p).resolve()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
