from pathlib import Path
import itertools
import shutil
import csv
import math
import json

from .bazel_utils import find_repo_root, update_build_files
from .hw_params import load_hw_params, build_group_choices
from .param_replace import replace_params_in_file
from .shell_writers import (
    write_batch_script,
    write_collect_dataset_script,
    write_collect_results_script,
    write_hw_gen_script,
    write_remote_run_script,
    write_sim_run_script,
)


def _build_source_rel_candidates(source_exp: Path, repo_root: Path | None) -> list[str]:
    candidates = [f"experiments/{source_exp.parent.name}/{source_exp.name}"]
    if repo_root:
        try:
            candidates.insert(0, source_exp.relative_to(repo_root).as_posix())
        except ValueError:
            pass
    return list(dict.fromkeys(candidates))


def _sample_combinations(combos: list[tuple[dict, ...]], sample: int) -> list[tuple[dict, ...]]:
    total = len(combos)
    if not sample or sample >= total:
        return combos
    step = total / sample
    indices = []
    for i in range(sample):
        idx = int(math.floor(i * step))
        indices.append(min(max(idx, 0), total - 1))
    return [combos[i] for i in sorted(dict.fromkeys(indices))]


def _rewrite_run_build_files(dest_dir: Path, build_repo_root: Path, source_exp: Path, source_rel_candidates: list[str]) -> None:
    for source_rel in source_rel_candidates:
        try:
            update_build_files(dest_dir, build_repo_root, source_rel)
        except ValueError:
            continue
    try:
        run_rel = dest_dir.relative_to(build_repo_root).as_posix()
        hard_old = f"//experiments/{source_exp.parent.name}/{source_exp.name}"
        hard_new = f"//{run_rel}"
        for build_path in list(dest_dir.rglob("BUILD")) + list(dest_dir.rglob("BUILD.bazel")):
            text = build_path.read_text()
            updated = text.replace(hard_old + "/", hard_new + "/").replace(hard_old + ":", hard_new + ":")
            if updated != text:
                build_path.write_text(updated)
    except ValueError:
        pass


def _prepare_run_directory(dest_dir: Path, source_exp: Path, mapping: dict, repo_root: Path | None, source_rel_candidates: list[str], settings: dict) -> None:
    # Create destination directory if it doesn't exist
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Copy all files from source_exp, overwriting existing files without deleting the directory
    shutil.copytree(source_exp, dest_dir, dirs_exist_ok=True)

    build_repo_root = repo_root or find_repo_root(dest_dir, settings["repo_root_marker"])
    if build_repo_root:
        _rewrite_run_build_files(dest_dir, build_repo_root, source_exp, source_rel_candidates)

    acc_files = list(dest_dir.rglob("acc_config.sc.h")) or list(dest_dir.rglob("acc_config.h"))
    if not acc_files:
        print(f"WARNING: no acc_config file found in {dest_dir}")
        return
    replace_params_in_file(acc_files[0], mapping)


def _write_run_scripts(dest_dir: Path, settings: dict, run_id: str, run_name: str) -> None:
    run_sh = dest_dir / settings["hw_gen_script_template"].format(run_id=run_id)
    run_remote_sh = dest_dir / settings["run_script_template"].format(run_id=run_id)
    run_sim_sh = dest_dir / settings["sim_script_template"].format(run_id=run_id)
    failure_logs = list(dict.fromkeys(settings["hw_gen_logs"] + ["vivado_hls.log", "vivado.log"]))
    write_hw_gen_script(run_sh, settings, run_id, run_name, failure_logs)
    write_remote_run_script(run_remote_sh, settings, run_id)
    write_sim_run_script(run_sim_sh, settings, run_id)


def _rewrite_all_build_files(out_root: Path, repo_root: Path | None, source_rel_candidates: list[str], settings: dict) -> None:
    build_repo_root = repo_root or find_repo_root(out_root, settings["repo_root_marker"])
    if not build_repo_root:
        return
    for run_dir in out_root.iterdir():
        if not run_dir.is_dir():
            continue
        for source_rel in source_rel_candidates:
            try:
                update_build_files(run_dir, build_repo_root, source_rel)
            except ValueError:
                continue


def _write_top_level_scripts(out_root: Path, settings: dict, dry_run: bool) -> None:
    global_hw = out_root / settings["hw_gen_all_name"]
    global_run = out_root / settings["run_all_name"]
    global_sim = out_root / settings["sim_all_name"]
    collect_results = out_root / settings["collect_results_name"]
    collect_dataset = out_root / settings["collect_dataset_name"]

    if dry_run:
        print(f"DRY-RUN: would create {global_hw}")
        print(f"DRY-RUN: would create {global_run}")
        print(f"DRY-RUN: would create {global_sim}")
        print(f"DRY-RUN: would create {collect_results}")
        print(f"DRY-RUN: would create {collect_dataset}")
        return

    write_batch_script(
        global_hw,
        settings["hw_gen_glob"],
        f"No {settings['hw_gen_glob']} scripts found",
        "Running all hardware generation scripts",
    )
    write_batch_script(
        global_run,
        settings["run_glob"],
        f"No {settings['run_glob']} scripts found",
        "Running all remote scripts",
    )
    write_batch_script(
        global_sim,
        settings["sim_glob"],
        f"No {settings['sim_glob']} scripts found",
        "Running all simulation scripts",
    )
    write_collect_results_script(collect_results, settings)
    write_collect_dataset_script(collect_dataset, settings)


def _print_summary(out_root: Path, settings: dict) -> None:
    global_hw = out_root / settings["hw_gen_all_name"]
    global_run = out_root / settings["run_all_name"]
    global_sim = out_root / settings["sim_all_name"]
    collect_results = out_root / settings["collect_results_name"]
    collect_dataset = out_root / settings["collect_dataset_name"]
    cmd_hw = f'cd "{out_root}" && ./{settings["hw_gen_all_name"]}'
    cmd_run = f'cd "{out_root}" && ./{settings["run_all_name"]}'
    cmd_sim = f'cd "{out_root}" && ./{settings["sim_all_name"]}'
    cmd_collect = f'cd "{out_root}" && ./{settings["collect_results_name"]}'
    cmd_dataset = f'cd "{out_root}" && ./{settings["collect_dataset_name"]}'
    print("Commands to run all hardware generation, remote runs, and simulations:")
    print(cmd_hw)
    print(cmd_run)
    print(cmd_sim)
    print(cmd_collect)
    print(cmd_dataset)
    print(f"{global_hw}")
    print(f"{global_run}")
    print(f"{global_sim}")
    print(f"{collect_results}")
    print(f"{collect_dataset}")
    print("Done")


def generate_runs(source_exp: Path, hw_json: Path, out_root: Path, settings: dict, dry_run: bool = False, sample: int = 0):
    repo_root = find_repo_root(source_exp, settings["repo_root_marker"])
    source_rel_candidates = _build_source_rel_candidates(source_exp, repo_root)
    params_map, groups = load_hw_params(hw_json)
    group_choices = build_group_choices(params_map, groups)

    combos = list(itertools.product(*group_choices))
    total = len(combos)
    if sample and 0 < sample < total:
        combos = _sample_combinations(combos, sample)
        print(f"Sampling {len(combos)} of {total} run(s)")
    else:
        print(f"Found {total} run(s) to generate")

    # Build CSV
    out_root.mkdir(parents=True, exist_ok=True)
    csv_path = out_root / settings["runs_csv"]
    fieldnames = ["run_id", "run_name", "params", "source_experiment"]

    with csv_path.open('w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for idx, combo in enumerate(combos, start=1):
            mapping = {}
            for d in combo:
                mapping.update(d)
            run_uid = f"{idx:04d}"
            run_name = run_uid
            run_id = run_uid
            dest_dir = out_root / run_name

            if dry_run:
                print(f"DRY-RUN: would create {dest_dir}")
            else:
                _prepare_run_directory(dest_dir, source_exp, mapping, repo_root, source_rel_candidates, settings)
                _write_run_scripts(dest_dir, settings, run_id, run_name)

            writer.writerow({
                "run_id": run_id,
                "run_name": run_name,
                "params": json.dumps(mapping),
                "source_experiment": str(source_exp)
            })

    print(f"Wrote runs.csv -> {csv_path}")

    if not dry_run:
        _rewrite_all_build_files(out_root, repo_root, source_rel_candidates, settings)

    _write_top_level_scripts(out_root, settings, dry_run)
    _print_summary(out_root, settings)
