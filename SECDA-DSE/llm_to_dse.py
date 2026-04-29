#!/usr/bin/env python3
"""Prepare LLM-generated experiments for DSE_Explorer and run dse_run flow.

Workflow:
1) Validate experiment folder structure.
2) Move/copy into SECDA-DSE/llm_experiments/<exp_name>/<version>.
3) Normalize hw_params.json and rewrite stale experiment paths in text files.
4) Run dse_run.py with a selected flow (default: sim_lite).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_ITEMS = [
    "BUILD",
    "experiment.cc",
    "hw_params.json",
    "accelerator",
]


def run_cmd(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def ensure_structure(source_exp: Path) -> None:
    missing = [name for name in REQUIRED_ITEMS if not (source_exp / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Input experiment is missing required items: {', '.join(missing)}"
        )

    hw = source_exp / "hw_params.json"
    data = load_json(hw)
    if "DSE" not in data or "hardware_gen" not in data:
        raise ValueError("hw_params.json must include DSE and hardware_gen sections")


def normalize_acc_name(acc_name: str, board: str) -> str:
    if not acc_name:
        return board
    cleaned = re.sub(r"_(?:Z1|KRIA)$", "", acc_name, flags=re.IGNORECASE)
    return f"{cleaned}_{board}"


def normalize_hlx_tcl(script: str, board: str) -> str:
    if board == "KRIA":
        default = "KRIA/KRIA_dma_1_hp_1_ctrl_hwc.tcl"
    else:
        default = "Z1/Z1_dma_1_hp_1_ctrl_hwc.tcl"

    if not script:
        return default

    filename = Path(script).name.strip()
    if not filename or not filename.endswith(".tcl"):
        return default

    # Ensure file name board token matches target board too, not only path prefix.
    tail = re.sub(r"^(?:Z1|KRIA)[_-]?", "", filename, flags=re.IGNORECASE)
    if not tail:
        return default
    prefix = "KRIA_" if board == "KRIA" else "Z1_"
    return f"{board}/{prefix}{tail}"


def normalize_hw_params(hw_path: Path, exp_name: str, version: str, target_board: str) -> None:
    data = load_json(hw_path)
    hg = data.setdefault("hardware_gen", {})

    hg["board"] = target_board
    hg["acc_name"] = normalize_acc_name(str(hg.get("acc_name", "")).strip(), target_board)
    hg["acc_src"] = f"SECDA-DSE/llm_experiments/{exp_name}/{version}/accelerator"
    hg["acc_link_folder"] = f"{exp_name}_src_{version}"
    hg["hlx_tcl_script"] = normalize_hlx_tcl(str(hg.get("hlx_tcl_script", "")).strip(), target_board)
    hg["del"] = exp_name
    hg["del_version"] = version[1:] if version.startswith("v") else version

    save_json(hw_path, data)


def rewrite_paths(dest_exp: Path, exp_name: str, version: str) -> int:
    replaced_files = 0
    new_label = f"//SECDA-DSE/llm_experiments/{exp_name}/{version}"
    new_plain = f"SECDA-DSE/llm_experiments/{exp_name}/{version}"

    file_patterns = [
        "BUILD",
        "BUILD.bazel",
        "*.bzl",
        "*.cc",
        "*.h",
        "*.json",
        "*.txt",
        "*.md",
    ]

    old_label_pattern = re.compile(r"//experiments/[^/]+/v\d+")
    old_plain_pattern = re.compile(r"(?<!\w)experiments/[^/]+/v\d+")

    seen: set[Path] = set()
    for pattern in file_patterns:
        for p in dest_exp.rglob(pattern):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            text = p.read_text()
            updated = old_label_pattern.sub(new_label, text)
            updated = old_plain_pattern.sub(new_plain, updated)
            if updated != text:
                p.write_text(updated)
                replaced_files += 1

    return replaced_files


def format_experiment_folder(source_exp: Path, fmt: str) -> str:
    mapping = {
        "exp_name": source_exp.parent.name,
        "exp_version": source_exp.name,
        "experiment": source_exp.name,
        "exp_path": source_exp.as_posix(),
    }
    return fmt.format_map(mapping)


def run_dse_flow(
    repo_root: Path,
    experiment: Path,
    output_root: Path,
    settings_path: Path,
    sample: int,
    dry_run: bool,
    flow: str,
    strict_sim: bool,
    monitor: bool,
    monitor_interval: float,
    resume: bool,
    project_status_filename: str,
    run_status_filename: str,
    enable_timeouts: bool,
    timeout_sim_bin: int,
    timeout_sim_run: int,
    timeout_hls: int,
    timeout_hlx: int,
    timeout_fpga_compile: int,
    timeout_fpga_exec: int,
    dse_run_args: list[str],
) -> None:
    dse_run = repo_root / "dse_run.py"
    cmd = [
        sys.executable,
        str(dse_run),
        "--experiment",
        str(experiment),
        "--settings",
        str(settings_path),
        "--output",
        str(output_root),
        "--flow",
        flow,
    ]
    if sample > 0:
        cmd += ["--sample", str(sample)]
    if monitor:
        cmd += ["--monitor"]
        if monitor_interval != 2.0:
            cmd += ["--monitor-interval", str(monitor_interval)]
    if resume:
        cmd += ["--resume"]
    if project_status_filename != "Project_Status.json":
        cmd += ["--project-status-filename", project_status_filename]
    if run_status_filename != "Run_Status.json":
        cmd += ["--run-status-filename", run_status_filename]
    cmd += ["--enable-timeouts"] if enable_timeouts else ["--no-enable-timeouts"]
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
    if dry_run:
        cmd += ["--dry-run"]
    if dse_run_args:
        cmd += dse_run_args
    try:
        run_cmd(cmd, repo_root)
    except subprocess.CalledProcessError:
        if strict_sim and flow in {"sim", "sim_lite", "all"}:
            raise
        print(
            "WARNING: dse_run flow failed for one or more runs. "
            "Use --strict-sim with a sim-containing flow to fail fast."
        )


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_settings = repo_root / "DSE_Explorer" / "dse_setting.json"

    p = argparse.ArgumentParser(description="Prepare LLM experiments and run dse_run flow")
    p.add_argument(
        "-i",
        "--input",
        default=str(repo_root / "SECDA-DSE" / "input_space" / "v1"),
        help="Path to source LLM experiment folder",
    )
    p.add_argument(
        "-e",
        "--experiment-name",
        required=True,
        help="Target experiment name under SECDA-DSE/llm_experiments",
    )
    p.add_argument(
        "-V",
        "--version",
        default="v1",
        help="Target experiment version folder name (default: v1)",
    )
    p.add_argument(
        "-b",
        "--target-board",
        choices=["KRIA", "Z1"],
        default="KRIA",
        help="Normalize hardware_gen.board and related fields",
    )
    p.add_argument(
        "-o",
        "--output-root",
        default=str(repo_root / "SECDA-DSE" / "llm_generated"),
        help="Output root for generated DSE runs/results",
    )
    p.add_argument(
        "-c",
        "--settings",
        default=str(default_settings),
        help="Path to dse_setting.json",
    )
    p.add_argument(
        "-s",
        "--sample",
        type=int,
        default=0,
        help="Optional deterministic sampling size passed to dse_run",
    )
    p.add_argument(
        "-f",
        "--flow",
        choices=["sim", "fpga", "lite", "sim_lite", "all"],
        default="sim_lite",
        help="dse_run flow preset (default: sim_lite)",
    )
    p.add_argument(
        "-k",
        "--keep-input",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Copy source into llm_experiments instead of moving (default: enabled)",
    )
    p.add_argument(
        "-F",
        "--force",
        action="store_true",
        help="Overwrite existing destination experiment folder",
    )
    p.add_argument(
        "-n",
        "--no-run",
        action="store_true",
        help="Only validate/normalize/place experiment; do not run DSE stages",
    )
    p.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Forward dry-run to dse_run.py stages",
    )
    p.add_argument(
        "-x",
        "--strict-sim",
        action="store_true",
        help="Fail immediately if dse_run fails when using a sim-containing flow",
    )
    p.add_argument("-m", "--monitor", action="store_true", help="Pass through to dse_run --monitor")
    p.add_argument("-I", "--monitor-interval", type=float, default=2.0, help="Pass through to dse_run --monitor-interval")
    p.add_argument("-r", "--resume", action="store_true", help="Pass through to dse_run --resume")
    p.add_argument("-P", "--project-status-filename", default="Project_Status.json", help="Pass through to dse_run --project-status-filename")
    p.add_argument("-R", "--run-status-filename", default="Run_Status.json", help="Pass through to dse_run --run-status-filename")
    p.add_argument(
        "-T",
        "--enable-timeouts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass through to dse_run timeout enable/disable",
    )
    p.add_argument("-B", "--timeout-sim-bin", type=int, default=900, help="Pass through to dse_run --timeout-sim-bin")
    p.add_argument("-U", "--timeout-sim-run", type=int, default=1800, help="Pass through to dse_run --timeout-sim-run")
    p.add_argument("-H", "--timeout-hls", type=int, default=3600, help="Pass through to dse_run --timeout-hls")
    p.add_argument("-X", "--timeout-hlx", type=int, default=10800, help="Pass through to dse_run --timeout-hlx")
    p.add_argument("-C", "--timeout-fpga-compile", type=int, default=1800, help="Pass through to dse_run --timeout-fpga-compile")
    p.add_argument("-E", "--timeout-fpga-exec", type=int, default=1800, help="Pass through to dse_run --timeout-fpga-exec")
    p.add_argument(
        "-a",
        "--dse-run-arg",
        action="append",
        default=[],
        help="Additional raw argument to forward to dse_run.py (repeatable)",
    )
    args = p.parse_args()

    input_exp = Path(args.input).resolve()
    if not input_exp.exists():
        raise FileNotFoundError(f"Input experiment folder not found: {input_exp}")

    ensure_structure(input_exp)

    exp_name = args.experiment_name.strip()
    version = args.version.strip()
    if not exp_name:
        raise ValueError("--experiment-name cannot be empty")
    if not re.fullmatch(r"v\d+", version):
        raise ValueError("--version must look like v1, v2, ...")

    llm_root = repo_root / "SECDA-DSE" / "llm_experiments"
    dest_exp = llm_root / exp_name / version
    dest_parent = dest_exp.parent

    if dest_exp.exists():
        if not args.force:
            raise FileExistsError(
                f"Destination already exists: {dest_exp}. Use --force to overwrite."
            )
        shutil.rmtree(dest_exp)

    dest_parent.mkdir(parents=True, exist_ok=True)

    if args.keep_input:
        shutil.copytree(input_exp, dest_exp)
    else:
        shutil.move(str(input_exp), str(dest_exp))

    normalize_hw_params(dest_exp / "hw_params.json", exp_name, version, args.target_board)
    touched = rewrite_paths(dest_exp, exp_name, version)

    settings_path = Path(args.settings).resolve()
    settings = load_json(settings_path)

    output_root = Path(args.output_root).resolve()

    if not args.no_run:
        run_dse_flow(
            repo_root=repo_root,
            experiment=dest_exp,
            output_root=output_root,
            settings_path=settings_path,
            sample=args.sample,
            dry_run=args.dry_run,
            flow=args.flow,
            strict_sim=args.strict_sim,
            monitor=args.monitor,
            monitor_interval=args.monitor_interval,
            resume=args.resume,
            project_status_filename=args.project_status_filename,
            run_status_filename=args.run_status_filename,
            enable_timeouts=args.enable_timeouts,
            timeout_sim_bin=args.timeout_sim_bin,
            timeout_sim_run=args.timeout_sim_run,
            timeout_hls=args.timeout_hls,
            timeout_hlx=args.timeout_hlx,
            timeout_fpga_compile=args.timeout_fpga_compile,
            timeout_fpga_exec=args.timeout_fpga_exec,
            dse_run_args=args.dse_run_arg,
        )

    exp_out = output_root / format_experiment_folder(dest_exp, settings["experiment_folder_format"])

    print("\nSECDA-DSE LLM ingestion complete")
    print(f"- Placed experiment: {dest_exp}")
    print(f"- Normalized board: {args.target_board}")
    print(f"- Files with path rewrites: {touched}")
    print(f"- DSE output root: {exp_out}")
    if not args.no_run:
        print(f"- HLS results aggregate: {exp_out / 'all_results'}")
        print(f"- HLS dataset: {exp_out / 'dataset'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
