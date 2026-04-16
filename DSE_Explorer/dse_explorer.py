#!/usr/bin/env python3
"""DSE Explorer

Generate experiment copies and per-run bash driver scripts from hw_params.json.
Supports new hw_params format with 'groups' and 'parameters'.
Groups are paired index-wise (no permutation inside a group).
Ungrouped parameters are cross-producted.

Usage:
    python3 DSE_Explorer/dse_explorer.py --experiment experiments/mm_exp/v1 --dry-run
        python3 DSE_Explorer/dse_explorer.py --experiment experiments/mm_exp/v1 --settings DSE_Explorer/dse_setting.json
"""

import sys
import os
from pathlib import Path
import argparse

# Ensure the DSE_Explorer directory is on sys.path so 'scripts' is importable
# regardless of the working directory the caller uses.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.settings import load_settings, format_experiment_folder
from scripts.bazel_utils import rewrite_build_deps_for_runs
from scripts.script_gen import generate_runs


def main():

    # Remember the caller's working directory so relative CLI paths are resolved
    # against it, even after we os.chdir into the DSE_Explorer directory.
    orig_cwd = Path.cwd().resolve()

    abspath = os.path.abspath(__file__)
    dname = os.path.dirname(abspath)
    os.chdir(dname)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--settings', default=None, help='Path to dse_setting.json (optional)')
    known, _ = pre.parse_known_args()

    default_settings_path = Path(__file__).resolve().parent / "dse_setting.json"
    settings_path = Path(known.settings) if known.settings else default_settings_path
    # Resolve --settings relative to original cwd if it was user-provided
    if known.settings:
        settings_path = (orig_cwd / settings_path).resolve()
    settings = load_settings(settings_path)

    p = argparse.ArgumentParser(parents=[pre])
    p.add_argument('--experiment', '-e', required=True,
                   help='Path to the experiment directory, e.g. experiments/mm_exp/v1')
    p.add_argument('--hw', '-j', default=None, help='Path to hw_params.json (optional)')
    p.add_argument('--output', '-o', default=settings["output_root"], help='Output directory for generated experiments')
    p.add_argument('--dry-run', action='store_true', help='Do not copy files; only print actions')
    p.add_argument('--sample', '-s', type=int, default=0, help='If >0, limit to this many runs (deterministic sampling)')
    args = p.parse_args()

    # Resolve user-provided relative paths against the original working directory
    source_exp = Path(args.experiment)
    if not source_exp.is_absolute():
        source_exp = (orig_cwd / source_exp).resolve()
    if not source_exp.exists():
        raise FileNotFoundError(f"Experiment path not found: {source_exp}")

    hw_params_name = settings["hw_params_filename"]
    hw_json = Path(args.hw) if args.hw else source_exp / hw_params_name
    if args.hw and not hw_json.is_absolute():
        hw_json = (orig_cwd / hw_json).resolve()
    if not hw_json.exists():
        # try recursive find
        found = list(source_exp.rglob(hw_params_name))
        if not found:
            raise FileNotFoundError(f"{hw_params_name} not found under {source_exp}")
        hw_json = found[0]

    exp_folder = format_experiment_folder(source_exp, settings["experiment_folder_format"])
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (orig_cwd / out_path).resolve()
    out_root = (out_path / exp_folder).resolve()
    generate_runs(source_exp, hw_json, out_root, settings, dry_run=args.dry_run, sample=args.sample)
    if not args.dry_run:
        rewrite_build_deps_for_runs(out_root, source_exp, settings)


if __name__ == '__main__':
    main()
