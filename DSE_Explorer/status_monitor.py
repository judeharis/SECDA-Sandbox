#!/usr/bin/env python3
"""Status Monitor for DSE Explorer runs.

Scans run folders and generates/updates status.json based on existing artifacts.
Can be used to reconstruct status for runs that predate the status tracking,
or to refresh status after manual changes.

Usage:
    python3 DSE_Explorer/status_monitor.py DSE_Explorer/generated/mm_exp_v1
    python3 DSE_Explorer/status_monitor.py DSE_Explorer/generated/mm_exp_v1/0001
    python3 DSE_Explorer/status_monitor.py DSE_Explorer/generated/mm_exp_v1 --summary
"""

from pathlib import Path
import argparse
import json
import re
import os
import datetime


DEFAULT_STATUS_FILENAME = "status.json"
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "dse_setting.json"

# Keys in status.json
STAGES = ["hls", "hlx", "bazel_build", "remote_run"]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _blank_stage() -> dict:
    return {
        "attempted": False,
        "success": None,
        "exit_code": None,
        "timestamp": None,
        "duration_seconds": None,
    }


def blank_status() -> dict:
    return {stage: _blank_stage() for stage in STAGES}


def load_status(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return blank_status()


def save_status(path: Path, status: dict) -> None:
    path.write_text(json.dumps(status, indent=2) + "\n")


# ---------------------------------------------------------------------------
# HLS log duration extraction
# ---------------------------------------------------------------------------

_VIVADO_HLS_ELAPSED_RE = re.compile(
    r"(?:Elapsed|Total elapsed)\s+time[:\s]+(\d+)\s*(?:hours?|h)[,\s]+(\d+)\s*(?:minutes?|min|m)[,\s]+(\d+)\s*(?:seconds?|sec|s)",
    re.IGNORECASE,
)
_HLS_FINISHED_RE = re.compile(r"Finished\s+(?:Generating|C synthesis)", re.IGNORECASE)
_HLS_ERROR_RE = re.compile(r"(?:ERROR|CRITICAL WARNING.*synthesis failed)", re.IGNORECASE)


def _parse_hls_duration(log_path: Path) -> float | None:
    """Try to extract elapsed time from an HLS log."""
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    matches = list(_VIVADO_HLS_ELAPSED_RE.finditer(text))
    if matches:
        m = matches[-1]
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def _hls_success_from_log(log_path: Path) -> bool | None:
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    if _HLS_ERROR_RE.search(text):
        return False
    if _HLS_FINISHED_RE.search(text):
        return True
    return None


# ---------------------------------------------------------------------------
# HLX log duration extraction
# ---------------------------------------------------------------------------

_HLX_ELAPSED_RE = re.compile(
    r"(?:Elapsed|Total elapsed)\s+time[:\s]+(\d+)\s*(?:hours?|h)[,\s]+(\d+)\s*(?:minutes?|min|m)[,\s]+(\d+)\s*(?:seconds?|sec|s)",
    re.IGNORECASE,
)
_HLX_WRITE_BITSTREAM_RE = re.compile(r"write_bitstream completed successfully", re.IGNORECASE)
_HLX_ERROR_RE = re.compile(r"\[(?:Impl|Route|Place|Opt)\s+\d+-\d+\]\s*ERROR|ERROR:\s*\[", re.IGNORECASE)


def _parse_hlx_duration(log_path: Path) -> float | None:
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    matches = list(_HLX_ELAPSED_RE.finditer(text))
    if matches:
        m = matches[-1]
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def _hlx_success_from_log(log_path: Path) -> bool | None:
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    if _HLX_ERROR_RE.search(text):
        return False
    if _HLX_WRITE_BITSTREAM_RE.search(text):
        return True
    return None


# ---------------------------------------------------------------------------
# Bazel build detection
# ---------------------------------------------------------------------------

def _detect_bazel_build(run_dir: Path, results_dir: Path) -> dict:
    """Detect bazel build status from filesystem artifacts."""
    stage = _blank_stage()
    # If a run log exists, bazel was at least attempted (it runs before remote exec)
    run_logs = list(results_dir.glob("run_remote_*.log"))
    if not run_logs:
        return stage
    # If the run log exists, bazel succeeded (script exits on build failure before logging)
    stage["attempted"] = True
    stage["success"] = True
    # Use the run log modification time as timestamp
    newest = max(run_logs, key=lambda p: p.stat().st_mtime)
    stage["timestamp"] = datetime.datetime.fromtimestamp(
        newest.stat().st_mtime, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return stage


# ---------------------------------------------------------------------------
# Remote run detection
# ---------------------------------------------------------------------------

def _detect_remote_run(run_dir: Path, results_dir: Path) -> dict:
    stage = _blank_stage()
    run_logs = sorted(results_dir.glob("run_remote_*.log"))
    if not run_logs:
        return stage
    stage["attempted"] = True
    newest = max(run_logs, key=lambda p: p.stat().st_mtime)
    stage["timestamp"] = datetime.datetime.fromtimestamp(
        newest.stat().st_mtime, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Check log content for success indicators
    try:
        text = newest.read_text(errors="replace")
    except OSError:
        text = ""
    if "Summary: run_id=" in text:
        stage["success"] = True
    elif text.strip():
        # Log exists but no summary line – likely a failure or incomplete
        stage["success"] = False
    return stage


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------

def scan_run_dir(run_dir: Path, settings: dict) -> dict:
    """Inspect a single run directory and return an updated status dict."""
    status_file = run_dir / settings.get("status_filename", DEFAULT_STATUS_FILENAME)
    status = load_status(status_file)

    results_dir = run_dir / settings.get("results_dir_name", "results")
    hw_gen_dir = run_dir / settings.get("hw_gen_dir_name", "hardware_gen")

    # --- HLS ---
    hls_log = results_dir / "outputHLS.log"
    if not hls_log.exists():
        # Also check inside hw_gen subdirs
        for sub in hw_gen_dir.iterdir() if hw_gen_dir.exists() else []:
            candidate = sub / "outputHLS.log"
            if candidate.exists():
                hls_log = candidate
                break
    if hls_log.exists():
        status["hls"]["attempted"] = True
        success = _hls_success_from_log(hls_log)
        if success is not None:
            status["hls"]["success"] = success
        duration = _parse_hls_duration(hls_log)
        if duration is not None:
            status["hls"]["duration_seconds"] = duration
        if not status["hls"]["timestamp"]:
            status["hls"]["timestamp"] = datetime.datetime.fromtimestamp(
                hls_log.stat().st_mtime, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- HLX ---
    hlx_log = results_dir / "outputHLX.log"
    if not hlx_log.exists():
        for sub in hw_gen_dir.iterdir() if hw_gen_dir.exists() else []:
            candidate = sub / "outputHLX.log"
            if candidate.exists():
                hlx_log = candidate
                break
    if hlx_log.exists():
        status["hlx"]["attempted"] = True
        success = _hlx_success_from_log(hlx_log)
        if success is not None:
            status["hlx"]["success"] = success
        duration = _parse_hlx_duration(hlx_log)
        if duration is not None:
            status["hlx"]["duration_seconds"] = duration
        if not status["hlx"]["timestamp"]:
            status["hlx"]["timestamp"] = datetime.datetime.fromtimestamp(
                hlx_log.stat().st_mtime, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Also detect HLX success from bitstream/report presence
    if status["hlx"]["success"] is None and hw_gen_dir.exists():
        for sub in hw_gen_dir.iterdir():
            gen_dir = sub / settings.get("generated_files_dir", "generated_files")
            if gen_dir.exists() and list(gen_dir.glob("*.bit")):
                status["hlx"]["attempted"] = True
                status["hlx"]["success"] = True
                break

    # --- Bazel build ---
    bazel_detected = _detect_bazel_build(run_dir, results_dir)
    if bazel_detected["attempted"]:
        # Only overwrite if we didn't already have script-recorded data
        if not status["bazel_build"]["attempted"]:
            status["bazel_build"] = bazel_detected

    # --- Remote run ---
    remote_detected = _detect_remote_run(run_dir, results_dir)
    if remote_detected["attempted"]:
        if not status["remote_run"]["attempted"]:
            status["remote_run"] = remote_detected

    return status


def is_run_dir(d: Path, settings: dict) -> bool:
    """Heuristic: a run dir has hw_params.json or an accelerator/ subfolder."""
    hw_params = settings.get("hw_params_filename", "hw_params.json")
    return d.is_dir() and (
        (d / hw_params).exists()
        or (d / "accelerator").exists()
        or (d / settings.get("hw_gen_dir_name", "hardware_gen")).exists()
    )


def scan_experiment(exp_root: Path, settings: dict, *, save: bool = True) -> dict[str, dict]:
    """Scan all run directories under exp_root. Returns {run_name: status}."""
    all_status = {}
    status_fn = settings.get("status_filename", DEFAULT_STATUS_FILENAME)
    for d in sorted(exp_root.iterdir()):
        if not is_run_dir(d, settings):
            continue
        status = scan_run_dir(d, settings)
        if save:
            save_status(d / status_fn, status)
        all_status[d.name] = status
    return all_status


def print_summary(all_status: dict[str, dict]) -> None:
    header = f"{'Run':<10} {'HLS':>12} {'HLX':>12} {'Bazel':>12} {'Remote':>12}"
    print(header)
    print("-" * len(header))
    for run_name, st in sorted(all_status.items()):
        cols = []
        for stage in STAGES:
            s = st.get(stage, {})
            if not s.get("attempted"):
                cols.append("--")
            elif s.get("success") is True:
                dur = s.get("duration_seconds")
                if dur is not None:
                    m, sec = divmod(int(dur), 60)
                    h, m = divmod(m, 60)
                    cols.append(f"OK {h}h{m:02d}m{sec:02d}s")
                else:
                    cols.append("OK")
            elif s.get("success") is False:
                cols.append("FAIL")
            else:
                cols.append("running?")
        print(f"{run_name:<10} {cols[0]:>12} {cols[1]:>12} {cols[2]:>12} {cols[3]:>12}")


def load_settings(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def main():
    p = argparse.ArgumentParser(description="Scan DSE run folders and update status.json files")
    p.add_argument("path", help="Path to an experiment output folder or a single run folder")
    p.add_argument("--settings", default=None, help="Path to dse_setting.json")
    p.add_argument("--summary", action="store_true", help="Print a summary table")
    p.add_argument("--no-write", action="store_true", help="Don't write status files, just print")
    args = p.parse_args()

    settings_path = Path(args.settings) if args.settings else DEFAULT_SETTINGS_PATH
    settings = load_settings(settings_path)

    target = Path(args.path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {target}")

    if is_run_dir(target, settings):
        # Single run
        status = scan_run_dir(target, settings)
        status_fn = settings.get("status_filename", DEFAULT_STATUS_FILENAME)
        if not args.no_write:
            save_status(target / status_fn, status)
            print(f"Updated {target / status_fn}")
        if args.summary or args.no_write:
            print_summary({target.name: status})
    else:
        # Experiment root
        all_status = scan_experiment(target, settings, save=not args.no_write)
        if not args.no_write:
            print(f"Updated {len(all_status)} run status files under {target}")
        if args.summary or args.no_write:
            print_summary(all_status)


if __name__ == "__main__":
    main()
