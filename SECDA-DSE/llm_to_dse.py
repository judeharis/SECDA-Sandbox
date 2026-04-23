#!/usr/bin/env python3
"""Prepare LLM-generated experiments for DSE_Explorer and run HLS+SIM flow.

Workflow:
1) Validate experiment folder structure.
2) Move/copy into SECDA-DSE/llm_experiments/<exp_name>/<version>.
3) Normalize hw_params.json and rewrite stale experiment paths in text files.
4) Run dse_run.py stages: generate -> hls -> sim -> collect.
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
    strict_sim: bool,
) -> None:
    dse_run = repo_root / "dse_run.py"
    common = [
        sys.executable,
        str(dse_run),
        "--experiment",
        str(experiment),
        "--settings",
        str(settings_path),
        "--output",
        str(output_root),
    ]

    cmd_generate = common + ["--stage", "generate"]
    if sample > 0:
        cmd_generate += ["--sample", str(sample)]
    if dry_run:
        cmd_generate += ["--dry-run"]
    run_cmd(cmd_generate, repo_root)

    if dry_run:
        print(
            "Dry-run mode: skipping hls and collect stages because generate dry-run "
            "does not create runnable scripts."
        )
        return

    cmd_hls = common + ["--stage", "hls"]
    run_cmd(cmd_hls, repo_root)

    cmd_sim = common + ["--stage", "sim"]
    try:
        run_cmd(cmd_sim, repo_root)
    except subprocess.CalledProcessError:
        if strict_sim:
            raise
        print(
            "WARNING: Simulation stage failed for one or more runs. "
            "Continuing to collect available outputs. "
            "Use --strict-sim to fail fast on simulation errors."
        )

    cmd_collect = common + ["--stage", "collect"]
    run_cmd(cmd_collect, repo_root)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_settings = repo_root / "DSE_Explorer" / "dse_setting.json"

    p = argparse.ArgumentParser(description="Prepare LLM experiments and run DSE HLS+SIM flow")
    p.add_argument(
        "--input",
        default=str(repo_root / "SECDA-DSE" / "input_space" / "v1"),
        help="Path to source LLM experiment folder",
    )
    p.add_argument(
        "--experiment-name",
        required=True,
        help="Target experiment name under SECDA-DSE/llm_experiments",
    )
    p.add_argument(
        "--version",
        default="v1",
        help="Target experiment version folder name (default: v1)",
    )
    p.add_argument(
        "--target-board",
        choices=["KRIA", "Z1"],
        default="KRIA",
        help="Normalize hardware_gen.board and related fields",
    )
    p.add_argument(
        "--output-root",
        default=str(repo_root / "SECDA-DSE" / "llm_generated"),
        help="Output root for generated DSE runs/results",
    )
    p.add_argument(
        "--settings",
        default=str(default_settings),
        help="Path to dse_setting.json",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Optional deterministic sampling size for generate stage",
    )
    p.add_argument(
        "--keep-input",
        action="store_true",
        help="Copy source into llm_experiments instead of moving",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing destination experiment folder",
    )
    p.add_argument(
        "--no-run",
        action="store_true",
        help="Only validate/normalize/place experiment; do not run DSE stages",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Forward dry-run to dse_run.py stages",
    )
    p.add_argument(
        "--strict-sim",
        action="store_true",
        help="Fail immediately if simulation stage fails",
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
            strict_sim=args.strict_sim,
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
