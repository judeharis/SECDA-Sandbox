#!/usr/bin/env python3
"""Unified DSE runner for SECDA-Sandbox.

This orchestrator drives the existing DSE_Explorer pipeline with explicit stages:
- generate: create parameterized run folders and scripts
- hw: run hardware generation (HLS/HLX)
- run: compile, upload, and execute on target board
- sim: build and execute simulation binaries on host machine
- collect: gather per-run outputs and build dataset/
- parse: parse hardware/performance metrics into CSV summaries
- all: run all stages in order

It also supports --resume to skip run folders that are already successful according
 to status.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def run_cmd(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


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
        # Skip dataset and aggregate folders.
        if d.name in {"dataset", "all_results"}:
            continue
        if (d / "hw_params.json").exists():
            yield d


def load_status(run_dir: Path, status_filename: str) -> dict:
    p = run_dir / status_filename
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def is_hw_done(status: dict) -> bool:
    hls_ok = bool((status.get("hls") or {}).get("success") is True)
    hlx_ok = bool((status.get("hlx") or {}).get("success") is True)
    return hls_ok and hlx_ok


def is_run_done(status: dict) -> bool:
    bazel_ok = bool((status.get("bazel_build") or {}).get("success") is True)
    remote_ok = bool((status.get("remote_run") or {}).get("success") is True)
    return bazel_ok and remote_ok


def is_sim_done(run_dir: Path) -> bool:
    results_dir = run_dir / "results"
    if not results_dir.exists():
        return False
    return any(results_dir.glob("*_sim.log"))


def is_hls_done(status: dict) -> bool:
    return bool((status.get("hls") or {}).get("success") is True)


def is_hlx_done(status: dict) -> bool:
    return bool((status.get("hlx") or {}).get("success") is True)


def ensure_hls_done_before_hlx(out_root: Path, status_filename: str) -> None:
    missing: list[str] = []
    for run_dir in iter_run_dirs(out_root):
        st = load_status(run_dir, status_filename)
        if not is_hls_done(st):
            missing.append(run_dir.name)

    if missing:
        preview = ", ".join(missing[:10])
        extra = "" if len(missing) <= 10 else f" ... (+{len(missing) - 10} more)"
        raise RuntimeError(
            "HLX stage requires HLS to be completed first. "
            "Run HLS before HLX, for example: "
            "python3 dse_run.py --experiment <exp_path> --stage hls "
            "[--resume]. "
            f"Runs missing HLS success in status.json: {preview}{extra}"
        )


def summarize_status(out_root: Path, status_filename: str) -> None:
    total = 0
    hw_done = 0
    run_done = 0
    for run_dir in iter_run_dirs(out_root):
        total += 1
        st = load_status(run_dir, status_filename)
        if is_hw_done(st):
            hw_done += 1
        if is_run_done(st):
            run_done += 1

    print("\nSummary")
    print(f"- Output root: {out_root}")
    print(f"- Runs: {total}")
    print(f"- HW completed (hls+hlx): {hw_done}/{total}")
    print(f"- Run completed (bazel+remote): {run_done}/{total}")


def main() -> int:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent
    default_settings = repo_root / "DSE_Explorer" / "dse_setting.json"

    p = argparse.ArgumentParser(description="Unified DSE runner")
    p.add_argument(
        "--experiment",
        required=True,
        help="Experiment path, e.g. experiments/mm_exp/v1",
    )
    p.add_argument(
        "--settings",
        default=str(default_settings),
        help="Path to dse_setting.json",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Base output directory (defaults to dse_setting.json output_root)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Deterministic sample size for generation",
    )
    p.add_argument(
        "--stage",
        choices=["generate", "hls", "hlx", "hw", "run", "sim", "collect", "parse", "all"],
        default="all",
        help="Pipeline stage to execute",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip run folders already successful according to status.json",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Forward dry-run mode to generated scripts when possible",
    )
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

    dse_explorer = repo_root / "DSE_Explorer" / "dse_explorer.py"
    parse_hw = repo_root / "DSE_Explorer" / "parse_hardware.py"
    parse_perf = repo_root / "DSE_Explorer" / "parse_performance.py"

    do_generate = args.stage in {"generate", "all"}
    do_hls = args.stage in {"hls", "all"}
    do_hlx = args.stage in {"hlx", "all"}
    do_hw = args.stage in {"hw", "all"}
    do_run = args.stage in {"run", "all"}
    do_sim = args.stage in {"sim", "all"}
    do_collect = args.stage in {"collect", "all"}
    do_parse = args.stage in {"parse", "all"}

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
        run_cmd(cmd, repo_root)

    if (do_hw or do_run or do_sim or do_collect or do_parse) and not out_root.exists():
        raise FileNotFoundError(
            f"Output root does not exist: {out_root}. Run generate stage first."
        )

    hw_mode: tuple[int, int] | None = None
    hw_label = ""
    if do_hw:
        hw_mode = (1, 1)
        hw_label = "HW"
    elif do_hls:
        hw_mode = (1, 0)
        hw_label = "HLS"
    elif do_hlx:
        hw_mode = (0, 1)
        hw_label = "HLX"

    if hw_mode is not None:
        run_hls, run_hlx = hw_mode
        copy_bits = 1 if run_hlx == 1 else 0
        if hw_mode == (0, 1):
            ensure_hls_done_before_hlx(out_root, settings["status_filename"])
        if args.resume:
            attempted = 0
            skipped = 0
            for run_dir in iter_run_dirs(out_root):
                st = load_status(run_dir, settings["status_filename"])
                if hw_mode == (1, 1) and is_hw_done(st):
                    skipped += 1
                    continue
                if hw_mode == (1, 0) and is_hls_done(st):
                    skipped += 1
                    continue
                if hw_mode == (0, 1) and is_hlx_done(st):
                    skipped += 1
                    continue

                candidates = sorted(run_dir.glob(settings["hw_gen_glob"]))
                if not candidates:
                    continue
                script = candidates[0]
                attempted += 1
                cmd = ["env", f"COPY_BITS={copy_bits}", "bash", script.name, str(run_hls), str(run_hlx)]
                if args.dry_run:
                    cmd = ["env", "DRY_RUN=1"] + cmd
                run_cmd(cmd, run_dir)
            print(f"{hw_label} stage: attempted={attempted}, skipped={skipped}")
        else:
            cmd = [
                "env",
                f"RUN_HLS={run_hls}",
                f"RUN_HLX={run_hlx}",
                f"COPY_BITS={copy_bits}",
                "bash",
                settings["hw_gen_all_name"],
            ]
            if args.dry_run:
                cmd = [
                    "env",
                    f"RUN_HLS={run_hls}",
                    f"RUN_HLX={run_hlx}",
                    f"COPY_BITS={copy_bits}",
                    "DRY_RUN=1",
                    "bash",
                    settings["hw_gen_all_name"],
                ]
            run_cmd(cmd, out_root)

    if do_run:
        if args.resume:
            attempted = 0
            skipped = 0
            for run_dir in iter_run_dirs(out_root):
                st = load_status(run_dir, settings["status_filename"])
                if is_run_done(st):
                    skipped += 1
                    continue
                candidates = sorted(run_dir.glob(settings["run_glob"]))
                if not candidates:
                    continue
                script = candidates[0]
                attempted += 1
                cmd = ["bash", script.name]
                if args.dry_run:
                    cmd = ["env", "DRY_RUN=1"] + cmd
                run_cmd(cmd, run_dir)
            print(f"Run stage: attempted={attempted}, skipped={skipped}")
        else:
            cmd = ["bash", settings["run_all_name"]]
            if args.dry_run:
                cmd = ["env", "DRY_RUN=1"] + cmd
            run_cmd(cmd, out_root)

    if do_sim:
        if args.resume:
            attempted = 0
            skipped = 0
            for run_dir in iter_run_dirs(out_root):
                if is_sim_done(run_dir):
                    skipped += 1
                    continue
                candidates = sorted(run_dir.glob(settings["sim_glob"]))
                if not candidates:
                    continue
                script = candidates[0]
                attempted += 1
                cmd = ["bash", script.name]
                if args.dry_run:
                    cmd = ["env", "DRY_RUN=1"] + cmd
                run_cmd(cmd, run_dir)
            print(f"Sim stage: attempted={attempted}, skipped={skipped}")
        else:
            cmd = ["bash", settings["sim_all_name"]]
            if args.dry_run:
                cmd = ["env", "DRY_RUN=1"] + cmd
            run_cmd(cmd, out_root)

    if do_collect:
        run_cmd(["bash", settings["collect_results_name"]], out_root)
        run_cmd(["bash", settings["collect_dataset_name"]], out_root)

    if do_parse:
        dataset_dir = out_root / settings["dataset_dir"]
        if not dataset_dir.exists():
            raise FileNotFoundError(
                f"Dataset directory not found: {dataset_dir}. Run collect stage first."
            )
        run_cmd([sys.executable, str(parse_hw), "--dataset", str(dataset_dir)], repo_root)
        run_cmd([sys.executable, str(parse_perf), "--dataset", str(dataset_dir)], repo_root)

    summarize_status(out_root, settings["status_filename"])

    dataset_dir = out_root / settings["dataset_dir"]
    hw_csv = dataset_dir / "results_summary.csv"
    perf_csv = dataset_dir / "performance_summary.csv"
    print("\nOutputs")
    print(f"- Runs root: {out_root}")
    if hw_csv.exists():
        print(f"- Hardware metrics: {hw_csv}")
    if perf_csv.exists():
        print(f"- Performance metrics: {perf_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
